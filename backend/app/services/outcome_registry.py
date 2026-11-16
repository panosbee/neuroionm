"""Post-operative outcome registry and alert-validation loop (item #12).

Every sealed case can be paired, after surgery, with its real neurological
outcome (was there a new deficit; which modality; transient or permanent). Joined
against whether the deterministic engine fired an alert during that case, this
yields the platform's OWN prospectively-collected validation dataset: a real 2x2
confusion matrix with positive/negative predictive value, sensitivity and
specificity, plus the labelled false-alarm / missed-alarm cases needed to tune
thresholds. Collected from day zero, this is what lets the product defend its
performance with its own evidence rather than borrowed literature.

The outcome is a human-entered clinical fact (never inferred by the system); the
validation statistics are computed deterministically from outcomes + sealed
alert history.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.services.case_seal import SEAL_COLLECTION, latest_seal


COLLECTION = "case_outcomes"

# Persistence of a post-op deficit. "none" means no new deficit occurred.
VALID_PERSISTENCE = {"none", "transient", "permanent"}


class OutcomeError(ValueError):
    """Raised for invalid outcome submissions."""


async def ensure_outcome_indexes(database: AsyncIOMotorDatabase) -> None:
    collection = database[COLLECTION]
    await collection.create_index("case_id", unique=True)
    await collection.create_index([("tenant_id", 1), ("recorded_at", -1)])


def _normalise_modalities(modalities: Any) -> list[str]:
    if modalities is None:
        return []
    if isinstance(modalities, str):
        modalities = [modalities]
    if not isinstance(modalities, (list, tuple, set)):
        raise OutcomeError("modalities must be a string or a list of strings.")
    return sorted({str(item).strip().lower() for item in modalities if str(item).strip()})


async def record_outcome(
    database: AsyncIOMotorDatabase,
    *,
    case_id: str,
    deficit: bool,
    persistence: str,
    modalities: Any = None,
    onset: str | None = None,
    notes: str | None = None,
    identity: dict[str, Any],
) -> dict[str, Any]:
    """Record (or update) the post-op outcome for one case.

    One current outcome per case (the latest entry supersedes). The deficit fact
    and its persistence are clinician-entered; the registry only validates
    internal consistency.
    """
    if not case_id:
        raise OutcomeError("case_id is required.")
    persistence = (persistence or "").strip().lower()
    if persistence not in VALID_PERSISTENCE:
        raise OutcomeError(f"persistence must be one of {sorted(VALID_PERSISTENCE)}.")
    if deficit and persistence == "none":
        raise OutcomeError("A deficit cannot have persistence 'none'; choose transient or permanent.")
    if not deficit and persistence != "none":
        raise OutcomeError("persistence must be 'none' when there is no deficit.")

    modality_list = _normalise_modalities(modalities)
    if deficit and not modality_list:
        raise OutcomeError("At least one affected modality is required when a deficit is recorded.")

    now = datetime.now(UTC)
    document = {
        "case_id": case_id,
        "tenant_id": identity.get("tenant_id", "default"),
        "deficit": bool(deficit),
        "persistence": persistence,
        "modalities": modality_list,
        "onset": (onset or "").strip().lower() or None,
        "notes": (notes or "").strip() or None,
        "recorded_by": identity.get("display_name") or identity.get("username") or identity.get("user_id"),
        "recorded_at": now,
    }
    await database[COLLECTION].update_one(
        {"case_id": case_id},
        {"$set": document, "$setOnInsert": {"first_recorded_at": now}},
        upsert=True,
    )
    return {**document, "recorded_at": now.isoformat()}


async def get_outcome(database: AsyncIOMotorDatabase, case_id: str) -> dict[str, Any] | None:
    document = await database[COLLECTION].find_one({"case_id": case_id})
    if not document:
        return None
    document.pop("_id", None)
    recorded_at = document.get("recorded_at")
    if isinstance(recorded_at, datetime):
        document["recorded_at"] = recorded_at.isoformat()
    return document


# --------------------------------------------------------------------------- #
# Pure validation maths (deterministic, no IO)
# --------------------------------------------------------------------------- #
def case_alerted(seal_payload: dict[str, Any]) -> dict[str, Any]:
    """Did the deterministic engine fire a warning/critical alert in this case?

    Inspects the sealed timeline. An event is an alert when its metadata records a
    warning/critical severity, an explicit is_alert flag, or any fired_criteria —
    regardless of how the timeline labelled the event type.
    """
    alerted = False
    severities: set[str] = set()
    modalities: set[str] = set()
    for event in (seal_payload or {}).get("timeline", []):
        metadata = event.get("metadata") or {}
        severity = str(metadata.get("severity") or "").lower()
        fired = metadata.get("fired_criteria") or []
        is_alert = bool(metadata.get("is_alert"))
        if severity in {"warning", "critical"} or is_alert or fired:
            alerted = True
            if severity:
                severities.add(severity)
            modality = metadata.get("group") or metadata.get("family")
            if modality:
                modalities.add(str(modality).lower())
    return {"alerted": alerted, "severities": sorted(severities), "modalities": sorted(modalities)}


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def summarise(pairs: list[dict[str, bool]]) -> dict[str, Any]:
    """Build the 2x2 confusion matrix + derived predictive values.

    Each pair is ``{"alerted": bool, "deficit": bool}``. Predictive values are
    None when their denominator is empty (not yet estimable), never faked.
    """
    tp = sum(1 for p in pairs if p["alerted"] and p["deficit"])
    fp = sum(1 for p in pairs if p["alerted"] and not p["deficit"])
    fn = sum(1 for p in pairs if not p["alerted"] and p["deficit"])
    tn = sum(1 for p in pairs if not p["alerted"] and not p["deficit"])
    return {
        "n": len(pairs),
        "confusion": {"true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn},
        "ppv": _ratio(tp, tp + fp),
        "npv": _ratio(tn, tn + fn),
        "sensitivity": _ratio(tp, tp + fn),
        "specificity": _ratio(tn, tn + fp),
    }


async def compute_validation(database: AsyncIOMotorDatabase, *, tenant_id: str = "default") -> dict[str, Any]:
    """Join recorded outcomes with sealed alert history into validation stats."""
    outcomes = await database[COLLECTION].find({"tenant_id": tenant_id}).to_list(length=None)
    pairs: list[dict[str, Any]] = []
    case_rows: list[dict[str, Any]] = []
    for outcome in outcomes:
        case_id = outcome.get("case_id")
        seal = await latest_seal(database, case_id) if case_id else None
        if not seal:
            continue  # no sealed record to validate against
        alert_info = case_alerted(seal.get("payload") or {})
        alerted = alert_info["alerted"]
        deficit = bool(outcome.get("deficit"))
        pairs.append({"alerted": alerted, "deficit": deficit})
        case_rows.append({
            "case_id": case_id,
            "alerted": alerted,
            "deficit": deficit,
            "persistence": outcome.get("persistence"),
            "outcome_modalities": outcome.get("modalities") or [],
            "alert_modalities": alert_info["modalities"],
            "classification": _classify(alerted, deficit),
        })
    summary = summarise(pairs)
    summary["tenant_id"] = tenant_id
    summary["cases"] = case_rows
    summary["seal_collection"] = SEAL_COLLECTION
    return summary


def _classify(alerted: bool, deficit: bool) -> str:
    if alerted and deficit:
        return "true_positive"
    if alerted and not deficit:
        return "false_positive"
    if not alerted and deficit:
        return "false_negative"
    return "true_negative"
