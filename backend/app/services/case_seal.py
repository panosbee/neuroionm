"""Sealed IONM case object — the immutable, tamper-evident system of record.

This is the "DICOM object + PACS" layer for intraoperative neuromonitoring. A
sealed case object assembles, at a single instant, the five clinical layers of a
case into one canonical envelope and freezes it behind a cryptographic seal:

* Layer 0/1 — the canonical channel identities (from channel_ontology), i.e. the
  semantic wrapper that raw EDF lacks.
* Layer 2  — the deterministic alert interpretations already on the timeline.
* Layer 3  — the human event timeline (who did what, when; tele/consent events).
* Layer 4  — INTEGRITY: a SHA-256 content hash over a canonical JSON projection,
  chained to the previous seal of the same case (a per-case hash chain).

Why this is the moat, not a feature: once a hospital has accumulated thousands of
these sealed, hash-chained, medico-legally meaningful records, the data cannot be
migrated elsewhere without breaking the chain and forfeiting its evidentiary
value. The format is the system of record; the machines below become commodity.

Guarantees enforced here:
* Append-only. A sealed object is never mutated. A correction is a NEW seal
  (a new version) that references the prior one; the chain preserves full history.
* Self-verifying. ``verify_seal`` recomputes the hash from the stored payload and
  re-checks the chain link, so any later tampering is provable.
* Deterministic. The canonical serialization is sorted and stable, so the same
  content always yields the same hash on any machine (court-reproducible).

Nothing is mocked: the hash is a real SHA-256 over the real case content pulled
from MongoDB at seal time.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase


SEAL_COLLECTION = "case_seals"
# Bumped only if the canonical serialization rules change; stored in each seal so
# an old seal is always verifiable against the algorithm that produced it.
SEAL_SCHEMA_VERSION = 1
HASH_ALGORITHM = "sha256"
# The genesis link for the first seal of a case (no predecessor).
GENESIS_PREVIOUS_HASH = "0" * 64


class SealError(RuntimeError):
    """Raised for invalid sealing/verification operations."""


# --------------------------------------------------------------------------- #
# Canonical serialization + hashing (pure, deterministic)
# --------------------------------------------------------------------------- #
def canonical_json(payload: dict[str, Any]) -> str:
    """A stable, sorted, separator-fixed JSON encoding.

    Determinism is the whole point: the same logical content must serialize
    byte-for-byte identically on any platform so the hash is reproducible in a
    courtroom years later. ``default=str`` renders datetimes via ISO strings.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)


def compute_content_hash(canonical_payload: dict[str, Any], previous_hash: str) -> str:
    """SHA-256 over the canonical payload chained to the predecessor seal hash.

    The previous hash is folded in so each seal commits to the entire prior
    history of the case — altering any earlier seal invalidates every later link.
    """
    serialized = canonical_json(canonical_payload)
    digest = hashlib.sha256()
    digest.update(previous_hash.encode("ascii"))
    digest.update(b"\x1f")  # unit separator between the chain link and the body
    digest.update(serialized.encode("utf-8"))
    return digest.hexdigest()


def compute_content_fingerprint(canonical_payload: dict[str, Any]) -> str:
    """A version- and chain-independent digest of the meaningful case content.

    The chained ``content_hash`` deliberately folds in the per-seal ``version`` and
    the predecessor hash, so two seals of identical content still differ (they sit
    at different chain positions). To answer the distinct question "did the case
    content actually change since the last seal?" we hash only the content body,
    with ``version`` removed. Equal fingerprints ⇒ nothing changed ⇒ no new seal.
    """
    body = {key: value for key, value in canonical_payload.items() if key != "version"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Canonical payload assembly (pure)
# --------------------------------------------------------------------------- #
def _clean(document: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key != "_id"}


def build_canonical_payload(
    *,
    case: dict[str, Any],
    events: list[dict[str, Any]],
    debriefs: list[dict[str, Any]],
    channel_map: list[dict[str, Any]],
    version: int,
) -> dict[str, Any]:
    """Assemble the immutable five-layer envelope for a case at seal time.

    Only stable, content-bearing fields are included; volatile bookkeeping like
    ``updated_at`` is intentionally excluded so re-sealing identical content is a
    no-op-equivalent (same body hash) rather than spurious churn.
    """
    return {
        "schema_version": SEAL_SCHEMA_VERSION,
        "object_type": "ionm_case_record",
        "version": version,
        "case": {
            "case_id": case.get("case_id"),
            "title": case.get("title"),
            "mode": case.get("mode"),
            "procedure_key": case.get("procedure_key"),
            "selected_node_keys": case.get("selected_node_keys") or [],
            "non_identifying_ref": case.get("non_identifying_ref"),
            "notes": case.get("notes"),
            "created_at": case.get("created_at"),
        },
        # Layer 0/1 — canonical channel identities (semantic wrapper).
        "channel_map": [
            {
                "raw_label": item.get("raw_label"),
                "canonical_code": (item.get("mapping") or {}).get("canonical_code"),
                "canonical_label": (item.get("mapping") or {}).get("canonical_label"),
                "alert_family": (item.get("mapping") or {}).get("alert_family"),
                "source": item.get("source"),
                "baseline_pp_amplitude_uv": item.get("baseline_pp_amplitude_uv"),
                "latest_pp_amplitude_uv": item.get("latest_pp_amplitude_uv"),
            }
            for item in channel_map
        ],
        # Layer 2/3 — interpretations + human timeline.
        "timeline": [
            {
                "event_id": event.get("event_id"),
                "event_type": event.get("event_type"),
                "title": event.get("title"),
                "description": event.get("description"),
                "node_key": event.get("node_key"),
                "metadata": event.get("metadata") or {},
                "created_at": event.get("created_at"),
            }
            for event in events
        ],
        "debriefs": [
            {
                "debrief_id": debrief.get("debrief_id"),
                "focus": debrief.get("focus"),
                "summary": debrief.get("summary"),
                "created_at": debrief.get("created_at"),
            }
            for debrief in debriefs
        ],
        "counts": {
            "events": len(events),
            "debriefs": len(debriefs),
            "channels": len(channel_map),
        },
    }


def new_seal_document(
    *,
    case_id: str,
    version: int,
    canonical_payload: dict[str, Any],
    content_hash: str,
    previous_hash: str,
    sealed_by: dict[str, Any],
    reason: str | None,
    tenant_id: str = "default",
    content_fingerprint: str | None = None,
) -> dict[str, Any]:
    """An immutable seal row (the stored case object + its integrity proof)."""
    return {
        "seal_id": str(uuid4()),
        "tenant_id": tenant_id,
        "case_id": case_id,
        "version": version,
        "object_type": "ionm_case_record",
        "schema_version": SEAL_SCHEMA_VERSION,
        "hash_algorithm": HASH_ALGORITHM,
        "content_hash": content_hash,
        "previous_hash": previous_hash,
        # Version- and chain-independent digest of the content body, used only to
        # detect "did the content change since last seal?" (idempotency guard).
        "content_fingerprint": content_fingerprint or compute_content_fingerprint(canonical_payload),
        "payload": canonical_payload,
        "sealed_by": sealed_by.get("username") or sealed_by.get("user_id"),
        "sealed_by_role": sealed_by.get("role"),
        "reason": reason,
        "sealed_at": datetime.now(UTC),
    }


def public_seal(document: dict[str, Any], *, include_payload: bool = False) -> dict[str, Any]:
    """A safe projection of a seal (payload omitted from list views by default)."""
    clean = _clean(document)
    if not include_payload:
        clean.pop("payload", None)
    return clean


def verify_seal_document(document: dict[str, Any]) -> dict[str, Any]:
    """Recompute the hash from the stored payload and report integrity.

    Returns a structured verdict. ``ok`` is True only when the recomputed hash
    matches the stored ``content_hash`` exactly. This is what proves to a third
    party that the record was not altered after sealing.
    """
    stored_hash = document.get("content_hash") or ""
    previous_hash = document.get("previous_hash") or GENESIS_PREVIOUS_HASH
    payload = document.get("payload")
    if not isinstance(payload, dict):
        return {"seal_id": document.get("seal_id"), "ok": False, "reason": "Seal has no stored payload."}
    recomputed = compute_content_hash(payload, previous_hash)
    ok = bool(stored_hash) and recomputed == stored_hash
    return {
        "seal_id": document.get("seal_id"),
        "case_id": document.get("case_id"),
        "version": document.get("version"),
        "ok": ok,
        "stored_hash": stored_hash,
        "recomputed_hash": recomputed,
        "previous_hash": previous_hash,
        "reason": None if ok else "Recomputed hash does not match the stored seal (content was altered).",
    }


def verify_chain(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify a full per-case seal chain (ordered by version ascending).

    Checks each seal's own content hash AND that every link points at its
    predecessor's hash, so a removed or rewritten earlier version is detectable.
    """
    ordered = sorted(documents, key=lambda doc: doc.get("version", 0))
    links: list[dict[str, Any]] = []
    expected_previous = GENESIS_PREVIOUS_HASH
    chain_ok = True
    for document in ordered:
        verdict = verify_seal_document(document)
        link_ok = (document.get("previous_hash") or GENESIS_PREVIOUS_HASH) == expected_previous
        verdict["link_ok"] = link_ok
        verdict["ok"] = bool(verdict["ok"] and link_ok)
        if not verdict["ok"]:
            chain_ok = False
            if not link_ok and verdict.get("reason") is None:
                verdict["reason"] = "Broken chain link: previous_hash does not match the prior seal."
        links.append(verdict)
        expected_previous = document.get("content_hash") or ""
    return {"ok": chain_ok, "length": len(ordered), "links": links}


# --------------------------------------------------------------------------- #
# Thin async MongoDB IO
# --------------------------------------------------------------------------- #
async def ensure_seal_indexes(database: AsyncIOMotorDatabase) -> None:
    """Indexes for the append-only seal archive (one row per case version)."""
    await database[SEAL_COLLECTION].create_index([("case_id", 1), ("version", 1)], unique=True, name="seal_case_version")
    await database[SEAL_COLLECTION].create_index([("tenant_id", 1), ("sealed_at", -1)], name="seal_recent")
    await database[SEAL_COLLECTION].create_index([("content_hash", 1)], name="seal_hash")


async def latest_seal(database: AsyncIOMotorDatabase, case_id: str) -> dict[str, Any] | None:
    """The most recent seal of a case, or None if it has never been sealed."""
    return await database[SEAL_COLLECTION].find_one({"case_id": case_id}, sort=[("version", -1)])


async def seal_case(
    database: AsyncIOMotorDatabase,
    *,
    case_id: str,
    channel_map: list[dict[str, Any]] | None,
    sealed_by: dict[str, Any],
    reason: str | None = None,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """Assemble and persist a new sealed version of a case.

    Pulls the live case, its full event timeline and debriefs from MongoDB,
    folds in the confirmed canonical channel map, hashes the canonical payload
    chained to the previous seal, and appends an immutable seal row.

    Idempotency guard: if the new content hash equals the latest seal's hash,
    no new version is created (sealing unchanged content is a no-op) and the
    existing seal is returned with ``created=False``.
    """
    case = await database.cases.find_one({"case_id": case_id})
    if not case:
        raise SealError("Case not found.")

    events = await database.case_events.find({"case_id": case_id}).sort("created_at", 1).to_list(length=None)
    debriefs = await database.case_debriefs.find({"case_id": case_id}).sort("created_at", 1).to_list(length=None)

    previous = await latest_seal(database, case_id)
    previous_hash = previous["content_hash"] if previous else GENESIS_PREVIOUS_HASH
    version = (previous["version"] + 1) if previous else 1

    payload = build_canonical_payload(
        case=case,
        events=events,
        debriefs=debriefs,
        channel_map=channel_map or [],
        version=version,
    )
    content_hash = compute_content_hash(payload, previous_hash)
    content_fingerprint = compute_content_fingerprint(payload)

    # Idempotency is decided on the content fingerprint (version/chain independent),
    # NOT the chained content hash (which always differs across chain positions).
    if previous is not None:
        previous_fingerprint = previous.get("content_fingerprint")
        if previous_fingerprint is None:
            # Legacy seal without a stored fingerprint: recompute it from its payload.
            previous_payload = previous.get("payload")
            if isinstance(previous_payload, dict):
                previous_fingerprint = compute_content_fingerprint(previous_payload)
        if previous_fingerprint == content_fingerprint:
            return {"created": False, "seal": public_seal(previous), "reason": "Case content is unchanged since the last seal."}

    document = new_seal_document(
        case_id=case_id,
        version=version,
        canonical_payload=payload,
        content_hash=content_hash,
        previous_hash=previous_hash,
        sealed_by=sealed_by,
        reason=reason,
        tenant_id=tenant_id,
        content_fingerprint=content_fingerprint,
    )
    await database[SEAL_COLLECTION].insert_one(document)
    # Stamp the case so the UI can show it has a sealed record (mutable pointer
    # only — the seals themselves stay immutable).
    await database.cases.update_one(
        {"case_id": case_id},
        {"$set": {"latest_seal_version": version, "latest_seal_hash": content_hash, "sealed_at": document["sealed_at"]}},
    )
    return {"created": True, "seal": public_seal(document), "version": version, "content_hash": content_hash}


async def list_seals(
    database: AsyncIOMotorDatabase,
    *,
    tenant_id: str = "default",
    case_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """The PACS-like archive view: sealed objects, newest first.

    When ``case_id`` is given, returns that case's full version chain (ascending);
    otherwise returns the latest seal of each case across the tenant.
    """
    if case_id:
        rows = await database[SEAL_COLLECTION].find({"case_id": case_id}).sort("version", 1).to_list(length=limit)
        return [public_seal(row) for row in rows]

    # Latest seal per case across the tenant: sort by (case_id, version desc) and
    # keep the first (highest-version) document of each case.
    pipeline = [
        {"$match": {"tenant_id": tenant_id}},
        {"$sort": {"case_id": 1, "version": -1}},
        {
            "$group": {
                "_id": "$case_id",
                "doc": {"$first": "$$ROOT"},
            }
        },
        {"$replaceRoot": {"newRoot": "$doc"}},
        {"$sort": {"sealed_at": -1}},
        {"$limit": limit},
    ]
    rows = await database[SEAL_COLLECTION].aggregate(pipeline).to_list(length=limit)
    return [public_seal(row) for row in rows]


async def get_seal(database: AsyncIOMotorDatabase, seal_id: str) -> dict[str, Any] | None:
    return await database[SEAL_COLLECTION].find_one({"seal_id": seal_id})


async def verify_case_chain(database: AsyncIOMotorDatabase, case_id: str) -> dict[str, Any]:
    """Verify the integrity of a case's entire seal chain from storage."""
    rows = await database[SEAL_COLLECTION].find({"case_id": case_id}).sort("version", 1).to_list(length=None)
    if not rows:
        raise SealError("Case has no sealed records.")
    return verify_chain(rows)
