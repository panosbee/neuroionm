"""Real-time IONM Alert Response Protocol engine.

This module turns a baseline -> current signal comparison into a deterministic,
clinically-grounded alert with a structured differential-response protocol.

Everything here is anchored to published intraoperative neuromonitoring (IONM)
warning criteria. No threshold is invented:

* SSEP            amplitude decrease >=50% and/or latency increase >=10%
                  (ASNM/ACNS SSEP position statement).
* TcMEP (muscle)  CMAP amplitude decrease >=80% (Langeloo), stimulation
                  threshold elevation >100 V (Calancie/Langeloo), and
                  all-or-none disappearance as the critical endpoint
                  (Kothbauer/Deletis; Macdonald review).
* D-wave          amplitude decrease >=50% predicts long-term motor deficit
                  (Deletis & Sala).
* BAEP            wave V latency prolongation >=0.5-1.0 ms and/or >=50%
                  amplitude decrease (ACNS guidance).
* Triggered EMG   pedicle-screw stimulation threshold <10-12 mA suspicious,
                  <5-8 mA high concern for a medial/pedicle-wall breach
                  (Calancie/Toleikis).
* Free-running    sustained neurotonic (train) discharges indicate mechanical
  EMG             or thermal nerve irritation.

The "watch" band is an explicit advisory trend (a configurable fraction of the
published warning threshold) and is clearly distinguished from the published
"warning"/"critical" lines so it is never mistaken for a hard criterion.
"""

from __future__ import annotations

from typing import Any

from backend.app.services.signal_dsp import linear_slope


# An advisory early-warning band: a trend that has reached this fraction of the
# published warning threshold but has not yet crossed it. This is a trend cue,
# NOT a published criterion, and is labelled as such everywhere it surfaces.
WATCH_FRACTION = 0.6

_SEVERITY_ORDER = {"none": 0, "watch": 1, "warning": 2, "critical": 3}


# Maps a waveform/modality family (as produced by services.waveforms) onto the
# criteria group that governs its alert thresholds.
_FAMILY_TO_GROUP = {
    "ssep_upper": "ssep",
    "ssep_lower": "ssep",
    "ssep": "ssep",
    "baep": "baep",
    "dwave": "dwave",
    "tcmep": "tcmep",
    "mep": "tcmep",
    "temg": "temg",
    "cranial_emg": "emg",
    "emg": "emg",
}


# Published warning criteria. `comparison` is "above" when a rising metric is the
# danger (amplitude drop %, latency %, threshold elevation) and "below" when a
# falling metric is the danger (triggered-EMG stimulation threshold in mA).
ALERT_CRITERIA: dict[str, dict[str, Any]] = {
    "ssep": {
        "label": "Somatosensory evoked potentials",
        "logic": "either",
        "rules": [
            {"metric": "amplitude_drop_percent", "comparison": "above", "warning_at": 50.0, "critical_at": 80.0, "label": "Amplitude reduction", "unit": "%"},
            {"metric": "latency_increase_percent", "comparison": "above", "warning_at": 10.0, "critical_at": 20.0, "label": "Latency prolongation", "unit": "%"},
        ],
        "citation": "ASNM/ACNS SSEP position statement: amplitude decrease >=50% and/or latency increase >=10% from baseline.",
    },
    "tcmep": {
        "label": "Transcranial motor evoked potentials (muscle MEP)",
        "logic": "either",
        "rules": [
            {"metric": "amplitude_drop_percent", "comparison": "above", "warning_at": 80.0, "critical_at": 100.0, "label": "CMAP amplitude reduction", "unit": "%"},
            {"metric": "mep_threshold_increase_v", "comparison": "above", "warning_at": 100.0, "critical_at": None, "label": "Stimulation threshold elevation", "unit": "V"},
        ],
        "citation": "Macdonald TcMEP review; Langeloo >=80% amplitude decrease; Calancie/Langeloo >100 V threshold elevation; Kothbauer/Deletis all-or-none disappearance (critical).",
    },
    "dwave": {
        "label": "Epidural D-wave",
        "logic": "either",
        "rules": [
            {"metric": "amplitude_drop_percent", "comparison": "above", "warning_at": 50.0, "critical_at": 60.0, "label": "D-wave amplitude reduction", "unit": "%"},
        ],
        "citation": "Deletis & Sala: D-wave amplitude decrease >=50% predicts long-term motor deficit.",
    },
    "baep": {
        "label": "Brainstem auditory evoked potentials",
        "logic": "either",
        "rules": [
            {"metric": "wave_v_latency_increase_ms", "comparison": "above", "warning_at": 0.5, "critical_at": 1.0, "label": "Wave V latency prolongation", "unit": "ms"},
            {"metric": "amplitude_drop_percent", "comparison": "above", "warning_at": 50.0, "critical_at": None, "label": "Wave V amplitude reduction", "unit": "%"},
        ],
        "citation": "ACNS BAEP guidance: wave V latency prolongation >=0.5-1.0 ms and/or >=50% amplitude decrease.",
    },
    "temg": {
        "label": "Triggered EMG (pedicle-screw stimulation)",
        "logic": "either",
        "rules": [
            {"metric": "threshold_ma", "comparison": "below", "warning_at": 10.0, "critical_at": 5.0, "label": "Low stimulation threshold (possible breach)", "unit": "mA"},
        ],
        "citation": "Calancie/Toleikis pedicle-screw t-EMG: <10-12 mA suspicious, <5-8 mA high concern for medial/pedicle-wall breach.",
    },
    "emg": {
        "label": "Free-running / cranial EMG",
        "logic": "either",
        "rules": [
            {"metric": "neurotonic_train_seconds", "comparison": "above", "warning_at": 1.0, "critical_at": 10.0, "label": "Sustained neurotonic discharge", "unit": "s"},
        ],
        "citation": "Sustained neurotonic (train) discharges indicate mechanical/thermal nerve irritation; prolonged trains carry higher injury risk.",
    },
}


# Established differential categories for an acute IONM signal change, each with
# the signature that points toward it and concrete bedside checks/actions.
DIFFERENTIAL_LIBRARY: dict[str, dict[str, Any]] = {
    "anesthetic": {
        "label": "Anesthetic",
        "typical_signature": "Bilateral/global, gradual onset, time-locked to an agent change or bolus.",
        "items": [
            {"check": "Recent bolus or infusion change (propofol, opioid, ketamine, lidocaine)?", "action": "Ask anesthesia to hold/level the agent and document the time.", "distinguishing": "Global, symmetric change following a drug event."},
            {"check": "Halogenated agent concentration / MAC increase or N2O change?", "action": "Request a stable, MEP-friendly regimen (TIVA where appropriate).", "distinguishing": "Dose-dependent suppression of MEP > SSEP."},
            {"check": "Neuromuscular blockade level (TOF) for MEP/EMG?", "action": "Confirm no residual paralytic; check TOF.", "distinguishing": "Flat MEP/EMG with preserved SSEP."},
        ],
    },
    "physiologic": {
        "label": "Systemic / physiologic",
        "typical_signature": "Bilateral/global, tracks a vital-sign change.",
        "items": [
            {"check": "Mean arterial pressure vs target (relative hypotension)?", "action": "Request MAP support to the agreed target.", "distinguishing": "Global change tracking a BP drop."},
            {"check": "Core/limb temperature (hypothermia)?", "action": "Correlate with warming/cooling; document.", "distinguishing": "Diffuse latency increase with cooling."},
            {"check": "EtCO2, SpO2, hematocrit, glucose?", "action": "Correct hypocapnia/hypoxia/anemia as indicated.", "distinguishing": "Global change tracking a metabolic shift."},
        ],
    },
    "technical": {
        "label": "Technical / acquisition",
        "typical_signature": "Single channel or non-physiologic morphology; abrupt, not patient-correlated.",
        "items": [
            {"check": "Electrode impedance / displaced or dry electrode?", "action": "Re-check and reseat electrodes; rebalance impedance.", "distinguishing": "Isolated channel, high impedance, flat or noisy."},
            {"check": "Stimulator output, connectors, reference, ground intact?", "action": "Verify stimulus delivery and the recording chain.", "distinguishing": "Loss with intact contralateral/adjacent channels."},
            {"check": "50/60 Hz line interference or new equipment in the field?", "action": "Identify and remove the interference source; re-average.", "distinguishing": "Periodic mains contamination, not physiologic."},
        ],
    },
    "positioning": {
        "label": "Positioning / peripheral",
        "typical_signature": "Regional/unilateral, time-locked to patient positioning.",
        "items": [
            {"check": "Limb position, arm abduction, ulnar/peroneal compression?", "action": "Reposition the limb and relieve focal pressure.", "distinguishing": "Unilateral upper-limb (brachial plexus) or peroneal change."},
            {"check": "Head/neck rotation or extreme flexion/extension?", "action": "Neutralize head/neck position with the surgical team.", "distinguishing": "Bilateral upper-limb or cervical-cord pattern after positioning."},
            {"check": "Time since last position change?", "action": "Correlate the change with the positioning timeline.", "distinguishing": "Onset coincides with a positioning event, not a surgical maneuver."},
        ],
    },
    "surgical": {
        "label": "Surgical",
        "typical_signature": "Focal to the at-risk pathway, abrupt, time-locked to a surgical maneuver.",
        "items": [
            {"check": "Retraction, distraction/correction, screw/instrumentation, clip, or ischemia at the at-risk structure?", "action": "Notify the surgeon now; pause/relax the maneuver and reassess.", "distinguishing": "Focal change matching the operative pathway and maneuver timing."},
            {"check": "Recent deformity-correction or distraction step?", "action": "Consider staged release/partial reversal per surgeon judgement.", "distinguishing": "MEP/SSEP loss after a correction maneuver."},
            {"check": "Local hematoma, vasospasm, or vessel compromise?", "action": "Inspect the field; consider papaverine/irrigation per surgeon.", "distinguishing": "Territorial change matching a vascular supply."},
        ],
    },
}


def analyze_signal(
    *,
    family: str,
    baseline: dict[str, Any] | None,
    current: dict[str, Any] | None,
    metrics_override: dict[str, Any] | None = None,
    signature: dict[str, Any] | None = None,
    channel_label: str | None = None,
) -> dict[str, Any]:
    """Detect an alert and build its response protocol.

    `baseline`/`current` carry per-channel measurements (amplitude_uv,
    latency_ms, threshold_ma). `metrics_override` supplies metrics that cannot be
    derived from amplitude/latency alone (e.g. mep_threshold_increase_v,
    neurotonic_train_seconds). `signature` optionally describes the change
    (laterality, onset, scope, after_maneuver) to order the differential.
    """
    group = _FAMILY_TO_GROUP.get((family or "").lower())
    if group is None:
        raise ValueError(f"Unsupported modality family: {family!r}")
    criteria = ALERT_CRITERIA[group]

    metrics = _compute_metrics(group, baseline or {}, current or {}, metrics_override or {})
    rule_results = [_evaluate_rule(rule, metrics) for rule in criteria["rules"]]

    fired = [result for result in rule_results if result["fired"]]
    overall = "none"
    for result in rule_results:
        if _SEVERITY_ORDER[result["severity"]] > _SEVERITY_ORDER[overall]:
            overall = result["severity"]
    is_alert = overall in {"warning", "critical"}

    protocol = build_response_protocol(group=group, severity=overall, signature=signature or {}, family=family)

    return {
        "family": family,
        "group": group,
        "group_label": criteria["label"],
        "channel_label": channel_label,
        "severity": overall,
        "is_alert": is_alert,
        "metrics": metrics,
        "criteria": rule_results,
        "fired_criteria": [result["label"] for result in fired],
        "citation": criteria["citation"],
        "summary": _summarize(criteria["label"], channel_label, overall, fired),
        "response_protocol": protocol,
    }


def _amplitude_warning_threshold(group: str) -> float | None:
    """The published amplitude-drop warning line (%) for a group, if it has one."""
    criteria = ALERT_CRITERIA.get(group)
    if not criteria:
        return None
    for rule in criteria["rules"]:
        if rule["metric"] == "amplitude_drop_percent":
            return float(rule["warning_at"])
    return None


def assess_amplitude_trend(
    *,
    family: str,
    trend: list[float],
    baseline_amplitude: float | None = None,
    window: int = 10,
    horizon: int = 5,
    min_slope: float = 0.5,
) -> dict[str, Any]:
    """Advisory slope projection of an amplitude trajectory toward the warning line.

    Item #11: instead of judging only the latest sweep, fit the recent
    amplitude-drop trajectory and project it forward. If the drop is steadily
    climbing and is expected to cross the published warning threshold within
    `horizon` sweeps, surface an early "trending toward warning" advisory — well
    before the single-point comparison would fire.

    This is purely advisory trend context: it NEVER raises a warning/critical
    verdict itself (that stays with `analyze_signal` against the published
    criteria). `trend` is the rolling per-sweep peak-to-peak amplitude, oldest
    first; the baseline defaults to the first trend point.
    """
    group = _FAMILY_TO_GROUP.get((family or "").lower())
    warning_at = _amplitude_warning_threshold(group) if group else None
    not_applicable = {
        "applicable": False,
        "family": family,
        "group": group,
        "status": "not_applicable",
        "advisory": "Slope watch-band applies only to amplitude-drop modalities (SSEP, TcMEP, D-wave, BAEP).",
    }
    if group is None or warning_at is None:
        return not_applicable

    series = [value for value in trend if isinstance(value, (int, float))]
    if len(series) < 3:
        return {**not_applicable, "status": "insufficient_data",
                "advisory": "Not enough sweeps yet to estimate an amplitude trend."}

    baseline = baseline_amplitude if baseline_amplitude is not None else series[0]
    if not baseline or baseline <= 0:
        return {**not_applicable, "status": "no_baseline",
                "advisory": "No positive baseline amplitude to compute a drop trajectory."}

    drops = [max(0.0, (baseline - value) / baseline * 100.0) for value in series]
    recent = drops[-window:] if len(drops) > window else drops
    slope = linear_slope(recent)  # drop-% per sweep
    current_drop = round(drops[-1], 1)

    base = {
        "applicable": True,
        "family": family,
        "group": group,
        "warning_at_percent": round(warning_at, 1),
        "current_drop_percent": current_drop,
        "slope_percent_per_sweep": round(slope, 3),
        "sweeps_analysed": len(recent),
    }

    if current_drop >= warning_at:
        return {**base, "status": "warning_reached", "projected_drop_percent": current_drop,
                "sweeps_to_warning": 0,
                "advisory": "Amplitude drop has already reached the published warning line; evaluate with analyze_signal."}

    if slope < min_slope:
        return {**base, "status": "stable", "projected_drop_percent": current_drop,
                "sweeps_to_warning": None,
                "advisory": "Amplitude trajectory is stable or recovering; no early warning."}

    sweeps_to_warning = (warning_at - current_drop) / slope
    projected = round(current_drop + slope * horizon, 1)
    if sweeps_to_warning <= horizon:
        return {**base, "status": "trending_toward_warning", "projected_drop_percent": projected,
                "sweeps_to_warning": round(sweeps_to_warning, 1),
                "advisory": (
                    f"Amplitude is trending down ~{round(slope, 2)}%/sweep and is projected to reach the "
                    f"{round(warning_at, 1)}% warning line in ~{round(sweeps_to_warning, 1)} sweeps. "
                    "Advisory only — confirm the trace and pre-position the differential."
                )}

    return {**base, "status": "declining", "projected_drop_percent": projected,
            "sweeps_to_warning": round(sweeps_to_warning, 1),
            "advisory": (
                f"Amplitude is declining (~{round(slope, 2)}%/sweep) but not projected to cross the warning "
                f"line within {horizon} sweeps. Keep watching."
            )}


def _compute_metrics(
    group: str,
    baseline: dict[str, Any],
    current: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for key, value in override.items():
        number = _num(value)
        if number is not None:
            metrics[key] = number

    b_amp = _num(baseline.get("amplitude_uv"))
    c_amp = _num(current.get("amplitude_uv"))
    b_lat = _num(baseline.get("latency_ms"))
    c_lat = _num(current.get("latency_ms"))

    if b_amp is not None and b_amp > 0 and c_amp is not None and "amplitude_drop_percent" not in metrics:
        metrics["amplitude_drop_percent"] = round(max(0.0, (b_amp - c_amp) / b_amp * 100.0), 1)
    if b_lat is not None and b_lat > 0 and c_lat is not None:
        if "latency_increase_percent" not in metrics:
            metrics["latency_increase_percent"] = round(max(0.0, (c_lat - b_lat) / b_lat * 100.0), 1)
        if group == "baep" and "wave_v_latency_increase_ms" not in metrics:
            metrics["wave_v_latency_increase_ms"] = round(max(0.0, c_lat - b_lat), 2)

    if "threshold_ma" not in metrics:
        c_thr = _num(current.get("threshold_ma"))
        if c_thr is not None:
            metrics["threshold_ma"] = c_thr
    return metrics


def _evaluate_rule(rule: dict[str, Any], metrics: dict[str, float]) -> dict[str, Any]:
    metric = rule["metric"]
    value = metrics.get(metric)
    base = {
        "metric": metric,
        "label": rule["label"],
        "unit": rule["unit"],
        "warning_at": rule["warning_at"],
        "critical_at": rule.get("critical_at"),
        "comparison": rule["comparison"],
        "value": value,
    }
    if value is None:
        return {**base, "status": "not_assessed", "severity": "none", "fired": False}

    warning_at = rule["warning_at"]
    critical_at = rule.get("critical_at")
    severity = "none"
    if rule["comparison"] == "above":
        if critical_at is not None and value >= critical_at:
            severity = "critical"
        elif value >= warning_at:
            severity = "warning"
        elif value >= warning_at * WATCH_FRACTION:
            severity = "watch"
    else:  # "below": a falling metric (e.g. triggered-EMG threshold) is the danger
        if critical_at is not None and value <= critical_at:
            severity = "critical"
        elif value <= warning_at:
            severity = "warning"
        elif WATCH_FRACTION > 0 and value <= warning_at / WATCH_FRACTION:
            severity = "watch"

    fired = severity in {"warning", "critical"}
    status = "fired" if fired else ("approaching" if severity == "watch" else "within_limits")
    return {**base, "status": status, "severity": severity, "fired": fired}


def build_response_protocol(
    *,
    group: str,
    severity: str,
    signature: dict[str, Any],
    family: str,
) -> dict[str, Any]:
    """Build immediate actions plus an ordered differential checklist."""
    order = _differential_order(signature)
    differential = []
    for category in order:
        entry = DIFFERENTIAL_LIBRARY[category]
        differential.append(
            {
                "category": category,
                "label": entry["label"],
                "typical_signature": entry["typical_signature"],
                "items": entry["items"],
            }
        )
    return {
        "severity": severity,
        "immediate_actions": _immediate_actions(group, severity),
        "differential": differential,
        "ordering_rationale": _ordering_rationale(signature),
    }


def _immediate_actions(group: str, severity: str) -> list[str]:
    actions = [
        "Confirm the change is real and reproducible (repeat the trace; rule out a single artifactual sweep).",
        "Note the exact time and the concurrent surgical step.",
    ]
    if severity in {"warning", "critical"}:
        actions.append("Announce the change to the surgeon and anesthesia team now.")
    if group in {"tcmep", "emg", "temg"}:
        actions.append("Verify neuromuscular blockade status (TOF) before interpreting motor responses.")
    if group == "ssep":
        actions.append("Check the contralateral and proximal (Erb/cervical) channels to localise the level.")
    if group == "baep":
        actions.append("Confirm stimulus delivery (insert earphone), masking, and wave I integrity.")
    if group == "temg":
        actions.append("Re-stimulate the screw/track and confirm with a direct bony control stimulation.")
    if severity == "critical":
        actions.append("Treat as a high-risk alert: pause the precipitating maneuver and start the differential immediately.")
    return actions


def _differential_order(signature: dict[str, Any]) -> list[str]:
    laterality = str(signature.get("laterality") or "").lower()
    onset = str(signature.get("onset") or "").lower()
    scope = str(signature.get("scope") or "").lower()
    after_maneuver = bool(signature.get("after_maneuver"))

    if scope == "single_channel" or scope == "single-channel":
        return ["technical", "surgical", "positioning", "physiologic", "anesthetic"]
    if after_maneuver or (laterality == "unilateral" and onset == "abrupt"):
        return ["surgical", "positioning", "technical", "physiologic", "anesthetic"]
    if laterality in {"bilateral", "global"} and onset in {"gradual", ""}:
        return ["anesthetic", "physiologic", "technical", "positioning", "surgical"]
    # Default: rule out fast-correctable global/technical causes while keeping
    # the time-critical surgical cause explicitly in view.
    return ["technical", "anesthetic", "physiologic", "positioning", "surgical"]


def _ordering_rationale(signature: dict[str, Any]) -> str:
    laterality = str(signature.get("laterality") or "").lower()
    onset = str(signature.get("onset") or "").lower()
    scope = str(signature.get("scope") or "").lower()
    after_maneuver = bool(signature.get("after_maneuver"))
    if scope in {"single_channel", "single-channel"}:
        return "Single-channel pattern: technical/acquisition causes are checked first."
    if after_maneuver or (laterality == "unilateral" and onset == "abrupt"):
        return "Focal/abrupt change after a maneuver: surgical and positioning causes lead."
    if laterality in {"bilateral", "global"} and onset in {"gradual", ""}:
        return "Bilateral/gradual change: anesthetic and systemic causes lead."
    return "No distinguishing signature supplied: fast-correctable technical/anesthetic/systemic causes are checked first while the surgical cause stays in view."


def _summarize(group_label: str, channel_label: str | None, severity: str, fired: list[dict[str, Any]]) -> str:
    scope = f"{group_label}" + (f" ({channel_label})" if channel_label else "")
    if severity == "none":
        return f"{scope}: signals within published limits."
    if severity == "watch":
        return f"{scope}: advisory trend approaching the warning threshold; no published criterion met yet."
    fired_text = ", ".join(f"{item['label']} {item['value']}{item['unit']}" for item in fired) or "warning criteria"
    line = "critical" if severity == "critical" else "warning"
    return f"{scope}: {line} alert - {fired_text}."


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
