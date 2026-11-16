"""Store-and-forward asynchronous expert review (item #5).

Not every second opinion needs a live consult. This module lets the clinician
package a short slice of the recording — typically the 20-30s around an event of
interest — together with a snapshot of the active protocol/criteria context, and
file it as a case-linked review request. An expert picks it up when free, reads
the clip and the context, and returns a structured verdict. The whole exchange
is recorded against the case so it joins the existing timeline / report engines.

The verdict is the *human expert's* read — there is no automated severity here;
the deterministic engine's criteria travel along only as read-only context.

Clip extraction reuses the dependency-free EDF reader: it concatenates a channel's
per-record sweeps into a continuous waveform and slices the requested window,
decimating only if the window would otherwise exceed a safe stored size.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.services.signal_import import EdfParseError, extract_channel_sweeps


COLLECTION = "async_reviews"
DEFAULT_TENANT = "default"

MIN_CLIP_SECONDS = 1.0
MAX_CLIP_SECONDS = 30.0
MAX_CLIP_CHANNELS = 8
# Cap on stored samples per channel; longer windows are integer-decimated so a
# review document stays comfortably inside Mongo's document limit.
MAX_SAMPLES_PER_CHANNEL = 12000

VALID_STATUSES = {"open", "in_review", "answered", "closed"}
VALID_VERDICTS = {"benign", "concerning", "critical", "inconclusive"}


class AsyncReviewError(ValueError):
    """Raised for invalid async-review operations."""


# --------------------------------------------------------------------------- #
# Clip extraction (pure, dependency-free)
# --------------------------------------------------------------------------- #
def extract_clip(
    raw: bytes,
    *,
    channel_indexes: list[int],
    start_seconds: float,
    duration_seconds: float,
) -> dict[str, Any]:
    """Slice a short multi-channel waveform window out of an EDF recording."""
    if not channel_indexes:
        raise AsyncReviewError("At least one channel must be selected for a review clip.")
    if len(channel_indexes) > MAX_CLIP_CHANNELS:
        raise AsyncReviewError(f"A review clip may include at most {MAX_CLIP_CHANNELS} channels.")
    if start_seconds < 0:
        raise AsyncReviewError("start_seconds cannot be negative.")
    if not MIN_CLIP_SECONDS <= duration_seconds <= MAX_CLIP_SECONDS:
        raise AsyncReviewError(
            f"duration_seconds must be between {MIN_CLIP_SECONDS:g} and {MAX_CLIP_SECONDS:g}."
        )

    channels: list[dict[str, Any]] = []
    clip_seconds = duration_seconds
    for channel_index in channel_indexes:
        try:
            sweeps = extract_channel_sweeps(raw, channel_index)
        except EdfParseError as error:
            raise AsyncReviewError(str(error)) from error
        sample_rate = sweeps.get("sample_rate_hz")
        if not sample_rate:
            raise AsyncReviewError(f"Channel {channel_index} has no resolvable sample rate.")
        waveform: list[float] = []
        for sweep in sweeps["sweeps"]:
            waveform.extend(sweep)
        total = len(waveform)
        start_sample = int(round(start_seconds * sample_rate))
        end_sample = int(round((start_seconds + duration_seconds) * sample_rate))
        if start_sample >= total:
            raise AsyncReviewError(
                f"start_seconds {start_seconds:g} is beyond the recording length for channel {channel_index}."
            )
        end_sample = min(end_sample, total)
        window = waveform[start_sample:end_sample]
        effective_rate = sample_rate
        # Decimate (integer stride) if the window is larger than the stored cap.
        if len(window) > MAX_SAMPLES_PER_CHANNEL:
            stride = (len(window) // MAX_SAMPLES_PER_CHANNEL) + 1
            window = window[::stride]
            effective_rate = round(sample_rate / stride, 3)
        channels.append(
            {
                "index": channel_index,
                "label": sweeps["label"],
                "dimension": sweeps["dimension"],
                "uv_factor": sweeps["uv_factor"],
                "sample_rate_hz": effective_rate,
                "samples": [round(value, 4) for value in window],
            }
        )
    return {
        "start_seconds": round(start_seconds, 3),
        "duration_seconds": round(clip_seconds, 3),
        "channels": channels,
    }


def _normalize_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Pass-through of the protocol/criteria context, bounded to a sane shape."""
    if snapshot is None:
        return {}
    if not isinstance(snapshot, dict):
        raise AsyncReviewError("protocol_snapshot must be an object.")
    return snapshot


# --------------------------------------------------------------------------- #
# MongoDB IO
# --------------------------------------------------------------------------- #
async def ensure_review_indexes(database: AsyncIOMotorDatabase) -> None:
    await database[COLLECTION].create_index("review_id", unique=True)
    await database[COLLECTION].create_index([("tenant_id", 1), ("status", 1)])
    await database[COLLECTION].create_index([("case_id", 1), ("created_at", -1)])


async def create_review(
    database: AsyncIOMotorDatabase,
    raw: bytes,
    *,
    tenant_id: str = DEFAULT_TENANT,
    case_id: str | None,
    node_key: str | None,
    modality: str | None,
    channel_indexes: list[int],
    start_seconds: float,
    duration_seconds: float,
    protocol_snapshot: dict[str, Any] | None,
    message: str | None,
    requested_by: dict[str, Any],
) -> dict[str, Any]:
    clip = extract_clip(
        raw,
        channel_indexes=channel_indexes,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
    )
    now = datetime.now(UTC)
    document = {
        "review_id": uuid4().hex,
        "tenant_id": tenant_id or DEFAULT_TENANT,
        "case_id": (str(case_id).strip() or None) if case_id else None,
        "node_key": (str(node_key).strip() or None) if node_key else None,
        "modality": (str(modality).strip().lower() or None) if modality else None,
        "status": "open",
        "message": (str(message).strip() or None) if message else None,
        "clip": clip,
        "protocol_snapshot": _normalize_snapshot(protocol_snapshot),
        "requested_by_id": requested_by.get("user_id"),
        "requested_by_name": requested_by.get("display_name"),
        "reviewer_id": None,
        "reviewer_name": None,
        "response": None,
        "created_at": now,
        "updated_at": now,
        "claimed_at": None,
        "answered_at": None,
        "closed_at": None,
    }
    await database[COLLECTION].insert_one(document)
    return public_review(document)


async def get_review(database: AsyncIOMotorDatabase, review_id: str) -> dict[str, Any] | None:
    document = await database[COLLECTION].find_one({"review_id": review_id})
    return public_review(document) if document else None


async def list_reviews(
    database: AsyncIOMotorDatabase,
    *,
    tenant_id: str = DEFAULT_TENANT,
    case_id: str | None = None,
    status: str | None = None,
    include_clip: bool = False,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"tenant_id": tenant_id}
    if case_id:
        query["case_id"] = case_id
    if status:
        if status not in VALID_STATUSES:
            raise AsyncReviewError(f"Unknown status filter: {status!r}.")
        query["status"] = status
    rows = await database[COLLECTION].find(query).to_list(length=500)
    rows.sort(key=lambda row: row.get("created_at") or datetime.min.replace(tzinfo=UTC), reverse=True)
    return [public_review(row, include_clip=include_clip) for row in rows]


async def claim_review(
    database: AsyncIOMotorDatabase,
    *,
    review_id: str,
    reviewer: dict[str, Any],
) -> dict[str, Any]:
    review = await database[COLLECTION].find_one({"review_id": review_id})
    if not review:
        raise AsyncReviewError("Review not found.")
    if review.get("status") != "open":
        raise AsyncReviewError(f"Review is {review.get('status')!r}; only open reviews can be claimed.")
    now = datetime.now(UTC)
    await database[COLLECTION].update_one(
        {"review_id": review_id, "status": "open"},
        {
            "$set": {
                "status": "in_review",
                "reviewer_id": reviewer.get("user_id"),
                "reviewer_name": reviewer.get("display_name"),
                "claimed_at": now,
                "updated_at": now,
            }
        },
    )
    updated = await database[COLLECTION].find_one({"review_id": review_id})
    return public_review(updated)


async def answer_review(
    database: AsyncIOMotorDatabase,
    *,
    review_id: str,
    reviewer: dict[str, Any],
    verdict: str,
    notes: str | None,
    annotations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    verdict_key = str(verdict or "").strip().lower()
    if verdict_key not in VALID_VERDICTS:
        raise AsyncReviewError(f"verdict must be one of {sorted(VALID_VERDICTS)}.")
    review = await database[COLLECTION].find_one({"review_id": review_id})
    if not review:
        raise AsyncReviewError("Review not found.")
    if review.get("status") not in {"open", "in_review"}:
        raise AsyncReviewError(f"Review is {review.get('status')!r}; it cannot be answered.")
    now = datetime.now(UTC)
    response = {
        "verdict": verdict_key,
        "notes": (str(notes).strip() or None) if notes else None,
        "annotations": _clean_annotations(annotations),
        "reviewer_id": reviewer.get("user_id"),
        "reviewer_name": reviewer.get("display_name"),
        "responded_at": now,
    }
    await database[COLLECTION].update_one(
        {"review_id": review_id},
        {
            "$set": {
                "status": "answered",
                "reviewer_id": reviewer.get("user_id"),
                "reviewer_name": reviewer.get("display_name"),
                "response": response,
                "answered_at": now,
                "updated_at": now,
            }
        },
    )
    updated = await database[COLLECTION].find_one({"review_id": review_id})
    return public_review(updated)


async def close_review(
    database: AsyncIOMotorDatabase,
    *,
    review_id: str,
) -> dict[str, Any]:
    review = await database[COLLECTION].find_one({"review_id": review_id})
    if not review:
        raise AsyncReviewError("Review not found.")
    now = datetime.now(UTC)
    await database[COLLECTION].update_one(
        {"review_id": review_id},
        {"$set": {"status": "closed", "closed_at": now, "updated_at": now}},
    )
    updated = await database[COLLECTION].find_one({"review_id": review_id})
    return public_review(updated)


def _clean_annotations(annotations: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not annotations:
        return []
    out: list[dict[str, Any]] = []
    for annotation in annotations[:50]:
        if not isinstance(annotation, dict):
            continue
        item: dict[str, Any] = {}
        for key in ("t_seconds", "amplitude_uv"):
            value = annotation.get(key)
            if value is not None:
                try:
                    item[key] = round(float(value), 4)
                except (TypeError, ValueError):
                    pass
        label = annotation.get("label")
        item["label"] = (str(label).strip()[:160] or None) if label else None
        channel = annotation.get("channel_index")
        if channel is not None:
            try:
                item["channel_index"] = int(channel)
            except (TypeError, ValueError):
                pass
        out.append(item)
    return out


def public_review(document: dict[str, Any] | None, *, include_clip: bool = True) -> dict[str, Any]:
    if not document:
        return {}
    clean = dict(document)
    clean.pop("_id", None)
    if not include_clip:
        clip = clean.get("clip") or {}
        channels = clip.get("channels") or []
        clean["clip"] = {
            "start_seconds": clip.get("start_seconds"),
            "duration_seconds": clip.get("duration_seconds"),
            "channel_count": len(channels),
            "channel_labels": [channel.get("label") for channel in channels],
        }
    return clean
