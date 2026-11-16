"""Round-trip tests for the vendor-neutral EDF/EDF+ reader.

The EDF bytes here are synthesised by a small spec-compliant writer so the parser
is validated against real, fully-decoded recordings (legitimate test fixtures,
not production clinical data).
"""

import pytest

from backend.app.services.alert_engine import analyze_signal
from backend.app.services.signal_import import EdfParseError, parse_edf


def _fld(value, width):
    return str(value)[:width].ljust(width).encode("ascii")


def build_edf(signals, data_records, record_duration=1.0, reserved=""):
    ns = len(signals)
    num_records = len(data_records)
    header = bytearray()
    header += _fld("0", 8)
    header += _fld("X X X Patient", 80)
    header += _fld("Startdate 01-JAN-2024 Cadwell export", 80)
    header += _fld("01.01.24", 8)
    header += _fld("12.00.00", 8)
    header += _fld(256 * (ns + 1), 8)
    header += _fld(reserved, 44)
    header += _fld(num_records, 8)
    header += _fld(record_duration, 8)
    header += _fld(ns, 4)

    def col(key, width, default=""):
        for sig in signals:
            header_local.append(_fld(sig.get(key, default), width))

    header_local: list[bytes] = []
    col("label", 16)
    col("transducer", 80, "AgAgCl electrode")
    col("dimension", 8, "uV")
    col("physical_min", 8, "-100")
    col("physical_max", 8, "100")
    col("digital_min", 8, "-32768")
    col("digital_max", 8, "32767")
    col("prefilter", 80, "HP 30 Hz LP 1500 Hz")
    col("samples_per_record", 8)
    col("reserved", 32, "")
    header += b"".join(header_local)

    body = bytearray()
    for record in data_records:
        for sig, payload in zip(signals, record):
            if isinstance(payload, (bytes, bytearray)):
                expected = sig["samples_per_record"] * 2
                body += bytes(payload).ljust(expected, b"\x00")[:expected]
            else:
                for sample in payload:
                    body += int(sample).to_bytes(2, "little", signed=True)
    return bytes(header) + bytes(body)


def _gain(phys_min=-100, phys_max=100, dig_min=-32768, dig_max=32767):
    return (phys_max - phys_min) / (dig_max - dig_min)


def test_parses_ordinary_channel_and_amplitude_trend():
    signal = {"label": "SSEP L cortical", "dimension": "uV", "samples_per_record": 4}
    records = [
        [[330, -330, 200, -200]],
        [[230, -230, 100, -100]],
        [[100, -100, 50, -50]],
    ]
    parsed = parse_edf(build_edf([signal], records))

    assert parsed["format"] == "EDF"
    assert parsed["signal_count"] == 1
    assert parsed["record_count"] == 3
    assert parsed["duration_s"] == 3.0

    channel = parsed["channels"][0]
    assert channel["label"] == "SSEP L cortical"
    assert channel["sample_rate_hz"] == 4.0
    assert len(channel["pp_amplitude_trend"]) == 3
    assert channel["baseline_pp_amplitude"] > channel["latest_pp_amplitude"]

    expected_baseline_uv = round(660 * _gain(), 4)
    assert channel["baseline_pp_amplitude_uv"] == pytest.approx(expected_baseline_uv, abs=0.01)


def test_millivolt_dimension_converts_to_microvolts():
    signal = {"label": "MEP", "dimension": "mV", "samples_per_record": 2, "physical_min": "-10", "physical_max": "10"}
    records = [[[1000, -1000]]]
    parsed = parse_edf(build_edf([signal], records))
    channel = parsed["channels"][0]
    native = channel["baseline_pp_amplitude"]
    assert channel["baseline_pp_amplitude_uv"] == pytest.approx(native * 1000.0, abs=0.01)


def test_edf_plus_annotations_are_parsed():
    ordinary = {"label": "TcMEP APB", "dimension": "uV", "samples_per_record": 2}
    annotation = {"label": "EDF Annotations", "dimension": "", "samples_per_record": 30}
    # Record 0: timekeeping TAL only. Record 1: timekeeping + an event TAL.
    tal0 = b"+0\x14\x14\x00"
    tal1 = b"+1\x14\x14\x00" + b"+1.5\x14Skin incision\x14\x00"
    records = [
        [[100, -100], tal0],
        [[80, -80], tal1],
    ]
    parsed = parse_edf(build_edf([ordinary, annotation], records, reserved="EDF+C"))

    assert parsed["format"] == "EDF+C"
    assert parsed["signal_count"] == 1  # annotation channel excluded from signal_count
    texts = [item["text"] for item in parsed["annotations"]]
    assert "Skin incision" in texts
    event = next(item for item in parsed["annotations"] if item["text"] == "Skin incision")
    assert event["onset_s"] == 1.5


def test_imported_amplitude_drop_feeds_alert_engine():
    signal = {"label": "SSEP L cortical", "dimension": "uV", "samples_per_record": 2}
    records = [[[330, -330]], [[100, -100]]]
    parsed = parse_edf(build_edf([signal], records))
    channel = parsed["channels"][0]

    result = analyze_signal(
        family="ssep_upper",
        baseline={"amplitude_uv": channel["baseline_pp_amplitude_uv"]},
        current={"amplitude_uv": channel["latest_pp_amplitude_uv"]},
    )
    assert result["severity"] in {"warning", "critical"}
    assert result["metrics"]["amplitude_drop_percent"] >= 50


def test_rejects_too_short_file():
    with pytest.raises(EdfParseError):
        parse_edf(b"not an edf")


def test_rejects_zero_signal_header():
    header = bytearray()
    header += _fld("0", 8)
    header += _fld("", 80)
    header += _fld("", 80)
    header += _fld("01.01.24", 8)
    header += _fld("12.00.00", 8)
    header += _fld(256, 8)
    header += _fld("", 44)
    header += _fld(1, 8)
    header += _fld(1.0, 8)
    header += _fld(0, 4)  # zero signals
    with pytest.raises(EdfParseError):
        parse_edf(bytes(header))
