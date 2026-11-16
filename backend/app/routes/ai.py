from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from backend.app.db.mongo import get_database
from backend.app.models.domain import new_case_debrief_document
from backend.app.services.ai import DeepSeekClient
from backend.app.services.i18n import resolve_request_lang
from backend.app.services.model_router import (
    ModelRoutingError,
    focus_directive,
    normalize_focus,
    route_for_context,
)
from backend.app.services.planner import KnowledgeNodeNotFound, build_planner
from backend.app.services.rag import JinaEmbeddingError, ground_citations, retrieve_knowledge
from backend.app.services.tree import to_public_document


router = APIRouter(prefix="/api/ai", tags=["ai"])


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    node_key: str | None = None
    case_id: str | None = None
    use_retrieval: bool = True
    retrieval_top_k: int = Field(default=6, ge=1, le=10)


class ClinicalCopilotRequest(BaseModel):
    node_key: str = Field(min_length=1, max_length=240)
    case_id: str | None = None
    focus: Literal["setup", "signal_change", "evidence", "custom"] = "setup"
    observation: str | None = Field(default=None, max_length=2000)
    phase: str | None = Field(default=None, max_length=80)
    use_retrieval: bool = True
    retrieval_top_k: int = Field(default=6, ge=1, le=10)


class CaseDebriefRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=120)
    node_key: str | None = Field(default=None, max_length=240)
    focus: Literal["post_case", "timeline", "learning"] = "post_case"
    additional_question: str | None = Field(default=None, max_length=2000)
    use_retrieval: bool = True
    retrieval_top_k: int = Field(default=6, ge=1, le=10)


class UnifiedCopilotRequest(BaseModel):
    focus: Literal["setup", "signal", "evidence"] = "setup"
    node_key: str | None = Field(default=None, max_length=240)
    case_id: str | None = None
    observation: str | None = Field(default=None, max_length=2000)
    phase: str | None = Field(default=None, max_length=80)
    use_retrieval: bool = True
    retrieval_top_k: int = Field(default=6, ge=1, le=10)


@router.post("/ask")
async def ask_ai(payload: AskRequest, request: Request, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    context = await build_ai_context(database, payload.node_key, payload.case_id)
    retrieval = await maybe_retrieve_knowledge(database, payload.question, context, payload.use_retrieval, payload.retrieval_top_k)

    result = await DeepSeekClient().ask(payload.question, context, language=resolve_request_lang(request))
    result["retrieval"] = public_retrieval(retrieval)
    return result


@router.post("/clinical-copilot")
async def clinical_copilot(payload: ClinicalCopilotRequest, request: Request, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    context = await build_ai_context(database, payload.node_key, payload.case_id)
    task = build_clinical_copilot_task(payload)
    retrieval = await maybe_retrieve_knowledge(database, task, context, payload.use_retrieval, payload.retrieval_top_k)
    try:
        result = await DeepSeekClient().ask_structured_json(task, context, language=resolve_request_lang(request))
        copilot = ground_copilot(normalize_clinical_copilot(result["data"]), retrieval)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    return {
        "model": result["model"],
        "focus": payload.focus,
        "node_key": payload.node_key,
        "case_id": payload.case_id,
        "copilot": copilot,
        "retrieval": public_retrieval(retrieval),
        "raw_usage": result.get("raw_usage"),
    }


@router.post("/copilot")
async def unified_copilot(payload: UnifiedCopilotRequest, request: Request, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    """Unified clinical copilot (#13).

    One entry point with three focus modes (setup / signal / evidence). The model
    router keeps any PHI-bearing request on the in-region EU model when one is
    configured, and otherwise uses the external API. The result stays advisory —
    it proposes, the clinician confirms and seals.
    """
    focus = normalize_focus(payload.focus)
    context = await build_ai_context(database, payload.node_key, payload.case_id)
    task = build_unified_copilot_task(payload, focus)
    retrieval = await maybe_retrieve_knowledge(database, task, context, payload.use_retrieval, payload.retrieval_top_k)
    try:
        route = route_for_context(context)
    except ModelRoutingError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    try:
        result = await DeepSeekClient(route=route).ask_structured_json(
            task,
            context,
            language=resolve_request_lang(request),
            focus_directive=focus_directive(focus),
        )
        copilot = ground_copilot(normalize_clinical_copilot(result["data"]), retrieval)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    return {
        "model": result["model"],
        "provider": result.get("provider", route.provider),
        "focus": focus,
        "route": route.public(),
        "node_key": payload.node_key,
        "case_id": payload.case_id,
        "copilot": copilot,
        "retrieval": public_retrieval(retrieval),
        "raw_usage": result.get("raw_usage"),
    }


@router.post("/case-debrief")
async def case_debrief(payload: CaseDebriefRequest, request: Request, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    context = await build_ai_context(database, payload.node_key, payload.case_id)
    task = build_case_debrief_task(payload, context)
    retrieval = await maybe_retrieve_knowledge(database, task, context, payload.use_retrieval, payload.retrieval_top_k)
    try:
        result = await DeepSeekClient().ask_case_debrief_json(task, context, language=resolve_request_lang(request))
        debrief = ground_copilot(normalize_case_debrief(result["data"]), retrieval)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    public_evidence = public_retrieval(retrieval)
    document = new_case_debrief_document(
        payload.case_id,
        focus=payload.focus,
        node_key=payload.node_key,
        model=result["model"],
        debrief=debrief,
        retrieval=public_evidence,
        raw_usage=result.get("raw_usage"),
        timeline_event_count=len(context.get("case_events") or []),
    )
    await database.case_debriefs.insert_one(document)
    await database.cases.update_one(
        {"case_id": payload.case_id},
        {"$set": {"updated_at": document["created_at"], "last_debrief_at": document["created_at"]}, "$inc": {"debrief_count": 1}},
    )
    return {
        "model": result["model"],
        "focus": payload.focus,
        "node_key": payload.node_key,
        "case_id": payload.case_id,
        "debrief": debrief,
        "retrieval": public_evidence,
        "saved_debrief": to_public_document(document),
        "raw_usage": result.get("raw_usage"),
    }


async def build_ai_context(database: AsyncIOMotorDatabase, node_key: str | None, case_id: str | None) -> dict[str, Any]:
    context: dict[str, Any] = {"node": None, "case": None, "protocols": [], "muscles": [], "retrieved_knowledge": []}
    if node_key:
        try:
            planner = await build_planner(database, node_key)
        except KnowledgeNodeNotFound:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
        context["node"] = planner["node"]
        context["muscles"] = planner.get("muscles", [])
        context["protocols"] = planner.get("protocols", [])
        context["clinical_plan"] = planner.get("clinical_plan")
        context["pins"] = planner.get("pins", [])

    if case_id:
        case = await database.cases.find_one({"case_id": case_id})
        if not case:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
        events = await database.case_events.find({"case_id": case_id}).sort("created_at", 1).to_list(length=50)
        context["case"] = to_public_document(case)
        context["case_events"] = [to_public_document(event) for event in events]
    return context


async def maybe_retrieve_knowledge(database: AsyncIOMotorDatabase, question: str, context: dict[str, Any], enabled: bool, top_k: int) -> dict[str, Any]:
    retrieval: dict[str, Any] = {"status": "disabled", "items": []}
    if not enabled:
        return retrieval
    try:
        retrieval_query = build_retrieval_query(question, context)
        retrieval = await retrieve_knowledge(database, retrieval_query, top_k=top_k)
    except JinaEmbeddingError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    context["retrieved_knowledge"] = retrieval.get("items", [])
    return retrieval


def public_retrieval(retrieval: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": retrieval.get("status"),
        "model": retrieval.get("model"),
        "retrieval_strategy": retrieval.get("retrieval_strategy"),
        "rerank_model": retrieval.get("rerank_model"),
        "items": [
            {
                "score": item.get("score"),
                "relevance": item.get("relevance"),
                "rerank_score": item.get("rerank_score"),
                "source_collection": item.get("source_collection"),
                "source_key": item.get("source_key"),
                "title": item.get("title"),
                "source": item.get("source"),
            }
            for item in retrieval.get("items", [])
        ],
    }


def ground_copilot(copilot: dict[str, Any], retrieval: dict[str, Any]) -> dict[str, Any]:
    """Attach citation provenance to a copilot/debrief payload.

    Every citation the model lists in ``evidence_used`` is checked against the
    sources that were actually retrieved. Grounded citations gain their source
    keys; ungrounded ones are flagged so the clinician sees when the model
    referenced something outside the retrieved evidence. This enforces the
    no-invented-citations rule without altering any deterministic numbers.
    """
    grounding = ground_citations(copilot.get("evidence_used", []), retrieval.get("items", []))
    copilot["evidence_used"] = grounding["citations"]
    copilot["grounding"] = {
        "grounded_count": grounding["grounded_count"],
        "ungrounded_count": grounding["ungrounded_count"],
        "fully_grounded": grounding["fully_grounded"],
    }
    return copilot


def build_clinical_copilot_task(payload: ClinicalCopilotRequest) -> str:
    focus_prompts = {
        "setup": "Create a pre-baseline IONM setup brief: immediate technical/anatomical checks, readiness gaps, what to watch, and missing data.",
        "signal_change": "Create an acute signal-change response brief: first checks, differential between technical/anesthetic/surgical causes, surgeon communication, and escalation cues.",
        "evidence": "Explain the evidence-backed rationale for this monitoring setup: selected muscles, roots or cranial nerves, protocols, gaps, and source limitations.",
        "custom": "Answer the clinician observation as a structured IONM copilot brief.",
    }
    rows = [focus_prompts[payload.focus]]
    if payload.phase:
        rows.append(f"Current phase: {payload.phase}")
    if payload.observation:
        rows.append(f"Clinician observation: {payload.observation}")
    rows.append("Keep the answer concise, actionable, and clinically cautious.")
    return "\n".join(rows)


def build_unified_copilot_task(payload: UnifiedCopilotRequest, focus: str) -> str:
    focus_prompts = {
        "setup": "Create a pre-baseline IONM setup brief: immediate technical/anatomical checks, readiness gaps, what to watch, and missing data.",
        "signal": "Create an acute signal-interpretation brief: first checks, the differential between technical/anesthetic/surgical causes, surgeon communication, and escalation cues. Use only thresholds already computed by the deterministic engine.",
        "evidence": "Explain the evidence-backed rationale for this monitoring decision: weigh the retrieved sources, state how strongly each applies, and flag missing data.",
    }
    rows = [focus_prompts[focus]]
    if payload.phase:
        rows.append(f"Current phase: {payload.phase}")
    if payload.observation:
        rows.append(f"Clinician observation: {payload.observation}")
    rows.append("Keep the answer concise, actionable, and clinically cautious. You are advisory only — the clinician confirms and seals every decision.")
    return "\n".join(rows)


def build_case_debrief_task(payload: CaseDebriefRequest, context: dict[str, Any]) -> str:
    focus_prompts = {
        "post_case": "Create a post-case IONM debrief: concise case summary, timeline interpretation, decision points, documentation gaps, follow-up and learning points.",
        "timeline": "Create a chronological IONM case replay: interpret each recorded event, identify risk moments and state what was documented versus missing.",
        "learning": "Create an Academy-focused debrief: convert the case timeline into learning points, practice prompts and review targets.",
    }
    events = context.get("case_events") or []
    case = context.get("case") or {}
    rows = [
        focus_prompts[payload.focus],
        f"Case title: {case.get('title') or 'unknown'}",
        f"Timeline event count: {len(events)}",
    ]
    if not events:
        rows.append("No timeline events are documented; explicitly state that the debrief is limited by missing timeline data.")
    if payload.additional_question:
        rows.append(f"Additional clinician question: {payload.additional_question}")
    rows.append("Keep the debrief clinically cautious, concise, and useful for both documentation and Academy review.")
    return "\n".join(rows)


def normalize_clinical_copilot(data: dict[str, Any]) -> dict[str, Any]:
    required = ["summary", "confidence", "next_checks", "surgeon_communication", "evidence_used", "missing_data", "watch_items", "clinical_caution"]
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"AI copilot response is missing required fields: {', '.join(missing)}")
    confidence = data["confidence"]
    if confidence not in {"low", "medium", "high"}:
        raise ValueError("AI copilot confidence must be low, medium, or high.")
    return {
        "summary": require_string(data, "summary"),
        "confidence": confidence,
        "next_checks": normalize_object_list(data, "next_checks", ["priority", "action", "rationale"]),
        "surgeon_communication": normalize_object_list(data, "surgeon_communication", ["when", "message"]),
        "evidence_used": normalize_object_list(data, "evidence_used", ["title", "why_it_matters"]),
        "missing_data": normalize_string_list(data, "missing_data"),
        "watch_items": normalize_string_list(data, "watch_items"),
        "clinical_caution": require_string(data, "clinical_caution"),
    }


def normalize_case_debrief(data: dict[str, Any]) -> dict[str, Any]:
    required = [
        "case_summary",
        "confidence",
        "timeline_replay",
        "decision_points",
        "documentation_gaps",
        "academy_learning_points",
        "evidence_used",
        "follow_up",
        "clinical_caution",
    ]
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"AI case debrief response is missing required fields: {', '.join(missing)}")
    confidence = data["confidence"]
    if confidence not in {"low", "medium", "high"}:
        raise ValueError("AI case debrief confidence must be low, medium, or high.")
    timeline = normalize_object_list(data, "timeline_replay", ["event", "interpretation", "risk_level"])
    invalid_risks = [item["risk_level"] for item in timeline if item["risk_level"] not in {"low", "medium", "high", "unknown"}]
    if invalid_risks:
        raise ValueError("AI case debrief risk_level must be low, medium, high, or unknown.")
    return {
        "case_summary": require_string(data, "case_summary"),
        "confidence": confidence,
        "timeline_replay": timeline,
        "decision_points": normalize_object_list(data, "decision_points", ["moment", "recommended_action", "rationale"]),
        "documentation_gaps": normalize_string_list(data, "documentation_gaps"),
        "academy_learning_points": normalize_object_list(data, "academy_learning_points", ["topic", "why_it_matters", "practice_prompt"]),
        "evidence_used": normalize_object_list(data, "evidence_used", ["title", "why_it_matters"]),
        "follow_up": normalize_string_list(data, "follow_up"),
        "clinical_caution": require_string(data, "clinical_caution"),
    }


def require_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"AI copilot field '{key}' must be a non-empty string.")
    return value.strip()


def normalize_string_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"AI copilot field '{key}' must be a list.")
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def normalize_object_list(data: dict[str, Any], key: str, fields: list[str]) -> list[dict[str, str]]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"AI copilot field '{key}' must be a list.")
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"AI copilot field '{key}' must contain objects.")
        normalized.append({field: require_string(item, field) for field in fields})
    return normalized


def build_retrieval_query(question: str, context: dict[str, Any]) -> str:
    node = context.get("node") or {}
    muscles = context.get("muscles") or []
    rows = [question]
    if node:
        rows.extend(
            [
                str(node.get("title_el") or ""),
                str(node.get("summary_el") or ""),
                f"domain: {node.get('domain') or ''}",
                f"node_type: {node.get('node_type') or ''}",
                f"linked_roots: {' · '.join(node.get('linked_roots') or [])}",
                f"linked_cns: {' · '.join(node.get('linked_cns') or [])}",
                f"linked_muscles: {' · '.join(node.get('linked_muscles') or [])}",
                f"linked_protocols: {' · '.join(node.get('linked_protocols') or [])}",
            ]
        )
    if muscles:
        rows.append("muscle placements: " + " · ".join(
            f"{muscle.get('lat_name')}: {muscle.get('placement') or ''} / {muscle.get('action') or ''}"
            for muscle in muscles
        ))
    protocols = context.get("protocols") or []
    if protocols:
        rows.append("resolved protocols: " + " · ".join(
            f"{protocol.get('key')}: {protocol.get('title_el') or ''} ({protocol.get('link_type') or 'direct'})"
            for protocol in protocols
        ))
    case_events = context.get("case_events") or []
    if case_events:
        rows.append("case timeline: " + " · ".join(
            f"{event.get('event_type') or ''}: {event.get('title') or ''} {event.get('description') or ''}".strip()
            for event in case_events[-12:]
        ))
    return "\n".join(row for row in rows if row.strip())
