"""Interoperability + system-of-record API.

Two tightly-related capabilities sit behind this router, both auth-gated through
the platform session cookie (same mechanism the rest of the app uses):

* ``/api/ontology`` — the canonical channel ontology. Resolve messy vendor labels
  to canonical clinical identities (deterministic learned aliases first, advisory
  heuristic proposals otherwise), confirm a human-validated mapping into the
  learned map, and (Admin only) promote a mapping to the global standard.

* ``/api/archive`` — the sealed case-object store. Seal a case into an immutable,
  hash-chained record; list the archive (PACS-like); and verify a case's integrity
  chain so any post-hoc tampering is provable.

The legally-critical rule is enforced structurally here: only an explicit human
confirmation writes the canonical ontology, and only the confirmed canonical map
is what gets sealed into a case object. The heuristic/AI proposal is advisory and
never authoritative.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from backend.app.db.mongo import get_database
from backend.app.services import case_seal as cs
from backend.app.services import channel_ontology as onto
from backend.app.services import outcome_registry as outcomes
from backend.app.services.admin_auth import AdminAuthError, ADMIN_COOKIE_NAME, verify_platform_session
from backend.app.services.tele_session import identity_from_session


router = APIRouter(tags=["interoperability"])


# --------------------------------------------------------------------------- #
# Shared identity dependency (mirrors routes/tele.py)
# --------------------------------------------------------------------------- #
def current_identity(request: Request) -> dict[str, Any]:
    try:
        payload = verify_platform_session(request.cookies.get(ADMIN_COOKIE_NAME))
    except AdminAuthError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required.") from error
    return identity_from_session(payload)


# --------------------------------------------------------------------------- #
# Ontology models
# --------------------------------------------------------------------------- #
class ResolveLabelRequest(BaseModel):
    label: str = Field(min_length=1, max_length=160)


class ResolveChannelsRequest(BaseModel):
    channels: list[dict[str, Any]] = Field(default_factory=list)


class ConfirmMappingRequest(BaseModel):
    raw_label: str = Field(min_length=1, max_length=160)
    modality: str = Field(min_length=2, max_length=40)
    anatomy: str | None = Field(default=None, max_length=60)
    laterality: str | None = Field(default=None, max_length=2)
    promote_global: bool = False


# --------------------------------------------------------------------------- #
# Ontology endpoints
# --------------------------------------------------------------------------- #
@router.get("/api/ontology/vocabulary")
async def ontology_vocabulary(identity: dict[str, Any] = Depends(current_identity)) -> dict[str, Any]:
    """The fixed canonical vocabulary (modalities/anatomy/lateralities) for the UI."""
    snapshot = onto.vocabulary_snapshot()
    # The caller's role lets the UI decide whether to offer global-standard promotion
    # (the server still enforces admin-only promotion on /confirm).
    snapshot["actor_role"] = identity.get("role")
    snapshot["can_promote_global"] = str(identity.get("role")) == "admin"
    return snapshot


@router.post("/api/ontology/resolve")
async def ontology_resolve(
    payload: ResolveLabelRequest,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Resolve a single raw label (learned alias first, heuristic proposal second)."""
    try:
        return await onto.resolve_label(database, payload.label, tenant_id=identity["tenant_id"])
    except onto.OntologyError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("/api/ontology/resolve-channels")
async def ontology_resolve_channels(
    payload: ResolveChannelsRequest,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Resolve a whole imported-recording channel list to canonical identities."""
    resolved = await onto.resolve_channels(database, payload.channels, tenant_id=identity["tenant_id"])
    pending = sum(1 for item in resolved if item["resolution"]["source"] != "learned" or not item["resolution"]["resolved"])
    return {"channels": resolved, "pending_confirmation": pending, "total": len(resolved)}


@router.post("/api/ontology/confirm")
async def ontology_confirm(
    payload: ConfirmMappingRequest,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Persist a human-validated mapping into the learned ontology (the CONFIRM step)."""
    try:
        return await onto.confirm_mapping(
            database,
            raw_label=payload.raw_label,
            modality=payload.modality,
            anatomy=payload.anatomy,
            laterality=payload.laterality,
            actor=identity,
            tenant_id=identity["tenant_id"],
            promote_global=payload.promote_global,
        )
    except onto.OntologyError as error:
        # Authorisation failure (non-admin promote) is a 403; bad vocabulary is 422.
        if "administrator" in str(error):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.get("/api/ontology/aliases")
async def ontology_aliases(
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Every learned alias visible to the tenant (its own + the global standard)."""
    aliases = await onto.list_aliases(database, tenant_id=identity["tenant_id"])
    return {"aliases": aliases, "count": len(aliases)}


# --------------------------------------------------------------------------- #
# Archive (sealed case object) models
# --------------------------------------------------------------------------- #
class SealCaseRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=120)
    channel_map: list[dict[str, Any]] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=400)


# --------------------------------------------------------------------------- #
# Archive endpoints
# --------------------------------------------------------------------------- #
@router.post("/api/archive/seal")
async def archive_seal(
    payload: SealCaseRequest,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Seal a case into an immutable, hash-chained record (append-only)."""
    try:
        return await cs.seal_case(
            database,
            case_id=payload.case_id,
            channel_map=payload.channel_map,
            sealed_by=identity,
            reason=payload.reason,
            tenant_id=identity["tenant_id"],
        )
    except cs.SealError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.get("/api/archive/seals")
async def archive_seals(
    case_id: str | None = None,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """PACS-like archive view: latest seal per case, or one case's full chain."""
    seals = await cs.list_seals(database, tenant_id=identity["tenant_id"], case_id=case_id)
    return {"seals": seals, "count": len(seals), "case_id": case_id}


@router.get("/api/archive/seals/{seal_id}")
async def archive_seal_detail(
    seal_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """A single sealed object including its full canonical payload + self-verification."""
    document = await cs.get_seal(database, seal_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seal not found.")
    return {
        "seal": cs.public_seal(document, include_payload=True),
        "verification": cs.verify_seal_document(document),
    }


@router.get("/api/archive/verify/{case_id}")
async def archive_verify(
    case_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Verify a case's entire seal chain (tamper-evidence proof)."""
    try:
        return await cs.verify_case_chain(database, case_id)
    except cs.SealError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


# --------------------------------------------------------------------------- #
# Post-op outcome registry + alert validation (item #12)
# --------------------------------------------------------------------------- #
class OutcomeRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=120)
    deficit: bool
    persistence: str = Field(min_length=1, max_length=20)
    modalities: list[str] = Field(default_factory=list)
    onset: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/api/archive/outcome")
async def archive_record_outcome(
    payload: OutcomeRequest,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Record the post-op neurological outcome for a case (clinician-entered)."""
    try:
        return await outcomes.record_outcome(
            database,
            case_id=payload.case_id,
            deficit=payload.deficit,
            persistence=payload.persistence,
            modalities=payload.modalities,
            onset=payload.onset,
            notes=payload.notes,
            identity=identity,
        )
    except outcomes.OutcomeError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.get("/api/archive/outcome/{case_id}")
async def archive_get_outcome(
    case_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """The recorded post-op outcome for a case, if any."""
    outcome = await outcomes.get_outcome(database, case_id)
    if not outcome:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No outcome recorded for this case.")
    return outcome


@router.get("/api/archive/validation")
async def archive_validation(
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Join outcomes with sealed alert history into a real PPV/NPV validation set."""
    return await outcomes.compute_validation(database, tenant_id=identity["tenant_id"])
