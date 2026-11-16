from copy import deepcopy
from math import sqrt
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from backend.app.db.mongo import get_database
from backend.app.services.academy_content import build_lesson_package, grade_case_scenario, grade_checkpoint_submission
from backend.app.services.academy_progress import (
    LEARNER_COOKIE_NAME,
    get_learner_profile,
    get_or_create_progress,
    learner_report,
    list_learner_profiles,
    record_attempt,
    require_learner,
    update_progress,
)
from backend.app.services.ai import DeepSeekClient
from backend.app.services.i18n import resolve_request_lang
from backend.app.services.planner import KnowledgeNodeNotFound, build_planner, resolve_protocols
from backend.app.services.pubmed import get_pubmed_bibliography
from backend.app.services.rag import JinaEmbeddingError, retrieve_knowledge
from backend.app.services.tree import to_public_document
from backend.app.services.waveforms import build_waveform_lab, grade_waveform_quiz


router = APIRouter(prefix="/api/academy", tags=["academy"])


class PlacementAttempt(BaseModel):
    target_id: str = Field(min_length=1)
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)


class SimulationValidationRequest(BaseModel):
    node_key: str = Field(min_length=1)
    placements: list[PlacementAttempt] = Field(default_factory=list)
    tolerance: float = Field(default=4.0, ge=1.0, le=12.0)


class CheckpointAnswer(BaseModel):
    checkpoint_id: str = Field(min_length=1)
    selected_values: list[str] = Field(default_factory=list)


class CheckpointGradeRequest(BaseModel):
    node_key: str = Field(min_length=1)
    answers: list[CheckpointAnswer] = Field(default_factory=list)


class LearnerSelectRequest(BaseModel):
    learner_id: str = Field(min_length=1)


class ProgressPatchRequest(BaseModel):
    checklist: dict[str, bool] | None = None
    placement_validation: dict[str, Any] | None = None
    placement_passed: bool | None = None
    placement_accuracy: int | None = Field(default=None, ge=0, le=100)
    waveform_reviewed: bool | None = None
    waveform_scenario: str | None = None
    waveform_parameters: dict[str, float] | None = None


class CaseScenarioGradeRequest(BaseModel):
    node_key: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    selected_actions: list[str] = Field(default_factory=list)


class WaveformQuizGradeRequest(BaseModel):
    node_key: str = Field(min_length=1)
    parameters: dict[str, float] | None = None
    answers: list[CheckpointAnswer] = Field(default_factory=list)


class TutorAskRequest(BaseModel):
    node_key: str = Field(min_length=1)
    question: str = Field(min_length=3, max_length=4000)
    placement_validation: dict[str, Any] | None = None
    waveform_scenario: str | None = None
    case_grade: dict[str, Any] | None = None
    retrieval_top_k: int = Field(default=8, ge=1, le=10)


@router.get("/learners")
async def get_learners(database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    learners = await list_learner_profiles(database)
    return {"items": learners, "count": len(learners)}


@router.post("/learners/select")
async def select_learner(
    payload: LearnerSelectRequest,
    response: Response,
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    learner = await get_learner_profile(database, payload.learner_id)
    if not learner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learner profile not found")
    response.set_cookie(LEARNER_COOKIE_NAME, learner["learner_id"], httponly=True, secure=False, samesite="lax", max_age=60 * 60 * 24 * 90, path="/")
    return {"learner": learner}


@router.get("/learners/session")
async def learner_session(request: Request, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    learner = await get_learner_profile(database, request.cookies.get(LEARNER_COOKIE_NAME))
    return {"learner": learner, "authenticated": bool(learner)}


@router.get("/progress/report")
async def get_progress_report(request: Request, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    try:
        learner = await require_learner(database, request.cookies.get(LEARNER_COOKIE_NAME))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_428_PRECONDITION_REQUIRED, detail=str(error)) from error
    return await learner_report(database, learner["learner_id"])


@router.get("/progress/lessons/{node_key:path}")
async def get_lesson_progress(
    node_key: str,
    request: Request,
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        learner = await require_learner(database, request.cookies.get(LEARNER_COOKIE_NAME))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_428_PRECONDITION_REQUIRED, detail=str(error)) from error
    try:
        planner = await build_planner(database, node_key)
    except KnowledgeNodeNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
    lesson = await build_lesson_package(database, planner)
    progress = await get_or_create_progress(database, learner["learner_id"], node_key, lesson)
    return {"learner": learner, "progress": progress}


@router.patch("/progress/lessons/{node_key:path}")
async def patch_lesson_progress(
    node_key: str,
    payload: ProgressPatchRequest,
    request: Request,
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        learner = await require_learner(database, request.cookies.get(LEARNER_COOKIE_NAME))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_428_PRECONDITION_REQUIRED, detail=str(error)) from error
    try:
        planner = await build_planner(database, node_key)
    except KnowledgeNodeNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
    lesson = await build_lesson_package(database, planner)
    progress = await update_progress(database, learner["learner_id"], node_key, payload.model_dump(exclude_none=True), lesson)
    return {"learner": learner, "progress": progress}


@router.get("/curriculum")
async def get_curriculum(database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    modules = await database.academy_modules.find({}).sort("sort_order", 1).to_list(length=None)
    nodes = await database.knowledge_nodes.find({"context": "academy", "status": "active"}).sort("sort_order", 1).to_list(length=None)
    simulations = await build_simulation_library(database, nodes)

    simulation_keys = {item["node_key"] for item in simulations}
    module_payload = []
    for module in modules:
        module_key = module.get("module_key")
        domain_hint = academy_domain_hint(module_key)
        lesson_nodes = [node for node in nodes if node_matches_module(node, module_key, domain_hint)]
        module_payload.append(
            {
                "module_key": module_key,
                "title": module.get("title"),
                "status": module.get("status"),
                "lesson_count": len(lesson_nodes),
                "simulation_count": sum(1 for node in lesson_nodes if node.get("key") in simulation_keys),
                "lessons": [lesson_summary(node, node.get("key") in simulation_keys) for node in lesson_nodes[:24]],
            }
        )

    return {
        "modules": module_payload,
        "simulation_library": simulations,
        "coverage": {
            "modules": len(module_payload),
            "academy_nodes": len(nodes),
            "simulation_ready_nodes": len(simulations),
        },
    }


@router.get("/simulations")
async def get_simulations(database: AsyncIOMotorDatabase = Depends(get_database)) -> list[dict[str, Any]]:
    nodes = await database.knowledge_nodes.find({"context": "academy", "status": "active"}).sort("sort_order", 1).to_list(length=None)
    return await build_simulation_library(database, nodes)


@router.get("/lessons/{node_key:path}")
async def get_lesson(node_key: str, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    try:
        planner = await build_planner(database, node_key)
    except KnowledgeNodeNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
    return await build_lesson_package(database, planner)


@router.get("/simulations/{node_key:path}")
async def get_simulation(node_key: str, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    try:
        planner = await build_planner(database, node_key)
    except KnowledgeNodeNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
    if not planner.get("pins"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation is not available for this node")
    return simulation_payload(planner)


@router.get("/waveforms/{node_key:path}")
async def get_waveforms(
    node_key: str,
    amplitude_drop_percent: float = Query(default=55, ge=10, le=95),
    latency_shift_ms: float = Query(default=3.0, ge=0, le=12),
    threshold_ma: float = Query(default=7.0, ge=1, le=30),
    noise_scale: float = Query(default=1.25, ge=0.2, le=3),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        planner = await build_planner(database, node_key)
    except KnowledgeNodeNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
    return build_waveform_lab(
        planner,
        {
            "amplitude_drop_percent": amplitude_drop_percent,
            "latency_shift_ms": latency_shift_ms,
            "threshold_ma": threshold_ma,
            "noise_scale": noise_scale,
        },
    )


@router.get("/bibliography/{node_key:path}")
async def get_bibliography(node_key: str, limit: int = 8, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    try:
        planner = await build_planner(database, node_key)
    except KnowledgeNodeNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
    return await get_pubmed_bibliography(database, planner, max(1, min(limit, 20)))


@router.post("/tutor/ask")
async def ask_academy_tutor(payload: TutorAskRequest, request: Request, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    try:
        planner = await build_planner(database, payload.node_key)
    except KnowledgeNodeNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
    lesson = await build_lesson_package(database, planner)
    bibliography = await get_pubmed_bibliography(database, planner, limit=6)
    context = {
        "mode": "academy_tutor",
        "node": planner["node"],
        "muscles": planner.get("muscles", []),
        "protocols": planner.get("protocols", []),
        "clinical_plan": planner.get("clinical_plan"),
        "lesson": {
            "learning_path": lesson.get("learning_path"),
            "objectives": lesson.get("objectives"),
            "competencies": lesson.get("competencies"),
            "checklist": lesson.get("checklist"),
            "case_scenarios": lesson.get("case_scenarios"),
        },
        "placement_validation": payload.placement_validation,
        "waveform_scenario": payload.waveform_scenario,
        "case_grade": payload.case_grade,
        "bibliography": bibliography,
    }
    try:
        retrieval = await retrieve_knowledge(database, build_tutor_retrieval_query(payload.question, context), top_k=payload.retrieval_top_k)
    except JinaEmbeddingError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    context["retrieved_knowledge"] = retrieval.get("items", [])
    result = await DeepSeekClient().ask(
        build_tutor_prompt(payload.question, payload.placement_validation, payload.waveform_scenario, payload.case_grade, bibliography),
        context,
        language=resolve_request_lang(request),
    )
    result["retrieval"] = {
        "status": retrieval.get("status"),
        "model": retrieval.get("model"),
        "items": [
            {
                "score": item.get("score"),
                "source_collection": item.get("source_collection"),
                "source_key": item.get("source_key"),
                "title": item.get("title"),
                "source": item.get("source"),
            }
            for item in retrieval.get("items", [])
        ],
    }
    result["bibliography"] = bibliography
    return result


@router.post("/simulations/validate")
async def validate_simulation(
    payload: SimulationValidationRequest,
    request: Request,
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        planner = await build_planner(database, payload.node_key)
    except KnowledgeNodeNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
    if not planner.get("pins"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation is not available for this node")

    target_lookup = {pin["target_id"]: pin for pin in planner["pins"]}
    results = [score_attempt(attempt, target_lookup, payload.tolerance) for attempt in payload.placements]
    placed_ids = {attempt.target_id for attempt in payload.placements}
    missing_targets = [pin for pin in planner["pins"] if pin["target_id"] not in placed_ids]
    correct_count = sum(1 for result in results if result["status"] == "correct")
    near_count = sum(1 for result in results if result["status"] == "near")
    total_targets = len(planner["pins"])
    completion = len(placed_ids & set(target_lookup.keys())) / total_targets if total_targets else 0
    accuracy = correct_count / total_targets if total_targets else 0

    result_payload = {
        "node": planner["node"],
        "tolerance": payload.tolerance,
        "summary": {
            "status": validation_status(correct_count, near_count, total_targets, len(missing_targets)),
            "correct_count": correct_count,
            "near_count": near_count,
            "placed_count": len(results),
            "target_count": total_targets,
            "completion_percent": round(completion * 100),
            "accuracy_percent": round(accuracy * 100),
        },
        "results": results,
        "missing_targets": [target_summary(pin) for pin in missing_targets],
        "signals": build_signal_feedback(results, total_targets),
    }
    learner = await get_learner_profile(database, request.cookies.get(LEARNER_COOKIE_NAME))
    if learner:
        lesson = await build_lesson_package(database, planner)
        await record_attempt(database, learner["learner_id"], payload.node_key, "placement", result_payload)
        progress = await update_progress(
            database,
            learner["learner_id"],
            payload.node_key,
            {
                "placement_validation": deepcopy(result_payload),
                "placement_passed": result_payload["summary"]["status"] == "passed",
                "placement_accuracy": result_payload["summary"]["accuracy_percent"],
            },
            lesson,
        )
        result_payload["progress"] = progress
    return result_payload


@router.post("/checkpoints/grade")
async def grade_checkpoints(
    payload: CheckpointGradeRequest,
    request: Request,
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        planner = await build_planner(database, payload.node_key)
    except KnowledgeNodeNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
    lesson = await build_lesson_package(database, planner)
    answer_lookup = {answer.checkpoint_id: answer.selected_values for answer in payload.answers}
    grade = grade_checkpoint_submission(lesson, answer_lookup)
    response: dict[str, Any] = {"node": lesson["node"], "grade": grade}
    learner = await get_learner_profile(database, request.cookies.get(LEARNER_COOKIE_NAME))
    if learner:
        await record_attempt(database, learner["learner_id"], payload.node_key, "checkpoint", {"answers": answer_lookup, "grade": grade})
        response["progress"] = await update_progress(
            database,
            learner["learner_id"],
            payload.node_key,
            {"checkpoint_score": grade["score_percent"], "checkpoint_grade": grade},
            lesson,
        )
    return response


@router.post("/cases/grade")
async def grade_case(
    payload: CaseScenarioGradeRequest,
    request: Request,
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        planner = await build_planner(database, payload.node_key)
    except KnowledgeNodeNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
    lesson = await build_lesson_package(database, planner)
    grade = grade_case_scenario(lesson, payload.scenario_id, payload.selected_actions)
    response: dict[str, Any] = {"node": lesson["node"], "grade": grade}
    learner = await get_learner_profile(database, request.cookies.get(LEARNER_COOKIE_NAME))
    if learner:
        progress_doc = await get_or_create_progress(database, learner["learner_id"], payload.node_key, lesson)
        case_grades = {**(progress_doc.get("case_grades") or {}), payload.scenario_id: grade}
        await record_attempt(database, learner["learner_id"], payload.node_key, "case_scenario", {"selected_actions": payload.selected_actions, "grade": grade})
        response["progress"] = await update_progress(
            database,
            learner["learner_id"],
            payload.node_key,
            {"case_grades": case_grades, "last_case_grade": grade},
            lesson,
        )
    return response


@router.post("/waveforms/quiz/grade")
async def grade_waveform_quiz_endpoint(
    payload: WaveformQuizGradeRequest,
    request: Request,
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        planner = await build_planner(database, payload.node_key)
    except KnowledgeNodeNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
    lab = build_waveform_lab(planner, payload.parameters)
    lesson = await build_lesson_package(database, planner)
    answer_lookup = {answer.checkpoint_id: answer.selected_values for answer in payload.answers}
    grade = grade_waveform_quiz(lab, answer_lookup)
    response: dict[str, Any] = {"node": lab["node"], "grade": grade}
    learner = await get_learner_profile(database, request.cookies.get(LEARNER_COOKIE_NAME))
    if learner:
        await record_attempt(database, learner["learner_id"], payload.node_key, "waveform_quiz", {"answers": answer_lookup, "grade": grade})
        response["progress"] = await update_progress(
            database,
            learner["learner_id"],
            payload.node_key,
            {"waveform_reviewed": True, "waveform_quiz_grade": grade, "waveform_parameters": lab.get("parameters")},
            lesson,
        )
    return response


async def build_simulation_library(database: AsyncIOMotorDatabase, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coordinate_names = {item["lat_name"] for item in await database.electrode_coordinates.find({}).to_list(length=None)}
    simulations = []
    for node in nodes:
        linked_muscles = node.get("linked_muscles") or []
        mapped_muscles = [name for name in linked_muscles if name in coordinate_names]
        if not mapped_muscles:
            continue
        protocols = await resolve_protocols(database, node)
        pin_count = await database.electrode_coordinates.aggregate(
            [
                {"$match": {"lat_name": {"$in": mapped_muscles}}},
                {"$project": {"count": {"$size": "$coordinates"}}},
                {"$group": {"_id": None, "total": {"$sum": "$count"}}},
            ]
        ).to_list(length=1)
        simulations.append(
            {
                "node_key": node.get("key"),
                "title_el": node.get("title_el"),
                "title_en": node.get("title_en"),
                "summary_el": node.get("summary_el"),
                "summary_en": node.get("summary_en"),
                "domain": node.get("domain"),
                "node_type": node.get("node_type"),
                "roots": node.get("linked_roots", []),
                "cranial_nerves": node.get("linked_cns", []),
                "channel_count": len(mapped_muscles),
                "pin_count": pin_count[0]["total"] if pin_count else 0,
                "protocol_count": len(protocols),
            }
        )
    return simulations


def simulation_payload(planner: dict[str, Any]) -> dict[str, Any]:
    pins = planner.get("pins", [])
    return {
        "node": planner["node"],
        "targets": [target_summary(pin) for pin in pins],
        "channel_plan": planner.get("clinical_plan", {}).get("channel_plan", []),
        "protocols": planner.get("protocols", []),
        "target_count": len(pins),
        "default_tolerance": 4.0,
    }


def score_attempt(
    attempt: PlacementAttempt,
    target_lookup: dict[str, dict[str, Any]],
    tolerance: float,
) -> dict[str, Any]:
    target = target_lookup.get(attempt.target_id)
    if not target:
        return {
            "target_id": attempt.target_id,
            "status": "unknown_target",
            "distance": None,
            "signal_quality_percent": 0,
            "feedback": "Unknown simulation target.",
        }
    distance = normalized_distance(attempt.x, attempt.y, float(target["x"]), float(target["y"]))
    if distance <= tolerance:
        status_value: Literal["correct", "near", "off"] = "correct"
    elif distance <= tolerance * 2:
        status_value = "near"
    else:
        status_value = "off"
    signal_quality = max(0, min(100, round(100 * (1 - (distance / (tolerance * 3))))))
    return {
        "target_id": attempt.target_id,
        "target": target_summary(target),
        "attempt": {"x": round(attempt.x, 2), "y": round(attempt.y, 2)},
        "expected": {"x": target.get("x"), "y": target.get("y")},
        "distance": round(distance, 2),
        "status": status_value,
        "signal_quality_percent": signal_quality,
        "feedback": feedback_for_status(status_value, target),
    }


def build_signal_feedback(results: list[dict[str, Any]], total_targets: int) -> dict[str, Any]:
    if not results:
        return {"overall_quality_percent": 0, "noise_risk": "not_started", "traces": []}
    quality = round(sum(result.get("signal_quality_percent", 0) for result in results) / max(total_targets, 1))
    if quality >= 80:
        noise_risk = "low"
    elif quality >= 50:
        noise_risk = "moderate"
    else:
        noise_risk = "high"
    return {
        "overall_quality_percent": quality,
        "noise_risk": noise_risk,
        "traces": [
            {
                "target_id": result.get("target_id"),
                "label": result.get("target", {}).get("name_el") or result.get("target", {}).get("lat_name"),
                "quality_percent": result.get("signal_quality_percent", 0),
                "status": result.get("status"),
            }
            for result in results
        ],
    }


def normalized_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)


def validation_status(correct_count: int, near_count: int, total_targets: int, missing_count: int) -> str:
    if missing_count == total_targets:
        return "not_started"
    if correct_count == total_targets:
        return "passed"
    if correct_count + near_count == total_targets:
        return "needs_refinement"
    return "needs_correction"


def feedback_for_status(status_value: str, target: dict[str, Any]) -> str:
    if status_value == "correct":
        return "Placement is within tolerance for the current training map."
    if status_value == "near":
        return f"Refine toward {target.get('placement') or target.get('name_el')} and re-check laterality."
    return f"Reposition to {target.get('placement') or target.get('name_el')} before accepting this channel."


def target_summary(pin: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_id": pin.get("target_id"),
        "lat_name": pin.get("lat_name"),
        "name_el": pin.get("name_el"),
        "name_en": pin.get("name_en"),
        "side": pin.get("side"),
        "view": pin.get("view"),
        "x": pin.get("x"),
        "y": pin.get("y"),
        "placement": pin.get("placement"),
        "placement_en": pin.get("placement_en"),
        "action": pin.get("action"),
        "action_en": pin.get("action_en"),
        "roots": pin.get("roots", []),
        "cranial_nerves": pin.get("cranial_nerves", []),
    }


def build_tutor_retrieval_query(question: str, context: dict[str, Any]) -> str:
    node = context.get("node") or {}
    lesson = context.get("lesson") or {}
    protocols = context.get("protocols") or []
    bibliography = context.get("bibliography") or {}
    rows = [
        question,
        str(node.get("title_el") or node.get("key") or ""),
        str(node.get("summary_el") or ""),
        f"domain: {node.get('domain') or ''}",
        "objectives: " + " · ".join(item.get("title", "") for item in lesson.get("objectives") or []),
        "protocols: " + " · ".join(protocol.get("title_el") or protocol.get("key", "") for protocol in protocols),
        "bibliography: " + " · ".join(item.get("title", "") for item in bibliography.get("items", [])[:6]),
    ]
    if context.get("placement_validation"):
        rows.append(f"placement validation: {context['placement_validation']}")
    if context.get("waveform_scenario"):
        rows.append(f"waveform scenario: {context['waveform_scenario']}")
    if context.get("case_grade"):
        rows.append(f"case grade: {context['case_grade']}")
    return "\n".join(row for row in rows if row.strip())


def build_tutor_prompt(
    question: str,
    placement_validation: dict[str, Any] | None,
    waveform_scenario: str | None,
    case_grade: dict[str, Any] | None,
    bibliography: dict[str, Any],
) -> str:
    parts = [
        "Απάντησε ως IONM Academy tutor στα ελληνικά.",
        "Χρησιμοποίησε μαζί current lesson context, protocols, KB/RAG evidence και PubMed bibliography.",
        "Αν διορθώνεις λάθος placement ή waveform/case mistake, δώσε συγκεκριμένη αιτιολόγηση: τι είναι λάθος, γιατί έχει σημασία, τι πρέπει να ξανακάνει ο learner.",
        "Κλείσε με σύντομο bibliography-backed rationale και κλινική επιφύλαξη.",
        f"Ερώτηση learner: {question}",
    ]
    if placement_validation:
        parts.append(f"Placement validation context: {placement_validation}")
    if waveform_scenario:
        parts.append(f"Waveform scenario context: {waveform_scenario}")
    if case_grade:
        parts.append(f"Case grading context: {case_grade}")
    if bibliography.get("items"):
        parts.append("PubMed titles: " + " | ".join(item.get("title", "") for item in bibliography["items"][:6]))
    return "\n".join(parts)


def lesson_summary(node: dict[str, Any], simulation_ready: bool) -> dict[str, Any]:
    clean = to_public_document(node)
    return {
        "node_key": clean.get("key"),
        "title_el": clean.get("title_el"),
        "title_en": clean.get("title_en"),
        "summary_el": clean.get("summary_el"),
        "summary_en": clean.get("summary_en"),
        "domain": clean.get("domain"),
        "node_type": clean.get("node_type"),
        "simulation_ready": simulation_ready,
    }


def academy_domain_hint(module_key: str | None) -> str | None:
    if not module_key:
        return None
    mapping = {
        "academy.spine": "spine",
        "academy.cranial": "cranial",
        "academy.thyroid": "thyroid",
        "academy.ssep": "ssep",
        "academy.pedicle": "pedicle",
    }
    return mapping.get(module_key)


def node_matches_module(node: dict[str, Any], module_key: str | None, domain_hint: str | None) -> bool:
    if module_key == "academy.foundations":
        return node.get("node_type") in {"mode", "domain", "folder"}
    if module_key == "academy.scenarios":
        return bool(node.get("linked_muscles") or node.get("linked_protocols"))
    if domain_hint:
        return node.get("domain") == domain_hint
    return False