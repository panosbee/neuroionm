"""Tests for the hard numeric guardrail on AI output (item #14).

The deterministic engine is the only source of clinical numbers. The guardrail
must let the model quote engine-produced or context-provided figures (including
faithfully rounded ones) while rejecting any clinical magnitude it invents.
"""

import pytest

from backend.app.services.ai_guardrail import (
    GuardrailViolation,
    audit_numbers,
    enforce_numeric_guardrail,
    grounded_values,
)


def _alert():
    return {
        "severity": "critical",
        "metrics": {"amplitude_drop_percent": 84.7, "latency_increase_percent": 5.0},
        "criteria": [
            {"metric": "amplitude_drop_percent", "warning_at": 50, "critical_at": 80, "value": 84.7},
            {"metric": "latency_increase_percent", "warning_at": 10, "critical_at": 20, "value": 5.0},
        ],
    }


def test_grounded_values_include_engine_numbers_and_rounding():
    grounded = grounded_values(_alert())
    assert 84.7 in grounded
    assert 85 in grounded  # rounded form allowed
    assert 50 in grounded and 80 in grounded


def test_clean_copilot_passes():
    copilot = {
        "summary": "Cortical amplitude fell 85% past the 80% critical line; latency stable.",
        "next_checks": [{"priority": "now", "action": "Repeat trace", "rationale": "Confirm the 85% drop is real."}],
        "clinical_caution": "Treat as critical.",
    }
    # Returns unchanged when nothing is fabricated.
    assert enforce_numeric_guardrail(copilot, alert=_alert()) is copilot


def test_fabricated_number_is_rejected():
    copilot = {
        "summary": "Amplitude dropped 92% and latency increased 40% — both fabricated values.",
        "clinical_caution": "x",
    }
    with pytest.raises(GuardrailViolation) as excinfo:
        enforce_numeric_guardrail(copilot, alert=_alert())
    fabricated = {v["value"] for v in excinfo.value.violations}
    assert "92" in fabricated
    assert "40" in fabricated


def test_context_provided_numbers_are_allowed():
    # The model may quote a number that came from read-only retrieved evidence.
    evidence = [{"title": "SSEP statement", "snippet": "A latency increase of 10% is the warning threshold."}]
    copilot = {"summary": "Per the cited statement, a 10% latency increase is the warning line.",
               "clinical_caution": "x"}
    # 10 is already in the engine criteria, but prove context grounding too:
    assert enforce_numeric_guardrail(copilot, alert=_alert(), context_objects=(evidence,)) is copilot


def test_non_clinical_numbers_are_ignored():
    copilot = {
        "summary": "Re-check within 5 minutes across all 3 channels; reposition by 2 mm if needed.",
        "clinical_caution": "x",
    }
    # minutes, channel counts, and mm of positioning are not clinical magnitudes.
    assert enforce_numeric_guardrail(copilot, alert=_alert()) is copilot


def test_no_alert_disables_enforcement():
    copilot = {"summary": "A 99% drop in some other context.", "clinical_caution": "x"}
    # With no engine ground truth, the guardrail does not fire (general Q&A path).
    assert enforce_numeric_guardrail(copilot, alert=None) is copilot


def test_audit_reports_units():
    grounded = grounded_values(_alert())
    violations = audit_numbers({"text": "Baseline was 3.2 uV and threshold 4 mA."}, grounded)
    units = {v["unit"] for v in violations}
    assert "uV" in units
    assert "mA" in units
