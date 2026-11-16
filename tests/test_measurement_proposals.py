"""Tests for auto-extracted measurement proposals + human confirmation (item #10).

A real EDF is decoded and the DSP layer proposes baseline/current measurements;
a clinician then confirms or corrects them. The confirmed values drive the
deterministic verdict and the proposed-vs-confirmed delta is stored as a training
signal. A tiny in-memory async Mongo stand-in drives the real service code — no
mocks of the measurement logic itself.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.app.services import measurement_proposals as mp
from backend.app.services.measurement_proposals import (
    MeasurementProposalError,
    confirm_measurement,
    propose_from_recording,
    propose_from_sweeps,
)

from test_signal_import import build_edf


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def create_index(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def insert_one(self, document: dict[str, Any]) -> None:
        self.docs.append(dict(document))


class FakeDB:
    def __init__(self) -> None:
        self._cols: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._cols.setdefault(name, FakeCollection())


def _ramp(n: int, peak: float, center: int) -> list[float]:
    import math

    return [peak * math.exp(-((i - center) ** 2) / (2.0 * 16.0)) for i in range(n)]


def _edf_with_two_sweeps() -> bytes:
    # 200 samples/record at 0.05 s/record -> 4000 Hz. Record 0 has a big N20-like
    # peak near 20 ms (sample 80), record 1 a much smaller one.
    signal = {"label": "Median SSEP L", "dimension": "uV", "samples_per_record": 200,
              "physical_min": "-500", "physical_max": "500"}
    gain = 1000.0 / 65535.0
    baseline = [int(round(v / gain)) for v in _ramp(200, 60.0, 80)]
    current = [int(round(v / gain)) for v in _ramp(200, 12.0, 80)]
    return build_edf([signal], [[baseline], [current]], record_duration=0.05)


def test_propose_from_recording_prefills_measurements():
    raw = _edf_with_two_sweeps()
    proposal = propose_from_recording(raw, channel_index=0, profile="ssep_median",
                                      baseline_record=0, current_record=1)
    assert proposal["status"] == "proposed"
    assert proposal["needs_confirmation"] is True
    assert proposal["confidence"] == mp.PROPOSAL_CONFIDENCE
    m = proposal["measurement"]
    assert m["baseline"]["amplitude_uv"] > m["current"]["amplitude_uv"]
    assert m["baseline"]["latency_ms"] == pytest.approx(20.0, abs=0.5)
    assert m["derived"]["amplitude_drop_percent"] > 50.0


def test_propose_rejects_unknown_profile():
    with pytest.raises(MeasurementProposalError):
        propose_from_sweeps([0.0, 1.0], [0.0, 1.0], 4000.0, "bogus",
                            channel_label="x", uv_factor=1.0)


def test_propose_rejects_single_sweep():
    signal = {"label": "L", "dimension": "uV", "samples_per_record": 4}
    raw = build_edf([signal], [[[10, -10, 5, -5]]], record_duration=0.001)
    with pytest.raises(MeasurementProposalError):
        propose_from_recording(raw, channel_index=0, profile="ssep_median")


def test_confirm_accepts_proposal_as_is_records_no_correction():
    db = FakeDB()
    proposed = {"baseline": {"amplitude_uv": 2.0, "latency_ms": 20.0},
                "current": {"amplitude_uv": 0.3, "latency_ms": 22.0}}
    result = run(confirm_measurement(
        db,
        proposal_id="abc123",
        family="ssep_upper",
        proposed=proposed,
        confirmed_baseline={"amplitude_uv": 2.0, "latency_ms": 20.0},
        confirmed_current={"amplitude_uv": 0.3, "latency_ms": 22.0},
        identity={"user_id": "u1", "display_name": "Dr Test", "tenant_id": "default"},
        case_id="case-1",
        channel_label="Median SSEP L",
    ))
    assert result["status"] == "confirmed"
    assert result["corrections"]["any_corrected"] is False
    assert result["verdict"]["is_alert"] is True  # 85% drop -> critical
    assert len(db["measurement_confirmations"].docs) == 1


def test_confirm_records_correction_as_training_signal():
    db = FakeDB()
    proposed = {"baseline": {"amplitude_uv": 2.0, "latency_ms": 20.0},
                "current": {"amplitude_uv": 0.3, "latency_ms": 22.0}}
    # Clinician corrects the current amplitude upward (it was an under-read).
    result = run(confirm_measurement(
        db,
        proposal_id="abc124",
        family="ssep_upper",
        proposed=proposed,
        confirmed_baseline={"amplitude_uv": 2.0, "latency_ms": 20.0},
        confirmed_current={"amplitude_uv": 1.5, "latency_ms": 21.0},
        identity={"user_id": "u1", "display_name": "Dr Test", "tenant_id": "default"},
        case_id="case-2",
        channel_label="Median SSEP L",
    ))
    corr = result["corrections"]
    assert corr["any_corrected"] is True
    assert corr["current"]["amplitude_uv"]["corrected"] is True
    assert corr["current"]["amplitude_uv"]["delta"] == pytest.approx(1.2, abs=0.001)
    # Verdict uses the CONFIRMED (corrected) values: 25% drop -> not an alert.
    assert result["verdict"]["is_alert"] is False
    stored = db["measurement_confirmations"].docs[0]
    assert stored["confirmed"]["current"]["amplitude_uv"] == 1.5
    assert stored["proposed"]["current"]["amplitude_uv"] == 0.3


def test_confirm_requires_some_measurement():
    db = FakeDB()
    with pytest.raises(MeasurementProposalError):
        run(confirm_measurement(
            db,
            proposal_id="x",
            family="ssep_upper",
            proposed={},
            confirmed_baseline={},
            confirmed_current={},
            identity={"user_id": "u1", "tenant_id": "default"},
            case_id=None,
            channel_label=None,
        ))
