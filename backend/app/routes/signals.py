from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from backend.app.db.mongo import get_database
from backend.app.routes.ai import (
    build_ai_context,
    ground_copilot,
    maybe_retrieve_knowledge,
    normalize_clinical_copilot,
    public_retrieval,
)
from backend.app.services import signal_stream
from backend.app.services.admin_auth import ADMIN_COOKIE_NAME, AdminAuthError, verify_platform_session
from backend.app.services.ai import DeepSeekClient
from backend.app.services.i18n import resolve_request_lang
from backend.app.services.alert_engine import ALERT_CRITERIA, analyze_signal, assess_amplitude_trend
from backend.app.services.ai_guardrail import GuardrailViolation, enforce_numeric_guardrail
from backend.app.services.artifact_gate import gate_alert
from backend.app.services.async_review import (
    AsyncReviewError,
    answer_review,
    claim_review,
    close_review,
    create_review,
    get_review,
    list_reviews,
)
from backend.app.services.measurement_proposals import (
    MeasurementProposalError,
    confirm_measurement,
    propose_from_recording,
)
from backend.app.services.signal_import import EdfParseError, parse_edf
from backend.app.services.tele_session import identity_from_session, is_expert_eligible


router = APIRouter(prefix="/api/signals", tags=["signals"])

# Hard cap for an uploaded recording (vendor EDF exports of a case are well
# within this bound); protects the worker from oversized uploads.
MAX_IMPORT_BYTES = 64 * 1024 * 1024

# Hard cap for a single live byte-delta appended to an open stream.
MAX_STREAM_DELTA_BYTES = 16 * 1024 * 1024


def current_identity(request: Request) -> dict[str, Any]:
    """Resolve the authenticated platform identity (mirrors routes/archive.py)."""
    try:
        payload = verify_platform_session(request.cookies.get(ADMIN_COOKIE_NAME))
    except AdminAuthError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required.") from error
    return identity_from_session(payload)



class ChannelMeasurement(BaseModel):
    amplitude_uv: float | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    threshold_ma: float | None = Field(default=None, ge=0)


class SignalSignature(BaseModel):
    laterality: Literal["unilateral", "bilateral", "global"] | None = None
    onset: Literal["abrupt", "gradual"] | None = None
    scope: Literal["single_channel", "multichannel", "focal"] | None = None
    after_maneuver: bool = False


class SignalAnalyzeRequest(BaseModel):
    family: str = Field(min_length=2, max_length=40)
    channel_label: str | None = Field(default=None, max_length=120)
    baseline: ChannelMeasurement | None = None
    current: ChannelMeasurement | None = None
    metrics_override: dict[str, float] | None = None
    signature: SignalSignature | None = None
    node_key: str | None = Field(default=None, max_length=240)
    case_id: str | None = Field(default=None, max_length=120)


class SignalProposalRequest(SignalAnalyzeRequest):
    observation: str | None = Field(default=None, max_length=2000)
    phase: str | None = Field(default=None, max_length=80)
    use_retrieval: bool = True
    retrieval_top_k: int = Field(default=6, ge=1, le=10)


class StreamOpenFileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=400)
    case_id: str | None = Field(default=None, max_length=120)
    node_key: str | None = Field(default=None, max_length=240)
    source_label: str | None = Field(default=None, max_length=160)


class TrendWatchRequest(BaseModel):
    family: str = Field(min_length=2, max_length=40)
    trend: list[float] = Field(min_length=1, max_length=600)
    baseline_amplitude: float | None = Field(default=None, ge=0)
    window: int = Field(default=10, ge=2, le=200)
    horizon: int = Field(default=5, ge=1, le=100)


class AnesthesiaContext(BaseModel):
    regimen: str | None = Field(default=None, max_length=40)
    recent_bolus: bool | None = None
    mac: float | None = Field(default=None, ge=0)
    map_mmHg: float | None = Field(default=None, ge=0)
    temp_c: float | None = Field(default=None, ge=0)


class QualityGateRequest(SignalAnalyzeRequest):
    sweep: list[float] = Field(min_length=2, max_length=200000)
    sample_rate_hz: float = Field(gt=0)
    anesthesia_context: AnesthesiaContext | None = None


class MeasurementProposed(BaseModel):
    baseline: ChannelMeasurement | None = None
    current: ChannelMeasurement | None = None


class MeasurementConfirmRequest(BaseModel):
    proposal_id: str = Field(min_length=1, max_length=64)
    family: str = Field(min_length=2, max_length=40)
    channel_label: str | None = Field(default=None, max_length=120)
    case_id: str | None = Field(default=None, max_length=120)
    proposed: MeasurementProposed = Field(default_factory=MeasurementProposed)
    confirmed_baseline: ChannelMeasurement | None = None
    confirmed_current: ChannelMeasurement | None = None
    metrics_override: dict[str, float] | None = None
    signature: SignalSignature | None = None


class ReviewAnnotation(BaseModel):
    t_seconds: float | None = Field(default=None, ge=0)
    amplitude_uv: float | None = None
    channel_index: int | None = Field(default=None, ge=0)
    label: str | None = Field(default=None, max_length=160)


class ReviewAnswerRequest(BaseModel):
    verdict: Literal["benign", "concerning", "critical", "inconclusive"]
    notes: str | None = Field(default=None, max_length=4000)
    annotations: list[ReviewAnnotation] = Field(default_factory=list, max_length=50)


@router.get("/criteria")
async def list_alert_criteria() -> dict[str, Any]:
    """Expose the published warning criteria so the UI can show the rule book."""
    return {"criteria": ALERT_CRITERIA}


@router.post("/import")
async def import_recording(file: UploadFile = File(...)) -> dict[str, Any]:
    """Vendor-neutral ingestion of an EDF/EDF+ recording exported by any system."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The uploaded file is empty.")
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Recording exceeds the {MAX_IMPORT_BYTES // (1024 * 1024)} MB import limit.",
        )
    try:
        parsed = parse_edf(raw)
    except EdfParseError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return {
        "filename": file.filename,
        "size_bytes": len(raw),
        "recording": parsed,
    }


# --------------------------------------------------------------------------- #
# Live (Stage B) incremental ingestion
# --------------------------------------------------------------------------- #
def _stream_http_error(error: signal_stream.SignalStreamError) -> HTTPException:
    message = str(error)
    if "not found" in message.lower():
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "no longer active" in message.lower():
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


@router.post("/stream/open")
async def stream_open(
    file: UploadFile = File(...),
    case_id: str | None = Form(default=None),
    node_key: str | None = Form(default=None),
    source_label: str | None = Form(default=None),
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Open a live stream from an opening chunk that carries the EDF header."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The opening chunk is empty.")
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Opening chunk exceeds the {MAX_IMPORT_BYTES // (1024 * 1024)} MB limit.",
        )
    try:
        stream = await signal_stream.open_stream(
            database,
            opening_chunk=raw,
            identity=identity,
            case_id=case_id,
            node_key=node_key,
            source_label=source_label,
        )
    except EdfParseError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return {"stream": stream}


@router.post("/stream/open-file")
async def stream_open_file(
    payload: StreamOpenFileRequest,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Open a live stream from a file the server can read (server-side tailing)."""
    try:
        stream = await signal_stream.open_stream_from_file(
            database,
            path=payload.path,
            identity=identity,
            case_id=payload.case_id,
            node_key=payload.node_key,
            source_label=payload.source_label,
        )
    except signal_stream.SignalStreamError as error:
        raise _stream_http_error(error) from error
    except EdfParseError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return {"stream": stream}


@router.post("/stream/{stream_id}/append")
async def stream_append(
    stream_id: str,
    file: UploadFile = File(...),
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Push transport: ingest a byte delta appended to the recording."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The byte delta is empty.")
    if len(raw) > MAX_STREAM_DELTA_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Byte delta exceeds the {MAX_STREAM_DELTA_BYTES // (1024 * 1024)} MB limit.",
        )
    try:
        delta = await signal_stream.append_bytes(database, stream_id, raw, tenant_id=identity["tenant_id"])
    except signal_stream.SignalStreamError as error:
        raise _stream_http_error(error) from error
    return {"delta": delta}


@router.post("/stream/{stream_id}/tail")
async def stream_tail(
    stream_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Server-side transport: read any new bytes written to the source file."""
    try:
        delta = await signal_stream.tail_file(database, stream_id, tenant_id=identity["tenant_id"])
    except signal_stream.SignalStreamError as error:
        raise _stream_http_error(error) from error
    return {"delta": delta}


@router.get("/stream/{stream_id}")
async def stream_state(
    stream_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        stream = await signal_stream.get_stream(database, stream_id, tenant_id=identity["tenant_id"])
    except signal_stream.SignalStreamError as error:
        raise _stream_http_error(error) from error
    return {"stream": stream}


@router.post("/stream/{stream_id}/close")
async def stream_close(
    stream_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        stream = await signal_stream.close_stream(database, stream_id, tenant_id=identity["tenant_id"])
    except signal_stream.SignalStreamError as error:
        raise _stream_http_error(error) from error
    return {"stream": stream}



@router.post("/analyze")
async def analyze(payload: SignalAnalyzeRequest) -> dict[str, Any]:
    try:
        result = _run_analysis(payload)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return result


@router.post("/analyze/trend-watch")
async def analyze_trend_watch(payload: TrendWatchRequest) -> dict[str, Any]:
    """Advisory slope projection of an amplitude trajectory toward the warning line.

    Item #11: judges the recent trend, not just the latest sweep, and flags
    "trending toward warning" before the single-point comparison would fire.
    Advisory only — it never produces a warning/critical verdict.
    """
    try:
        return assess_amplitude_trend(
            family=payload.family,
            trend=payload.trend,
            baseline_amplitude=payload.baseline_amplitude,
            window=payload.window,
            horizon=payload.horizon,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("/analyze/quality-gate")
async def analyze_quality_gate(payload: QualityGateRequest) -> dict[str, Any]:
    """Deterministic verdict plus an advisory artifact/context quality gate.

    Item #9: the published verdict is computed unchanged, then annotated with a
    signal-quality and anesthesia-context assessment that recommends holding a
    crossing for confirmation when it looks artifact- or confound-driven. The
    gate never suppresses or alters the verdict — a human confirms.
    """
    try:
        alert = _run_analysis(payload)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

    context = payload.anesthesia_context.model_dump(exclude_none=True) if payload.anesthesia_context else None
    return gate_alert(alert, payload.sweep, payload.sample_rate_hz, anesthesia_context=context)


@router.post("/measure/propose")
async def propose_measurements(
    file: UploadFile = File(...),
    channel_index: int = Form(...),
    profile: str = Form(...),
    baseline_record: int = Form(0),
    current_record: int = Form(-1),
    channel_label: str | None = Form(None),
) -> dict[str, Any]:
    """Auto-extract amplitude/latency from a channel's sweeps and PROPOSE them.

    Item #10: the DSP layer pre-fills baseline vs current measurements; the
    response is advisory and stateless (nothing is written). A clinician confirms
    or corrects via `/measure/confirm`.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The uploaded file is empty.")
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Recording exceeds the {MAX_IMPORT_BYTES // (1024 * 1024)} MB import limit.",
        )
    try:
        return propose_from_recording(
            raw,
            channel_index=channel_index,
            profile=profile,
            baseline_record=baseline_record,
            current_record=current_record,
            channel_label=channel_label,
        )
    except (EdfParseError, MeasurementProposalError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("/measure/confirm")
async def confirm_measurements(
    payload: MeasurementConfirmRequest,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Persist the human-confirmed measurement and run the deterministic verdict.

    Item #10: the confirmed values (not the proposed ones) drive the verdict;
    proposed vs confirmed deltas are stored as training signal.
    """
    try:
        return await confirm_measurement(
            database,
            proposal_id=payload.proposal_id,
            family=payload.family,
            proposed=payload.proposed.model_dump(exclude_none=True),
            confirmed_baseline=payload.confirmed_baseline.model_dump(exclude_none=True) if payload.confirmed_baseline else {},
            confirmed_current=payload.confirmed_current.model_dump(exclude_none=True) if payload.confirmed_current else {},
            identity=identity,
            case_id=payload.case_id,
            channel_label=payload.channel_label,
            metrics_override=payload.metrics_override,
            signature=payload.signature.model_dump(exclude_none=True) if payload.signature else None,
        )
    except (MeasurementProposalError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("/ai-proposals")
async def ai_proposals(payload: SignalProposalRequest, request: Request, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    try:
        alert = _run_analysis(payload)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

    context = await build_ai_context(database, payload.node_key, payload.case_id)
    context["detected_alert"] = alert
    task = _build_proposal_task(payload, alert)
    retrieval = await maybe_retrieve_knowledge(database, task, context, payload.use_retrieval, payload.retrieval_top_k)
    try:
        result = await DeepSeekClient().ask_structured_json(task, context, language=resolve_request_lang(request))
        proposals = normalize_clinical_copilot(result["data"])
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    # Item #14: the engine is the sole source of clinical numbers. Reject any AI
    # text that quotes an amplitude/latency/threshold/percentage not produced by
    # the deterministic alert or present in the read-only context/evidence.
    try:
        enforce_numeric_guardrail(
            proposals,
            alert=alert,
            context_objects=(retrieval.get("items", []), context.get("plan"), context.get("node")),
        )
    except GuardrailViolation as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    proposals = ground_copilot(proposals, retrieval)

    return {
        "model": result["model"],
        "alert": alert,
        "proposals": proposals,
        "retrieval": public_retrieval(retrieval),
        "node_key": payload.node_key,
        "case_id": payload.case_id,
        "raw_usage": result.get("raw_usage"),
    }


def _run_analysis(payload: SignalAnalyzeRequest) -> dict[str, Any]:
    return analyze_signal(
        family=payload.family,
        baseline=payload.baseline.model_dump(exclude_none=True) if payload.baseline else None,
        current=payload.current.model_dump(exclude_none=True) if payload.current else None,
        metrics_override=payload.metrics_override,
        signature=payload.signature.model_dump(exclude_none=True) if payload.signature else None,
        channel_label=payload.channel_label,
    )


def _build_proposal_task(payload: SignalProposalRequest, alert: dict[str, Any]) -> str:
    fired = ", ".join(alert.get("fired_criteria") or []) or "none (trend/within-limits)"
    metrics = ", ".join(f"{key}={value}" for key, value in (alert.get("metrics") or {}).items()) or "no quantified metrics"
    rows = [
        "An IONM signal change has been evaluated by the deterministic alert engine.",
        "Produce a prioritized acute response brief grounded ONLY on the provided detected_alert, montage/clinical plan, case timeline and retrieved evidence.",
        "Do not restate, invent, or contradict the published thresholds in detected_alert; add the prioritized next checks, the technical/anesthetic/physiologic/positioning/surgical differential reasoning, and concrete surgeon communication.",
        f"Modality: {alert.get('group_label')} ({alert.get('family')}).",
        f"Detected severity: {alert.get('severity')}.",
        f"Fired criteria: {fired}.",
        f"Quantified metrics: {metrics}.",
    ]
    if payload.phase:
        rows.append(f"Current surgical phase: {payload.phase}.")
    if payload.observation:
        rows.append(f"Clinician observation: {payload.observation}.")
    rows.append("Keep the brief concise, actionable, and clinically cautious; final decisions require qualified clinical judgement.")
    return "\n".join(rows)


# --------------------------------------------------------------------------- #
# Async store-and-forward expert review (item #5)
# --------------------------------------------------------------------------- #
@router.post("/review", status_code=status.HTTP_201_CREATED)
async def create_async_review(
    file: UploadFile = File(...),
    channel_indexes: str = Form(...),
    start_seconds: float = Form(...),
    duration_seconds: float = Form(...),
    case_id: str | None = Form(default=None),
    node_key: str | None = Form(default=None),
    modality: str | None = Form(default=None),
    message: str | None = Form(default=None),
    protocol_snapshot: str | None = Form(default=None),
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Package a short EDF clip + protocol snapshot as a case-linked review request.

    Item #5: the clinician selects channels and a 1-30s window; the clip and the
    active protocol/criteria context are stored for an expert to read and answer
    asynchronously. The expert's verdict is a human read — no automated severity."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The uploaded file is empty.")
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Recording exceeds the {MAX_IMPORT_BYTES // (1024 * 1024)} MB import limit.",
        )
    try:
        indexes = [int(token) for token in channel_indexes.split(",") if token.strip()]
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="channel_indexes must be a comma-separated list of integers.",
        ) from error
    snapshot: dict[str, Any] | None = None
    if protocol_snapshot:
        try:
            snapshot = orjson.loads(protocol_snapshot)
        except orjson.JSONDecodeError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="protocol_snapshot must be valid JSON.",
            ) from error
    try:
        review = await create_review(
            database,
            raw,
            tenant_id=identity["tenant_id"],
            case_id=case_id,
            node_key=node_key,
            modality=modality,
            channel_indexes=indexes,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
            protocol_snapshot=snapshot,
            message=message,
            requested_by=identity,
        )
    except (AsyncReviewError, EdfParseError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return {"review": review}


@router.get("/review")
async def list_async_reviews(
    case_id: str | None = None,
    review_status: str | None = None,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        reviews = await list_reviews(
            database,
            tenant_id=identity["tenant_id"],
            case_id=case_id,
            status=review_status,
            include_clip=False,
        )
    except AsyncReviewError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {"reviews": reviews, "count": len(reviews)}


@router.get("/review/{review_id}")
async def get_async_review(
    review_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    review = await get_review(database, review_id)
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found.")
    return {"review": review}


@router.post("/review/{review_id}/claim")
async def claim_async_review(
    review_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    if not is_expert_eligible(identity):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an eligible consultant can claim a review.")
    try:
        review = await claim_review(database, review_id=review_id, reviewer=identity)
    except AsyncReviewError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return {"review": review}


@router.post("/review/{review_id}/answer")
async def answer_async_review(
    review_id: str,
    payload: ReviewAnswerRequest,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    if not is_expert_eligible(identity):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an eligible consultant can answer a review.")
    try:
        review = await answer_review(
            database,
            review_id=review_id,
            reviewer=identity,
            verdict=payload.verdict,
            notes=payload.notes,
            annotations=[annotation.model_dump(exclude_none=True) for annotation in payload.annotations],
        )
    except AsyncReviewError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return {"review": review}


@router.post("/review/{review_id}/close")
async def close_async_review(
    review_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    review = await get_review(database, review_id)
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found.")
    if identity["user_id"] != review.get("requested_by_id") and identity["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the requester can close the review.")
    try:
        closed = await close_review(database, review_id=review_id)
    except AsyncReviewError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {"review": closed}
