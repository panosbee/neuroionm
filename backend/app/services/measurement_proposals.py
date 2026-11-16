"""Auto-extracted measurement proposals with human confirmation (item #10).

This extends the platform's "AI proposes, human confirms" golden rule from the
channel ontology (where the AI proposes a canonical name and a human seals it)
to *measurement detection*. The DSP layer (item #8) auto-extracts baseline and
current amplitude/latency from the actual sweeps and PROPOSES them; a clinician
then confirms or corrects each value. Every correction is persisted as a labelled
training signal (proposed vs confirmed) so the extractor can be tuned over time.

Invariants:

* **Proposing is stateless and writes nothing.** Exactly like the ontology
  resolve step — the AI may propose with a capped confidence but never commits.
* **Confirming is the only write, and it is human-authored.** The confirmed
  numbers (not the proposed ones) are what flow into the deterministic verdict
  and, ultimately, the sealed case. The proposed numbers are kept alongside only
  as a correction record / training label.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.services.alert_engine import analyze_signal
from backend.app.services.signal_dsp import MODALITY_PROFILES, extract_measurements
from backend.app.services.signal_import import extract_channel_sweeps


COLLECTION = "measurement_confirmations"

# Advisory confidence cap: a DSP proposal is never presented as certain, mirroring
# the ontology AI-proposal cap. The human confirmation carries the authority.
PROPOSAL_CONFIDENCE = 0.9


class MeasurementProposalError(ValueError):
    """Raised for invalid proposal/confirmation requests."""


async def ensure_measurement_indexes(database: AsyncIOMotorDatabase) -> None:
    collection = database[COLLECTION]
    await collection.create_index("proposal_id", unique=True)
    await collection.create_index([("tenant_id", 1), ("created_at", -1)])
    await collection.create_index([("case_id", 1), ("created_at", -1)])


def propose_from_sweeps(
    baseline_sweep: list[float],
    current_sweep: list[float],
    sample_rate: float,
    profile: str,
    *,
    channel_label: str | None,
    uv_factor: float | None,
) -> dict[str, Any]:
    """Run the DSP extractor and wrap it as an advisory, unsaved proposal."""
    if profile not in MODALITY_PROFILES:
        raise MeasurementProposalError(f"Unknown modality profile: {profile!r}")
    measurement = extract_measurements(
        baseline_sweep,
        current_sweep,
        sample_rate,
        profile,
        scale_to_uv=uv_factor if uv_factor and uv_factor > 0 else None,
    )
    return {
        "proposal_id": secrets.token_hex(8),
        "status": "proposed",
        "needs_confirmation": True,
        "confidence": PROPOSAL_CONFIDENCE,
        "profile": profile,
        "channel_label": channel_label,
        "measurement": measurement,
        "advisory": (
            "AI-proposed measurements from automated sweep analysis. Confirm or correct each value "
            "before it is used by the alert engine or sealed into the case."
        ),
    }


def propose_from_recording(
    raw: bytes,
    *,
    channel_index: int,
    profile: str,
    baseline_record: int = 0,
    current_record: int = -1,
    channel_label: str | None = None,
) -> dict[str, Any]:
    """Decode one channel of an EDF recording and propose measurements for it."""
    extracted = extract_channel_sweeps(raw, channel_index)
    sweeps = extracted["sweeps"]
    if len(sweeps) < 2:
        raise MeasurementProposalError("Recording has fewer than two sweeps; cannot propose baseline vs current.")
    try:
        baseline_sweep = sweeps[baseline_record]
        current_sweep = sweeps[current_record]
    except IndexError as error:
        raise MeasurementProposalError("Requested sweep index is out of range for this recording.") from error

    sample_rate = extracted["sample_rate_hz"]
    if not sample_rate:
        raise MeasurementProposalError("Recording has no usable sample rate.")

    proposal = propose_from_sweeps(
        baseline_sweep,
        current_sweep,
        sample_rate,
        profile,
        channel_label=channel_label or extracted["label"],
        uv_factor=extracted["uv_factor"],
    )
    proposal["channel_index"] = channel_index
    proposal["baseline_record"] = baseline_record if baseline_record >= 0 else len(sweeps) + baseline_record
    proposal["current_record"] = current_record if current_record >= 0 else len(sweeps) + current_record
    return proposal


def _corrections(proposed: dict[str, Any], confirmed_baseline: dict[str, Any], confirmed_current: dict[str, Any]) -> dict[str, Any]:
    """Per-field delta between the proposed and the human-confirmed values.

    A non-zero delta is a labelled training signal for tuning the extractor; a
    zero delta confirms the proposal was accepted as-is.
    """
    proposed_baseline = (proposed.get("baseline") or {})
    proposed_current = (proposed.get("current") or {})

    def delta(field: str, proposed_side: dict[str, Any], confirmed_side: dict[str, Any]) -> dict[str, Any] | None:
        p = proposed_side.get(field)
        c = confirmed_side.get(field)
        if p is None and c is None:
            return None
        corrected = not (isinstance(p, (int, float)) and isinstance(c, (int, float)) and abs(p - c) < 1e-9)
        return {
            "proposed": p,
            "confirmed": c,
            "delta": round(c - p, 4) if isinstance(p, (int, float)) and isinstance(c, (int, float)) else None,
            "corrected": corrected,
        }

    result: dict[str, Any] = {"baseline": {}, "current": {}, "any_corrected": False}
    for field in ("amplitude_uv", "latency_ms"):
        b = delta(field, proposed_baseline, confirmed_baseline)
        c = delta(field, proposed_current, confirmed_current)
        if b is not None:
            result["baseline"][field] = b
            result["any_corrected"] = result["any_corrected"] or b["corrected"]
        if c is not None:
            result["current"][field] = c
            result["any_corrected"] = result["any_corrected"] or c["corrected"]
    return result


async def confirm_measurement(
    database: AsyncIOMotorDatabase,
    *,
    proposal_id: str,
    family: str,
    proposed: dict[str, Any],
    confirmed_baseline: dict[str, Any],
    confirmed_current: dict[str, Any],
    identity: dict[str, Any],
    case_id: str | None,
    channel_label: str | None,
    metrics_override: dict[str, Any] | None = None,
    signature: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist the human-confirmed measurement and run the deterministic verdict.

    The verdict is computed on the CONFIRMED values only; the proposed values are
    stored as a correction/training record next to them.
    """
    if not confirmed_baseline and not confirmed_current:
        raise MeasurementProposalError("At least one confirmed measurement is required.")

    verdict = analyze_signal(
        family=family,
        baseline=confirmed_baseline or None,
        current=confirmed_current or None,
        metrics_override=metrics_override,
        signature=signature,
        channel_label=channel_label,
    )

    corrections = _corrections(proposed or {}, confirmed_baseline or {}, confirmed_current or {})
    now = datetime.now(UTC)
    record = {
        "proposal_id": proposal_id or secrets.token_hex(8),
        "tenant_id": identity.get("tenant_id", "default"),
        "user_id": identity.get("user_id"),
        "confirmed_by": identity.get("display_name") or identity.get("username"),
        "case_id": case_id,
        "family": family,
        "channel_label": channel_label,
        "proposed": proposed,
        "confirmed": {"baseline": confirmed_baseline, "current": confirmed_current},
        "corrections": corrections,
        "verdict_severity": verdict["severity"],
        "verdict_metrics": verdict["metrics"],
        "created_at": now,
    }
    await database[COLLECTION].insert_one(dict(record))

    return {
        "proposal_id": record["proposal_id"],
        "status": "confirmed",
        "confirmed_by": record["confirmed_by"],
        "case_id": case_id,
        "channel_label": channel_label,
        "corrections": corrections,
        "verdict": verdict,
        "created_at": now.isoformat(),
    }
