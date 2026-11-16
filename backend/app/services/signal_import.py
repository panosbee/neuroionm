"""Vendor-neutral signal ingestion.

A real, dependency-free reader for the European Data Format (EDF / EDF+), the
open interchange format that Cadwell, Natus/Nicolet, IONOMED and most clinical
neurophysiology systems can export. The parser follows the published EDF
specification (Kemp et al.) and the EDF+ extension (Kemp & Olivan):

* 256-byte ASCII main header.
* ns * 256-byte ASCII signal headers.
* Data records of int16 little-endian samples, scaled to physical units with
  physical = (digital - digital_min) * gain + physical_min.
* EDF+ "EDF Annotations" channel carrying Time-stamped Annotation Lists (TAL).

Nothing here is mocked: every value returned is decoded from the uploaded bytes.
For each ordinary channel we compute the real per-record peak-to-peak amplitude,
which gives a genuine amplitude trend (first record = baseline, last record =
current) that the alert engine can evaluate.
"""

from __future__ import annotations

import sys
from array import array
from typing import Any


MAIN_HEADER_BYTES = 256
SIGNAL_HEADER_BYTES = 256
ANNOTATION_LABEL = "EDF Annotations"

# Conversion of a channel's physical dimension to microvolts, when recognised.
_UV_FACTOR = {
    "uv": 1.0,
    "µv": 1.0,
    "\u00b5v": 1.0,
    "mv": 1_000.0,
    "v": 1_000_000.0,
}


class EdfParseError(ValueError):
    """Raised when the uploaded bytes are not a valid EDF/EDF+ stream."""


def parse_edf(raw: bytes) -> dict[str, Any]:
    if len(raw) < MAIN_HEADER_BYTES:
        raise EdfParseError("File is smaller than a valid EDF header (256 bytes).")

    header = _parse_main_header(raw)
    ns = header["signal_count"]
    expected_header_bytes = MAIN_HEADER_BYTES + ns * SIGNAL_HEADER_BYTES
    if header["header_bytes"] not in (0, expected_header_bytes):
        # Some writers store 0; otherwise it must match ns.
        raise EdfParseError(
            f"Header byte count {header['header_bytes']} does not match {ns} signals."
        )
    header_bytes = expected_header_bytes
    if len(raw) < header_bytes:
        raise EdfParseError("File is truncated inside the signal header block.")

    signals = _parse_signal_headers(raw, ns)
    bytes_per_record = sum(sig["samples_per_record"] for sig in signals) * 2
    if bytes_per_record <= 0:
        raise EdfParseError("Signal header declares zero samples per data record.")

    data = raw[header_bytes:]
    num_records = header["num_records"]
    if num_records < 0:
        num_records = len(data) // bytes_per_record
    if num_records <= 0:
        raise EdfParseError("No complete data records are present after the header.")

    channels = _decode_records(data, signals, num_records, bytes_per_record, header["record_duration"])
    annotations = _collect_annotations(data, signals, num_records, bytes_per_record)

    duration_s = round(num_records * header["record_duration"], 3)
    return {
        "format": header["edf_plus"] or "EDF",
        "version": header["version"],
        "patient": header["patient"],
        "recording": header["recording"],
        "start_date": header["start_date"],
        "start_time": header["start_time"],
        "record_duration_s": header["record_duration"],
        "record_count": num_records,
        "duration_s": duration_s,
        "signal_count": len([sig for sig in signals if not sig["is_annotation"]]),
        "channels": channels,
        "annotations": annotations,
    }


# --------------------------------------------------------------------------- #
# Incremental (live / file-tailing) decoding
# --------------------------------------------------------------------------- #
# `parse_edf` above is the one-shot Stage A reader for a finished file. The
# functions below provide Stage B: decode a recording that is still being
# written, one byte-delta at a time. The header is parsed exactly once (`edf_layout`);
# every subsequent slice of data bytes is turned into only the NEW complete data
# records via `decode_data_region`, leaving any trailing partial record for the
# next delta.

def edf_layout(raw: bytes) -> dict[str, Any]:
    """Parse the header + signal headers needed to decode records incrementally.

    Returns the static layout (header summary, per-signal decode parameters,
    header byte length and bytes-per-record). `raw` must contain at least the
    full header block; the data section may be partial or absent.
    """
    if len(raw) < MAIN_HEADER_BYTES:
        raise EdfParseError("Opening chunk is smaller than a valid EDF header (256 bytes).")
    header = _parse_main_header(raw)
    ns = header["signal_count"]
    expected_header_bytes = MAIN_HEADER_BYTES + ns * SIGNAL_HEADER_BYTES
    if header["header_bytes"] not in (0, expected_header_bytes):
        raise EdfParseError(
            f"Header byte count {header['header_bytes']} does not match {ns} signals."
        )
    if len(raw) < expected_header_bytes:
        raise EdfParseError("Opening chunk is truncated inside the signal header block.")
    signals = _parse_signal_headers(raw, ns)
    bytes_per_record = sum(sig["samples_per_record"] for sig in signals) * 2
    if bytes_per_record <= 0:
        raise EdfParseError("Signal header declares zero samples per data record.")
    return {
        "header": header,
        "signals": signals,
        "header_bytes": expected_header_bytes,
        "bytes_per_record": bytes_per_record,
    }


def uv_factor(dimension: str | None) -> float | None:
    """Microvolt conversion factor for a recognised physical dimension, else None."""
    return _UV_FACTOR.get((dimension or "").strip().lower())


def _channel_offsets(signals: list[dict[str, Any]]) -> list[int]:
    offsets: list[int] = []
    cursor = 0
    for sig in signals:
        offsets.append(cursor)
        cursor += sig["samples_per_record"] * 2
    return offsets


def _record_peak_to_peak(chunk: bytes, sig: dict[str, Any], little_endian: bool) -> float:
    samples = array("h")
    samples.frombytes(chunk)
    if not little_endian:
        samples.byteswap()
    digital_low = min(samples)
    digital_high = max(samples)
    offset = sig["physical_min"] - sig["digital_min"] * sig["gain"]
    physical_low = digital_low * sig["gain"] + offset
    physical_high = digital_high * sig["gain"] + offset
    return abs(physical_high - physical_low)


def decode_data_region(
    signals: list[dict[str, Any]],
    bytes_per_record: int,
    region: bytes,
) -> dict[str, Any]:
    """Decode every COMPLETE data record in a header-less EDF data-section slice.

    Only whole records are decoded; any trailing partial record is reported via
    ``consumed_bytes`` so the caller can carry it forward to the next delta.

    Returns per-ordinary-channel peak-to-peak amplitude lists (physical units,
    keyed by signal index), EDF+ annotations found in the slice, the number of
    complete records decoded and the number of bytes consumed.
    """
    little_endian = sys.byteorder == "little"
    offsets = _channel_offsets(signals)
    complete = len(region) // bytes_per_record if bytes_per_record else 0
    pp: dict[int, list[float]] = {sig["index"]: [] for sig in signals if not sig["is_annotation"]}
    annotations: list[dict[str, Any]] = []
    for record in range(complete):
        base = record * bytes_per_record
        for sig, sig_offset in zip(signals, offsets):
            start = base + sig_offset
            end = start + sig["samples_per_record"] * 2
            chunk = region[start:end]
            if sig["is_annotation"]:
                annotations.extend(_parse_tal(chunk))
                continue
            pp[sig["index"]].append(round(_record_peak_to_peak(chunk, sig, little_endian), 4))
    return {
        "pp": pp,
        "annotations": annotations,
        "complete_records": complete,
        "consumed_bytes": complete * bytes_per_record,
    }


def extract_channel_sweeps(raw: bytes, channel_index: int) -> dict[str, Any]:
    """Decode one ordinary channel into its per-record physical-unit sweeps.

    Each EDF data record holds one sweep of the channel; this returns the full
    physical-unit sample waveform of every record so the DSP layer can measure
    peak-to-peak amplitude, peak/onset latency and inter-sweep latency shift.
    Unlike `parse_edf` (which only keeps per-record peak-to-peak) this preserves
    the waveform shape; it is computed on demand and never persisted.
    """
    layout = edf_layout(raw)
    signals = layout["signals"]
    header_bytes = layout["header_bytes"]
    bytes_per_record = layout["bytes_per_record"]
    record_duration = layout["header"]["record_duration"]
    data = raw[header_bytes:]

    target = None
    target_offset = 0
    cursor = 0
    for sig in signals:
        if sig["index"] == channel_index:
            target = sig
            target_offset = cursor
            break
        cursor += sig["samples_per_record"] * 2
    if target is None:
        raise EdfParseError(f"Channel index {channel_index} is not present in the recording.")
    if target["is_annotation"]:
        raise EdfParseError(f"Channel index {channel_index} is an annotation channel, not a signal.")

    little_endian = sys.byteorder == "little"
    spr = target["samples_per_record"]
    gain = target["gain"]
    base_offset = target["physical_min"] - target["digital_min"] * gain
    num_records = len(data) // bytes_per_record

    sweeps: list[list[float]] = []
    for record in range(num_records):
        start = record * bytes_per_record + target_offset
        end = start + spr * 2
        samples = array("h")
        samples.frombytes(data[start:end])
        if not little_endian:
            samples.byteswap()
        sweeps.append([sample * gain + base_offset for sample in samples])

    return {
        "index": channel_index,
        "label": target["label"],
        "dimension": target["dimension"],
        "sample_rate_hz": round(spr / record_duration, 3) if record_duration else None,
        "uv_factor": uv_factor(target["dimension"]),
        "samples_per_record": spr,
        "sweeps": sweeps,
    }


def _parse_main_header(raw: bytes) -> dict[str, Any]:
    head = raw[:MAIN_HEADER_BYTES]
    try:
        version = _ascii(head[0:8])
        patient = _ascii(head[8:88])
        recording = _ascii(head[88:168])
        start_date = _ascii(head[168:176])
        start_time = _ascii(head[176:184])
        header_bytes = _int(head[184:192])
        reserved = _ascii(head[192:236])
        num_records = _int(head[236:244])
        record_duration = _float(head[244:252])
        signal_count = _int(head[252:256])
    except ValueError as error:
        raise EdfParseError(f"Malformed EDF main header: {error}") from error

    if signal_count <= 0:
        raise EdfParseError("EDF header declares zero signals.")
    if record_duration <= 0:
        raise EdfParseError("EDF header declares a non-positive record duration.")

    edf_plus = None
    marker = reserved.strip().upper()
    if marker.startswith("EDF+C"):
        edf_plus = "EDF+C"
    elif marker.startswith("EDF+D"):
        edf_plus = "EDF+D"

    return {
        "version": version,
        "patient": patient,
        "recording": recording,
        "start_date": start_date,
        "start_time": start_time,
        "header_bytes": header_bytes,
        "num_records": num_records,
        "record_duration": record_duration,
        "signal_count": signal_count,
        "edf_plus": edf_plus,
    }


def _parse_signal_headers(raw: bytes, ns: int) -> list[dict[str, Any]]:
    block = raw[MAIN_HEADER_BYTES : MAIN_HEADER_BYTES + ns * SIGNAL_HEADER_BYTES]

    # EDF stores each signal-header field contiguously across all signals:
    # all labels (ns*16), then all transducers (ns*80), and so on. The byte
    # offset of a field is therefore the running total of prior field widths
    # multiplied by ns.
    cursor = 0

    def field(width: int) -> list[str]:
        nonlocal cursor
        start = cursor
        values = [_ascii(block[start + i * width : start + (i + 1) * width]) for i in range(ns)]
        cursor = start + ns * width
        return values

    labels = field(16)
    transducers = field(80)
    dimensions = field(8)
    phys_min = field(8)
    phys_max = field(8)
    dig_min = field(8)
    dig_max = field(8)
    prefilter = field(80)
    n_samples = field(8)

    signals: list[dict[str, Any]] = []
    for i in range(ns):
        label = labels[i]
        is_annotation = label == ANNOTATION_LABEL
        try:
            samples_per_record = int(n_samples[i])
        except ValueError as error:
            raise EdfParseError(f"Signal {i} has an invalid samples-per-record value.") from error
        if samples_per_record <= 0:
            raise EdfParseError(f"Signal {i} declares zero samples per data record.")

        signal: dict[str, Any] = {
            "index": i,
            "label": label,
            "transducer": transducers[i],
            "dimension": dimensions[i],
            "prefilter": prefilter[i],
            "samples_per_record": samples_per_record,
            "is_annotation": is_annotation,
        }
        if not is_annotation:
            try:
                signal["physical_min"] = float(phys_min[i])
                signal["physical_max"] = float(phys_max[i])
                signal["digital_min"] = float(dig_min[i])
                signal["digital_max"] = float(dig_max[i])
            except ValueError as error:
                raise EdfParseError(f"Signal {i} has invalid physical/digital range fields.") from error
            digital_span = signal["digital_max"] - signal["digital_min"]
            if digital_span == 0:
                raise EdfParseError(f"Signal {i} has a zero digital range.")
            signal["gain"] = (signal["physical_max"] - signal["physical_min"]) / digital_span
        signals.append(signal)
    return signals


def _decode_records(
    data: bytes,
    signals: list[dict[str, Any]],
    num_records: int,
    bytes_per_record: int,
    record_duration: float,
) -> list[dict[str, Any]]:
    little_endian = sys.byteorder == "little"
    # Per-channel per-record peak-to-peak amplitude in physical units.
    trends: dict[int, list[float]] = {sig["index"]: [] for sig in signals if not sig["is_annotation"]}

    offsets = []
    cursor = 0
    for sig in signals:
        offsets.append(cursor)
        cursor += sig["samples_per_record"] * 2

    for record in range(num_records):
        base = record * bytes_per_record
        if base + bytes_per_record > len(data):
            break
        for sig, sig_offset in zip(signals, offsets):
            if sig["is_annotation"]:
                continue
            start = base + sig_offset
            end = start + sig["samples_per_record"] * 2
            samples = array("h")
            samples.frombytes(data[start:end])
            if not little_endian:
                samples.byteswap()
            digital_low = min(samples)
            digital_high = max(samples)
            physical_low = digital_low * sig["gain"] + (sig["physical_min"] - sig["digital_min"] * sig["gain"])
            physical_high = digital_high * sig["gain"] + (sig["physical_min"] - sig["digital_min"] * sig["gain"])
            trends[sig["index"]].append(round(abs(physical_high - physical_low), 4))

    channels: list[dict[str, Any]] = []
    for sig in signals:
        if sig["is_annotation"]:
            continue
        trend = trends[sig["index"]]
        sample_rate = round(sig["samples_per_record"] / record_duration, 3)
        baseline_pp = trend[0] if trend else None
        latest_pp = trend[-1] if trend else None
        uv_factor = _UV_FACTOR.get(sig["dimension"].strip().lower())
        channel = {
            "index": sig["index"],
            "label": sig["label"],
            "transducer": sig["transducer"],
            "dimension": sig["dimension"],
            "prefilter": sig["prefilter"],
            "sample_rate_hz": sample_rate,
            "samples_per_record": sig["samples_per_record"],
            "physical_min": sig["physical_min"],
            "physical_max": sig["physical_max"],
            "baseline_pp_amplitude": baseline_pp,
            "latest_pp_amplitude": latest_pp,
            "pp_amplitude_trend": trend,
        }
        if uv_factor is not None:
            channel["baseline_pp_amplitude_uv"] = round(baseline_pp * uv_factor, 4) if baseline_pp is not None else None
            channel["latest_pp_amplitude_uv"] = round(latest_pp * uv_factor, 4) if latest_pp is not None else None
        channels.append(channel)
    return channels


def _collect_annotations(
    data: bytes,
    signals: list[dict[str, Any]],
    num_records: int,
    bytes_per_record: int,
) -> list[dict[str, Any]]:
    annotation_signals = [sig for sig in signals if sig["is_annotation"]]
    if not annotation_signals:
        return []

    offsets = []
    cursor = 0
    for sig in signals:
        offsets.append(cursor)
        cursor += sig["samples_per_record"] * 2

    annotations: list[dict[str, Any]] = []
    for record in range(num_records):
        base = record * bytes_per_record
        if base + bytes_per_record > len(data):
            break
        for sig, sig_offset in zip(signals, offsets):
            if not sig["is_annotation"]:
                continue
            start = base + sig_offset
            end = start + sig["samples_per_record"] * 2
            annotations.extend(_parse_tal(data[start:end]))
    return annotations


def _parse_tal(chunk: bytes) -> list[dict[str, Any]]:
    """Parse a Time-stamped Annotation List block (EDF+).

    Each TAL: onset[<0x15>duration]<0x14>text[<0x14>text...]<0x14><0x00>.
    The first TAL of every record is the record timekeeping marker (empty text).
    """
    results: list[dict[str, Any]] = []
    for tal in chunk.split(b"\x00"):
        if not tal.strip(b"\x14\x15 "):
            continue
        head, _, rest = tal.partition(b"\x14")
        onset_field, _, duration_field = head.partition(b"\x15")
        try:
            onset = float(onset_field.decode("ascii", "ignore"))
        except ValueError:
            continue
        duration = None
        if duration_field:
            try:
                duration = float(duration_field.decode("ascii", "ignore"))
            except ValueError:
                duration = None
        texts = [part.decode("utf-8", "ignore").strip() for part in rest.split(b"\x14") if part.strip()]
        for text in texts:
            results.append({"onset_s": round(onset, 3), "duration_s": duration, "text": text})
    return results


def _ascii(chunk: bytes) -> str:
    return chunk.decode("ascii", "replace").strip()


def _int(chunk: bytes) -> int:
    text = chunk.decode("ascii", "replace").strip()
    if not text:
        return 0
    return int(text)


def _float(chunk: bytes) -> float:
    text = chunk.decode("ascii", "replace").strip()
    if not text:
        return 0.0
    return float(text)
