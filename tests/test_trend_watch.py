"""Tests for the advisory slope watch-band (item #11).

`assess_amplitude_trend` judges the recent amplitude-drop trajectory and flags a
"trending toward warning" advisory before the single-point comparison would fire.
It must never emit a warning/critical verdict itself.
"""

import pytest

from backend.app.services.alert_engine import assess_amplitude_trend


def test_steady_decline_triggers_early_advisory():
    # SSEP warning line is a 50% amplitude drop. A baseline of 100 uV declining
    # 8 uV/sweep is projected to cross 50% within the horizon.
    trend = [100.0, 92.0, 84.0, 76.0, 68.0, 60.0]
    result = assess_amplitude_trend(family="ssep_upper", trend=trend, horizon=5)
    assert result["applicable"] is True
    assert result["status"] == "trending_toward_warning"
    assert result["warning_at_percent"] == 50.0
    assert result["slope_percent_per_sweep"] > 0
    assert 0 < result["sweeps_to_warning"] <= 5
    assert "advisory" in result["advisory"].lower()


def test_stable_trend_no_advisory():
    trend = [80.0, 80.5, 79.5, 80.2, 80.1]
    result = assess_amplitude_trend(family="ssep_upper", trend=trend)
    assert result["applicable"] is True
    assert result["status"] == "stable"
    assert result["sweeps_to_warning"] is None


def test_recovering_trend_is_stable():
    trend = [60.0, 70.0, 80.0, 90.0, 95.0]  # amplitude climbing back up
    result = assess_amplitude_trend(family="ssep_upper", trend=trend, baseline_amplitude=100.0)
    assert result["status"] == "stable"


def test_already_at_warning_line_defers_to_engine():
    trend = [100.0, 80.0, 60.0, 50.0, 45.0]  # last point is a 55% drop
    result = assess_amplitude_trend(family="ssep_upper", trend=trend, baseline_amplitude=100.0)
    assert result["status"] == "warning_reached"
    assert result["sweeps_to_warning"] == 0


def test_slow_decline_not_projected_to_cross():
    trend = [100.0, 99.0, 98.0, 97.0, 96.0]  # ~1%/sweep, far from 50%
    result = assess_amplitude_trend(family="ssep_upper", trend=trend, horizon=5)
    assert result["status"] == "declining"
    assert result["sweeps_to_warning"] > 5


def test_threshold_modality_not_applicable():
    # Triggered EMG is a threshold modality with no amplitude-drop rule.
    result = assess_amplitude_trend(family="temg", trend=[10.0, 9.0, 8.0, 7.0])
    assert result["applicable"] is False
    assert result["status"] == "not_applicable"


def test_insufficient_data():
    result = assess_amplitude_trend(family="ssep_upper", trend=[100.0, 90.0])
    assert result["applicable"] is False
    assert result["status"] == "insufficient_data"


def test_never_emits_verdict_severity():
    trend = [100.0, 80.0, 60.0, 40.0]
    result = assess_amplitude_trend(family="ssep_upper", trend=trend, baseline_amplitude=100.0)
    # The watch-band reports trend status, not the engine's severity verdict.
    assert result.get("severity") is None
    assert result.get("is_alert") is None
