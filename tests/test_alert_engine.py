import pytest

from backend.app.services.alert_engine import (
    ALERT_CRITERIA,
    DIFFERENTIAL_LIBRARY,
    analyze_signal,
    build_response_protocol,
)


def _severity(family, baseline=None, current=None, override=None, signature=None):
    result = analyze_signal(
        family=family,
        baseline=baseline,
        current=current,
        metrics_override=override,
        signature=signature,
    )
    return result["severity"], result


# --- SSEP amplitude criterion (>=50% warning, >=80% critical) ---------------

def test_ssep_amplitude_50_percent_is_warning():
    severity, result = _severity("ssep_upper", {"amplitude_uv": 2.0}, {"amplitude_uv": 1.0})
    assert severity == "warning"
    assert result["is_alert"] is True
    assert result["metrics"]["amplitude_drop_percent"] == 50.0


def test_ssep_amplitude_80_percent_is_critical():
    severity, _ = _severity("ssep_upper", {"amplitude_uv": 2.0}, {"amplitude_uv": 0.4})
    assert severity == "critical"


def test_ssep_amplitude_35_percent_is_watch_not_alert():
    severity, result = _severity("ssep_upper", {"amplitude_uv": 2.0}, {"amplitude_uv": 1.3})
    assert severity == "watch"
    assert result["is_alert"] is False


def test_ssep_amplitude_20_percent_is_within_limits():
    severity, _ = _severity("ssep_upper", {"amplitude_uv": 2.0}, {"amplitude_uv": 1.6})
    assert severity == "none"


# --- SSEP latency criterion (>=10% warning, >=20% critical) -----------------

def test_ssep_latency_10_percent_is_warning():
    severity, result = _severity("ssep_lower", {"latency_ms": 40.0}, {"latency_ms": 44.0})
    assert severity == "warning"
    assert result["metrics"]["latency_increase_percent"] == 10.0


def test_ssep_latency_20_percent_is_critical():
    severity, _ = _severity("ssep_lower", {"latency_ms": 40.0}, {"latency_ms": 48.0})
    assert severity == "critical"


def test_ssep_combined_takes_highest_severity():
    # 50% amplitude (warning) + 20% latency (critical) -> overall critical.
    severity, result = _severity(
        "ssep_upper",
        {"amplitude_uv": 2.0, "latency_ms": 20.0},
        {"amplitude_uv": 1.0, "latency_ms": 24.0},
    )
    assert severity == "critical"
    assert len(result["fired_criteria"]) == 2


# --- TcMEP (muscle MEP): >=80% warning, all-or-none critical ----------------

def test_tcmep_80_percent_is_warning():
    severity, _ = _severity("tcmep", {"amplitude_uv": 700.0}, {"amplitude_uv": 140.0})
    assert severity == "warning"


def test_tcmep_all_or_none_loss_is_critical():
    severity, _ = _severity("tcmep", {"amplitude_uv": 700.0}, {"amplitude_uv": 0.0})
    assert severity == "critical"


def test_tcmep_threshold_elevation_volts_override():
    severity, result = _severity("tcmep", {"amplitude_uv": 700.0}, {"amplitude_uv": 650.0}, override={"mep_threshold_increase_v": 120.0})
    assert severity == "warning"
    assert result["metrics"]["mep_threshold_increase_v"] == 120.0


# --- D-wave: >=50% warning, >=60% critical ----------------------------------

def test_dwave_50_percent_is_warning():
    severity, _ = _severity("dwave", {"amplitude_uv": 30.0}, {"amplitude_uv": 15.0})
    assert severity == "warning"


def test_dwave_63_percent_is_critical():
    severity, _ = _severity("dwave", {"amplitude_uv": 30.0}, {"amplitude_uv": 11.0})
    assert severity == "critical"


# --- BAEP: wave V latency prolongation (ms) ---------------------------------

def test_baep_wave_v_latency_06ms_is_warning():
    severity, result = _severity("baep", {"latency_ms": 5.7}, {"latency_ms": 6.3})
    assert severity == "warning"
    assert result["metrics"]["wave_v_latency_increase_ms"] == pytest.approx(0.6, abs=0.01)


def test_baep_wave_v_latency_11ms_is_critical():
    severity, _ = _severity("baep", {"latency_ms": 5.7}, {"latency_ms": 6.8})
    assert severity == "critical"


# --- Triggered EMG (pedicle): low threshold is the danger (below) -----------

def test_temg_threshold_8ma_is_warning():
    severity, _ = _severity("temg", current={"threshold_ma": 8.0})
    assert severity == "warning"


def test_temg_threshold_4ma_is_critical():
    severity, _ = _severity("temg", current={"threshold_ma": 4.0})
    assert severity == "critical"


def test_temg_threshold_14ma_is_watch():
    severity, _ = _severity("temg", current={"threshold_ma": 14.0})
    assert severity == "watch"


def test_temg_threshold_20ma_is_within_limits():
    severity, _ = _severity("temg", current={"threshold_ma": 20.0})
    assert severity == "none"


# --- Free-running EMG: neurotonic train duration ----------------------------

def test_emg_neurotonic_train_2s_is_warning():
    severity, _ = _severity("cranial_emg", override={"neurotonic_train_seconds": 2.0})
    assert severity == "warning"


def test_emg_neurotonic_train_12s_is_critical():
    severity, _ = _severity("cranial_emg", override={"neurotonic_train_seconds": 12.0})
    assert severity == "critical"


# --- Engine guards & metrics passthrough ------------------------------------

def test_unsupported_family_raises():
    with pytest.raises(ValueError):
        analyze_signal(family="not_a_modality", baseline={}, current={})


def test_metrics_override_without_baseline():
    severity, result = _severity("ssep_upper", override={"amplitude_drop_percent": 55.0})
    assert severity == "warning"
    assert result["metrics"]["amplitude_drop_percent"] == 55.0


def test_not_assessed_when_metric_absent():
    result = analyze_signal(family="ssep_upper", baseline={}, current={})
    assert result["severity"] == "none"
    assert all(rule["status"] == "not_assessed" for rule in result["criteria"])


# --- Response protocol ordering by signature --------------------------------

def test_protocol_orders_surgical_first_for_focal_abrupt_change():
    protocol = build_response_protocol(group="ssep", severity="warning", signature={"laterality": "unilateral", "onset": "abrupt"}, family="ssep_upper")
    assert protocol["differential"][0]["category"] == "surgical"


def test_protocol_orders_anesthetic_first_for_bilateral_gradual_change():
    protocol = build_response_protocol(group="ssep", severity="warning", signature={"laterality": "bilateral", "onset": "gradual"}, family="ssep_upper")
    assert protocol["differential"][0]["category"] == "anesthetic"


def test_protocol_orders_technical_first_for_single_channel():
    protocol = build_response_protocol(group="ssep", severity="warning", signature={"scope": "single_channel"}, family="ssep_upper")
    assert protocol["differential"][0]["category"] == "technical"


def test_protocol_always_lists_all_five_categories():
    protocol = build_response_protocol(group="tcmep", severity="critical", signature={}, family="tcmep")
    categories = {entry["category"] for entry in protocol["differential"]}
    assert categories == set(DIFFERENTIAL_LIBRARY.keys())


def test_warning_protocol_includes_surgeon_notification():
    result = analyze_signal(family="ssep_upper", baseline={"amplitude_uv": 2.0}, current={"amplitude_uv": 0.8})
    actions = " ".join(result["response_protocol"]["immediate_actions"]).lower()
    assert "surgeon" in actions


# --- Criteria knowledge base integrity --------------------------------------

def test_every_criteria_group_has_citation_and_rules():
    for group, spec in ALERT_CRITERIA.items():
        assert spec["citation"].strip(), f"{group} missing citation"
        assert spec["rules"], f"{group} has no rules"
        for rule in spec["rules"]:
            assert rule["comparison"] in {"above", "below"}
            assert rule["warning_at"] is not None
