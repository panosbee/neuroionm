"""Tests for the artifact / anesthesia-context gate (item #9).

The gate must (a) detect mains line noise, broadband EMG and electrode-pop
transients from real DSP descriptors, (b) recommend holding an alert for
confirmation when a crossing looks artifact- or confound-driven, and (c) NEVER
mutate the published deterministic verdict.
"""

import math

import pytest

from backend.app.services.alert_engine import analyze_signal
from backend.app.services.artifact_gate import evaluate_signal_quality, gate_alert


def _sine(n, sample_rate, freq, amplitude=1.0):
    return [amplitude * math.sin(2.0 * math.pi * freq * i / sample_rate) for i in range(n)]


def _clean_sweep(n=400, sample_rate=4000.0):
    # Smooth low-frequency evoked-like response, no contamination.
    return _sine(n, sample_rate, 25.0, amplitude=20.0)


def test_detects_line_noise():
    sample_rate = 1000.0
    sweep = _sine(1000, sample_rate, 50.0, amplitude=20.0)
    quality = evaluate_signal_quality(sweep, sample_rate)
    assert quality["artifact_suspected"] is True
    assert quality["dominant_artifact"] == "line_noise"
    assert quality["flags"]["line_noise"] is True


def test_detects_electrode_pop_transient():
    sweep = _clean_sweep()
    sweep[200] = 5000.0  # single huge pop
    quality = evaluate_signal_quality(sweep, 4000.0)
    assert quality["flags"]["electrode_pop"] is True


def test_clean_sweep_is_trusted():
    quality = evaluate_signal_quality(_clean_sweep(), 4000.0)
    assert quality["artifact_suspected"] is False
    assert quality["dominant_artifact"] is None


def test_anesthesia_confound_flagged():
    quality = evaluate_signal_quality(
        _clean_sweep(),
        4000.0,
        anesthesia_context={"regimen": "inhalational", "mac": 1.2, "map_mmHg": 55},
    )
    assert quality["anesthesia"]["confound_suspected"] is True
    assert len(quality["anesthesia"]["factors"]) >= 2


def test_gate_holds_alert_on_artifact_without_mutating_verdict():
    alert = analyze_signal(
        family="ssep_upper",
        baseline={"amplitude_uv": 2.0},
        current={"amplitude_uv": 0.2},  # 90% drop -> critical
        channel_label="Median SSEP L",
    )
    assert alert["is_alert"] is True
    original_severity = alert["severity"]

    sample_rate = 1000.0
    noisy = _sine(1000, sample_rate, 50.0, amplitude=30.0)  # mains-dominated
    gated = gate_alert(alert, noisy, sample_rate)

    # Verdict preserved, gate is purely additive and advisory.
    assert gated["severity"] == original_severity
    assert gated["is_alert"] is True
    assert gated["quality_gate"]["recommendation"] == "hold_for_confirmation"
    assert gated["quality_gate"]["artifact_suspected"] is True


def test_gate_passes_clean_alert_through():
    alert = analyze_signal(
        family="ssep_upper",
        baseline={"amplitude_uv": 2.0},
        current={"amplitude_uv": 0.2},
        channel_label="Median SSEP L",
    )
    gated = gate_alert(alert, _clean_sweep(), 4000.0)
    assert gated["quality_gate"]["recommendation"] == "pass_through"
    assert gated["severity"] == alert["severity"]


def test_gate_no_alert_records_context_only():
    alert = analyze_signal(
        family="ssep_upper",
        baseline={"amplitude_uv": 2.0},
        current={"amplitude_uv": 1.95},  # negligible drop -> no alert
        channel_label="Median SSEP L",
    )
    assert alert["is_alert"] is False
    gated = gate_alert(alert, _clean_sweep(), 4000.0)
    assert gated["quality_gate"]["recommendation"] == "pass_through"
    assert "quality_gate" in gated
