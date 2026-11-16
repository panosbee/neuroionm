"""Incremental (quasi-real-time) ingestion of a growing EDF/EDF+ recording.

Stage A of the signal pipeline parses a finished file in one shot
(:func:`signal_import.parse_edf`). Stage B — implemented here — turns that into a
LIVE path: a recording that the acquisition system is still writing is ingested
delta-by-delta. The EDF header is parsed exactly once when the stream opens;
thereafter each appended byte slice is decoded into only the NEW complete data
records, the per-channel peak-to-peak amplitude trend is extended (bounded), and
any trailing partial record is carried forward to the next delta.

State lives in MongoDB (``signal_streams``) so it survives across gunicorn
workers and restarts. Nothing is mocked — every amplitude is decoded from real
uploaded bytes via :func:`signal_import.decode_data_region`.

Two transports feed the same delta engine:

* **Push** (default): a tiny agent on the acquisition machine tails the file
  locally and POSTs the appended bytes to ``/api/signals/stream/{id}/append``.
* **Server-side tail**: when the recording lives on a directory the server can
  read (``IONM_SIGNAL_STREAM_DIR``), the server re-reads the file from the last
  consumed offset on ``/api/signals/stream/{id}/tail``.

Both paths converge on :func:`_apply_delta` so behaviour is identical.
"""

from __future__ import annotations

import base64
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.core.config import settings
from backend.app.services.signal_import import (
    decode_data_region,
    edf_layout,
    uv_factor,
)


COLLECTION = "signal_streams"

# Per-channel rolling amplitude trend is bounded so a long case cannot grow the
# stream document without limit; the full trend can always be reconstructed from
# the sealed/imported file. 600 records ≈ 10 minutes at one record/second.
ROLLING_TREND_MAX = 600
ANNOTATION_MAX = 400

ACTIVE = "active"
ENDED = "ended"


class SignalStreamError(ValueError):
    """Raised for invalid live-stream operations (not found, closed, bad path)."""


async def ensure_signal_stream_indexes(database: AsyncIOMotorDatabase) -> None:
    await database[COLLECTION].create_index("stream_id", unique=True, name="signal_stream_id")
    await database[COLLECTION].create_index([("tenant_id", 1), ("updated_at", -1)], name="signal_stream_recent")
    await database[COLLECTION].create_index([("case_id", 1), ("updated_at", -1)], name="signal_stream_case")


# --------------------------------------------------------------------------- #
# Layout helpers
# --------------------------------------------------------------------------- #
def _compact_signals(layout: dict[str, Any]) -> list[dict[str, Any]]:
    """Project the parsed layout into the minimal per-signal fields that
    :func:`decode_data_region` needs, in a Mongo-serialisable form."""
    compact: list[dict[str, Any]] = []
    for sig in layout["signals"]:
        entry: dict[str, Any] = {
            "index": sig["index"],
            "label": sig["label"],
            "dimension": sig.get("dimension", ""),
            "samples_per_record": sig["samples_per_record"],
            "is_annotation": sig["is_annotation"],
        }
        if not sig["is_annotation"]:
            entry["gain"] = sig["gain"]
            entry["physical_min"] = sig["physical_min"]
            entry["digital_min"] = sig["digital_min"]
        compact.append(entry)
    return compact


def _initial_channels(signals: list[dict[str, Any]], record_duration: float) -> dict[str, Any]:
    channels: dict[str, Any] = {}
    for sig in signals:
        if sig["is_annotation"]:
            continue
        dimension = sig["dimension"]
        channels[str(sig["index"])] = {
            "index": sig["index"],
            "label": sig["label"],
            "dimension": dimension,
            "sample_rate_hz": round(sig["samples_per_record"] / record_duration, 3) if record_duration else None,
            "uv_factor": uv_factor(dimension),
            "baseline_pp": None,
            "baseline_pp_uv": None,
            "latest_pp": None,
            "latest_pp_uv": None,
            "record_count": 0,
            "trend": [],
        }
    return channels


# --------------------------------------------------------------------------- #
# Core delta engine (pure on the doc; callers persist)
# --------------------------------------------------------------------------- #
def _apply_delta(doc: dict[str, Any], appended: bytes) -> dict[str, Any]:
    """Decode the new complete records in ``appended`` (joined with any carried
    partial record), extend per-channel trends and return the delta summary.

    Mutates ``doc`` in place: ``channels``, ``annotations``, ``record_cursor``
    and ``remainder_b64``. The caller is responsible for ``source_offset`` and
    persistence.
    """
    remainder = base64.b64decode(doc.get("remainder_b64") or "")
    buffer = remainder + appended
    signals = doc["signals"]
    bytes_per_record = doc["bytes_per_record"]

    decoded = decode_data_region(signals, bytes_per_record, buffer)
    consumed = decoded["consumed_bytes"]
    doc["remainder_b64"] = base64.b64encode(buffer[consumed:]).decode("ascii")

    delta_channels: dict[str, Any] = {}
    for idx_str, channel in doc["channels"].items():
        added = decoded["pp"].get(channel["index"]) or []
        if not added:
            continue
        factor = channel.get("uv_factor")
        if channel["baseline_pp"] is None:
            channel["baseline_pp"] = added[0]
            channel["baseline_pp_uv"] = round(added[0] * factor, 4) if factor is not None else None
        channel["latest_pp"] = added[-1]
        channel["latest_pp_uv"] = round(added[-1] * factor, 4) if factor is not None else None
        channel["record_count"] += len(added)
        trend = (channel.get("trend") or []) + added
        if len(trend) > ROLLING_TREND_MAX:
            trend = trend[-ROLLING_TREND_MAX:]
        channel["trend"] = trend
        delta_channels[idx_str] = {
            "index": channel["index"],
            "label": channel["label"],
            "added": added,
            "added_uv": [round(value * factor, 4) for value in added] if factor is not None else None,
            "baseline_pp": channel["baseline_pp"],
            "baseline_pp_uv": channel["baseline_pp_uv"],
            "latest_pp": channel["latest_pp"],
            "latest_pp_uv": channel["latest_pp_uv"],
            "record_count": channel["record_count"],
        }

    if decoded["annotations"]:
        annotations = (doc.get("annotations") or []) + decoded["annotations"]
        if len(annotations) > ANNOTATION_MAX:
            annotations = annotations[-ANNOTATION_MAX:]
        doc["annotations"] = annotations

    doc["record_cursor"] += decoded["complete_records"]
    return {
        "new_records": decoded["complete_records"],
        "channels": delta_channels,
        "annotations": decoded["annotations"],
    }


# --------------------------------------------------------------------------- #
# Public projection
# --------------------------------------------------------------------------- #
def _public_stream(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "stream_id": doc["stream_id"],
        "status": doc["status"],
        "case_id": doc.get("case_id"),
        "node_key": doc.get("node_key"),
        "source_label": doc.get("source_label"),
        "format": doc.get("format"),
        "record_duration_s": doc.get("record_duration_s"),
        "signal_count": doc.get("signal_count"),
        "source_offset": doc.get("source_offset"),
        "record_cursor": doc.get("record_cursor"),
        "created_by_name": doc.get("created_by_name"),
        "channels": list(doc.get("channels", {}).values()),
        "annotations": doc.get("annotations", []),
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
        "ended_at": _iso(doc.get("ended_at")),
    }


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return None


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
async def open_stream(
    database: AsyncIOMotorDatabase,
    *,
    opening_chunk: bytes,
    identity: dict[str, Any],
    case_id: str | None = None,
    node_key: str | None = None,
    source_label: str | None = None,
    file_path: str | None = None,
) -> dict[str, Any]:
    """Begin a live stream from an opening chunk that contains the EDF header
    (and optionally some initial data records)."""
    layout = edf_layout(opening_chunk)
    signals = _compact_signals(layout)
    record_duration = layout["header"]["record_duration"]
    now = datetime.now(UTC)
    doc: dict[str, Any] = {
        "stream_id": secrets.token_hex(8),
        "tenant_id": identity["tenant_id"],
        "status": ACTIVE,
        "case_id": case_id,
        "node_key": node_key,
        "source_label": source_label,
        "file_path": file_path,
        "created_by": identity["user_id"],
        "created_by_name": identity["display_name"],
        "created_role": identity.get("role"),
        "format": layout["header"]["edf_plus"] or "EDF",
        "record_duration_s": record_duration,
        "signal_count": len([s for s in signals if not s["is_annotation"]]),
        "header_bytes": layout["header_bytes"],
        "bytes_per_record": layout["bytes_per_record"],
        "signals": signals,
        "source_offset": layout["header_bytes"],
        "record_cursor": 0,
        "remainder_b64": "",
        "channels": _initial_channels(signals, record_duration),
        "annotations": [],
        "created_at": now,
        "updated_at": now,
        "ended_at": None,
    }

    initial = opening_chunk[layout["header_bytes"]:]
    if initial:
        _apply_delta(doc, initial)
    doc["source_offset"] = len(opening_chunk)

    await database[COLLECTION].insert_one(dict(doc))
    return _public_stream(doc)


async def open_stream_from_file(
    database: AsyncIOMotorDatabase,
    *,
    path: str,
    identity: dict[str, Any],
    case_id: str | None = None,
    node_key: str | None = None,
    source_label: str | None = None,
) -> dict[str, Any]:
    """Begin a live stream from a file the server can read (server-side tailing)."""
    resolved = _validated_stream_path(path)
    if not resolved.is_file():
        raise SignalStreamError("Source file does not exist.")
    raw = resolved.read_bytes()
    return await open_stream(
        database,
        opening_chunk=raw,
        identity=identity,
        case_id=case_id,
        node_key=node_key,
        source_label=source_label or resolved.name,
        file_path=str(resolved),
    )


async def append_bytes(
    database: AsyncIOMotorDatabase,
    stream_id: str,
    appended: bytes,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Push transport: ingest a byte delta appended to the recording."""
    doc = await _load_active(database, stream_id, tenant_id)
    delta = _apply_delta(doc, appended)
    doc["source_offset"] += len(appended)
    doc["updated_at"] = datetime.now(UTC)
    await database[COLLECTION].replace_one({"stream_id": stream_id}, doc)
    return _delta_envelope(doc, delta)


async def tail_file(
    database: AsyncIOMotorDatabase,
    stream_id: str,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Server-side transport: read any new bytes the acquisition box has written
    to the source file since the last consumed offset."""
    doc = await _load_active(database, stream_id, tenant_id)
    file_path = doc.get("file_path")
    if not file_path:
        raise SignalStreamError("This stream was not opened from a server-readable file.")
    resolved = _validated_stream_path(file_path)
    if not resolved.is_file():
        raise SignalStreamError("Source file is no longer available.")
    size = resolved.stat().st_size
    start = doc["source_offset"]
    if size < start:
        raise SignalStreamError("Source file shrank below the consumed offset (rotated or truncated).")
    if size == start:
        return _delta_envelope(doc, {"new_records": 0, "channels": {}, "annotations": []})
    with resolved.open("rb") as handle:
        handle.seek(start)
        appended = handle.read(size - start)
    delta = _apply_delta(doc, appended)
    doc["source_offset"] += len(appended)
    doc["updated_at"] = datetime.now(UTC)
    await database[COLLECTION].replace_one({"stream_id": stream_id}, doc)
    return _delta_envelope(doc, delta)


async def get_stream(
    database: AsyncIOMotorDatabase,
    stream_id: str,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    doc = await database[COLLECTION].find_one({"stream_id": stream_id, "tenant_id": tenant_id})
    if not doc:
        raise SignalStreamError("Live signal stream not found.")
    return _public_stream(doc)


async def close_stream(
    database: AsyncIOMotorDatabase,
    stream_id: str,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    doc = await database[COLLECTION].find_one({"stream_id": stream_id, "tenant_id": tenant_id})
    if not doc:
        raise SignalStreamError("Live signal stream not found.")
    if doc["status"] != ENDED:
        doc["status"] = ENDED
        doc["ended_at"] = datetime.now(UTC)
        doc["updated_at"] = doc["ended_at"]
        await database[COLLECTION].replace_one({"stream_id": stream_id}, doc)
    return _public_stream(doc)


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _delta_envelope(doc: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    return {
        "stream_id": doc["stream_id"],
        "status": doc["status"],
        "source_offset": doc["source_offset"],
        "record_cursor": doc["record_cursor"],
        "new_records": delta["new_records"],
        "channels": delta["channels"],
        "annotations": delta["annotations"],
    }


async def _load_active(
    database: AsyncIOMotorDatabase,
    stream_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    doc = await database[COLLECTION].find_one({"stream_id": stream_id, "tenant_id": tenant_id})
    if not doc:
        raise SignalStreamError("Live signal stream not found.")
    if doc.get("status") != ACTIVE:
        raise SignalStreamError("This live signal stream is no longer active.")
    return doc


def _validated_stream_path(path: str) -> Path:
    base = settings.signal_stream_path
    if base is None:
        raise SignalStreamError("Server-side file tailing is not enabled (IONM_SIGNAL_STREAM_DIR unset).")
    base_resolved = base.resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = base_resolved / candidate
    candidate = candidate.resolve()
    if candidate != base_resolved and base_resolved not in candidate.parents:
        raise SignalStreamError("Source path escapes the configured signal stream directory.")
    return candidate
