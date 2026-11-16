"""Tests for Stage B incremental (live / file-tailing) EDF ingestion.

These validate the pure delta engine: that decoding a recording in arbitrary
byte chunks — including chunks that split a data record in half — yields exactly
the same per-channel amplitude trend as the one-shot Stage A reader, and that
EDF+ annotations are recovered from a partial stream.
"""

import pytest

from backend.app.services.signal_import import (
    decode_data_region,
    edf_layout,
    parse_edf,
)
from backend.app.services.signal_stream import (
    SignalStreamError,
    _apply_delta,
    _compact_signals,
    _initial_channels,
    _validated_stream_path,
)

from test_signal_import import build_edf


def _new_doc(full: bytes) -> dict:
    """Build the in-memory stream doc the way `open_stream` would, without Mongo."""
    layout = edf_layout(full)
    signals = _compact_signals(layout)
    record_duration = layout["header"]["record_duration"]
    return {
        "signals": signals,
        "bytes_per_record": layout["bytes_per_record"],
        "header_bytes": layout["header_bytes"],
        "record_cursor": 0,
        "remainder_b64": "",
        "annotations": [],
        "channels": _initial_channels(signals, record_duration),
    }


def _feed(doc: dict, data: bytes, chunk_size: int) -> list[dict]:
    deltas = []
    for start in range(0, len(data), chunk_size):
        deltas.append(_apply_delta(doc, data[start : start + chunk_size]))
    return deltas


def test_incremental_matches_one_shot_for_split_records():
    signal = {"label": "SSEP L cortical", "dimension": "uV", "samples_per_record": 4}
    records = [
        [[330, -330, 200, -200]],
        [[230, -230, 100, -100]],
        [[180, -180, 90, -90]],
        [[100, -100, 50, -50]],
    ]
    full = build_edf([signal], records)
    one_shot = parse_edf(full)["channels"][0]

    layout = edf_layout(full)
    header_bytes = layout["header_bytes"]
    data = full[header_bytes:]
    bytes_per_record = layout["bytes_per_record"]

    # Feed three bytes at a time so most records straddle a chunk boundary.
    assert 3 < bytes_per_record
    doc = _new_doc(full)
    _feed(doc, data, chunk_size=3)

    assert doc["record_cursor"] == 4
    assert doc["remainder_b64"] == ""  # all records consumed, nothing left over
    channel = doc["channels"]["0"]
    assert channel["trend"] == one_shot["pp_amplitude_trend"]
    assert channel["baseline_pp"] == one_shot["baseline_pp_amplitude"]
    assert channel["latest_pp"] == one_shot["latest_pp_amplitude"]
    assert channel["baseline_pp_uv"] == one_shot["baseline_pp_amplitude_uv"]
    assert channel["latest_pp_uv"] == one_shot["latest_pp_amplitude_uv"]
    assert channel["record_count"] == 4


def test_partial_record_is_carried_forward():
    signal = {"label": "TcMEP APB", "dimension": "uV", "samples_per_record": 3}
    records = [[[300, -300, 0]], [[120, -120, 0]]]
    full = build_edf([signal], records)
    layout = edf_layout(full)
    data = full[layout["header_bytes"]:]
    bytes_per_record = layout["bytes_per_record"]

    doc = _new_doc(full)
    # First delta: one full record plus a couple of bytes of the second.
    first = _apply_delta(doc, data[: bytes_per_record + 2])
    assert first["new_records"] == 1
    assert doc["record_cursor"] == 1
    assert doc["remainder_b64"] != ""  # leftover partial record buffered

    # Second delta: the rest of the second record completes it.
    second = _apply_delta(doc, data[bytes_per_record + 2 :])
    assert second["new_records"] == 1
    assert doc["record_cursor"] == 2

    one_shot = parse_edf(full)["channels"][0]
    assert doc["channels"]["0"]["trend"] == one_shot["pp_amplitude_trend"]


def test_decode_data_region_reports_consumed_bytes():
    signal = {"label": "BAEP", "dimension": "uV", "samples_per_record": 2}
    records = [[[200, -200]], [[150, -150]]]
    full = build_edf([signal], records)
    layout = edf_layout(full)
    data = full[layout["header_bytes"]:]
    bpr = layout["bytes_per_record"]

    # Hand it 1.5 records worth of bytes: only one complete record is decoded.
    decoded = decode_data_region(layout["signals"], bpr, data[: bpr + (bpr // 2)])
    assert decoded["complete_records"] == 1
    assert decoded["consumed_bytes"] == bpr


def test_annotations_recovered_from_incremental_stream():
    ordinary = {"label": "TcMEP APB", "dimension": "uV", "samples_per_record": 2}
    annotation = {"label": "EDF Annotations", "dimension": "", "samples_per_record": 30}
    tal0 = b"+0\x14\x14\x00"
    tal1 = b"+1\x14\x14\x00" + b"+1.5\x14Skin incision\x14\x00"
    records = [[[100, -100], tal0], [[80, -80], tal1]]
    full = build_edf([ordinary, annotation], records, reserved="EDF+C")
    layout = edf_layout(full)
    data = full[layout["header_bytes"]:]

    doc = _new_doc(full)
    _feed(doc, data, chunk_size=5)

    texts = [item["text"] for item in doc["annotations"]]
    assert "Skin incision" in texts


def test_file_tail_disabled_without_configured_dir():
    with pytest.raises(SignalStreamError):
        _validated_stream_path("anything.edf")
