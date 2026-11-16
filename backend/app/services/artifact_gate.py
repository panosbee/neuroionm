"""Artifact and anesthesia-context gate (item #9).

Before a deterministic crossing is escalated in front of the surgeon, this gate
asks a separate question: *is this change driven by a recording artifact or by a
systemic anesthetic/physiologic confound rather than by surgery?* It answers from
real DSP descriptors of the actual current sweep (mains line noise, broadband
EMG, electrode-pop transients — the artifact families the literature flags) plus
the anesthesia/physiology context recorded for the case.

Two deliberate design choices:

* **It is advisory, never a silencer.** The gate does NOT mutate or suppress the
  published verdict. Auto-suppressing an alert would itself be a regulated
  decision; instead the gate attaches a quality assessment and a recommendation
  ("hold for confirmation — likely artifact") that a human confirms. The system
  stays a record-and-supervision tool, not an autonomous alerting device.
* **It is transparent measurement, not a black box.** Every flag is a thresholded
  DSP measurement on the uploaded samples — reproducible and inspectable — not an
  opaque score. This is the false-alarm headline against single-point devices.
"""

from __future__ import annotations

from typing import Any

from backend.app.services.signal_dsp import spectral_features


# Thresholds on the DSP descriptors. These are signal-quality measurement bands,
# not clinical warning criteria — they decide "is the trace trustworthy", never
# "is there a deficit".
LINE_NOISE_SUSPECT = 0.35   # >=35% of sweep power at mains (+2nd harmonic)
LINE_NOISE_DOMINANT = 0.55
BROADBAND_SUSPECT = 0.35    # first-difference energy fraction (EMG contamination)
BROADBAND_DOMINANT = 0.55
TRANSIENT_SUSPECT = 8.0     # largest sample jump / sweep RMS (electrode pop)
TRANSIENT_DOMINANT = 14.0


def _confidence(value: float, suspect: float, dominant: float) -> float:
    if value <= suspect:
        return 0.0
    if value >= dominant:
        return 1.0
    return round((value - suspect) / (dominant - suspect), 3)


def _anesthesia_assessment(context: dict[str, Any] | None) -> dict[str, Any]:
    """Flag a systemic anesthetic/physiologic confound that can mimic a change.

    A global, bilateral amplitude/latency drift that coincides with a halogenated
    agent step, a TIVA bolus, hypotension or hypothermia is more likely systemic
    than surgical. We surface the confound; we do not decide for the clinician.
    """
    if not context:
        return {"confound_suspected": False, "factors": []}

    factors: list[str] = []
    regimen = str(context.get("regimen") or "").lower()
    if regimen in {"inhalational", "halogenated", "gas"}:
        factors.append("Halogenated/inhalational regimen suppresses cortical SSEP and TcMEP amplitude.")
    if context.get("recent_bolus"):
        factors.append("Recent anesthetic bolus can transiently depress evoked responses.")
    mac = context.get("mac")
    if isinstance(mac, (int, float)) and mac >= 1.0:
        factors.append(f"MAC {mac} is high enough to attenuate evoked responses.")

    map_mmhg = context.get("map_mmHg")
    if isinstance(map_mmhg, (int, float)) and map_mmhg < 60:
        factors.append(f"Mean arterial pressure {map_mmhg} mmHg (hypotension) can reduce amplitudes.")
    temp_c = context.get("temp_c")
    if isinstance(temp_c, (int, float)) and temp_c < 35.0:
        factors.append(f"Core temperature {temp_c} °C (hypothermia) prolongs latency and lowers amplitude.")

    return {"confound_suspected": bool(factors), "regimen": regimen or None, "factors": factors}


def evaluate_signal_quality(
    sweep: list[float],
    sample_rate: float,
    *,
    anesthesia_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify the current sweep for artifact and systemic confounds (advisory)."""
    features = spectral_features(sweep, sample_rate)
    line = features["line_noise_ratio"]
    broadband = features["broadband_ratio"]
    transient = features["transient_ratio"]

    flags = {
        "line_noise": line >= LINE_NOISE_SUSPECT,
        "emg_broadband": broadband >= BROADBAND_SUSPECT,
        "electrode_pop": transient >= TRANSIENT_SUSPECT,
    }
    scores = {
        "line_noise": _confidence(line, LINE_NOISE_SUSPECT, LINE_NOISE_DOMINANT),
        "emg_broadband": _confidence(broadband, BROADBAND_SUSPECT, BROADBAND_DOMINANT),
        "electrode_pop": _confidence(transient, TRANSIENT_SUSPECT, TRANSIENT_DOMINANT),
    }
    dominant = max(scores, key=scores.get)
    confidence = scores[dominant]
    artifact_suspected = confidence > 0.0

    anesthesia = _anesthesia_assessment(anesthesia_context)

    return {
        "features": features,
        "flags": flags,
        "scores": scores,
        "artifact_suspected": artifact_suspected,
        "dominant_artifact": dominant if artifact_suspected else None,
        "artifact_confidence": confidence,
        "anesthesia": anesthesia,
    }


def gate_alert(
    alert: dict[str, Any],
    sweep: list[float],
    sample_rate: float,
    *,
    anesthesia_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach an advisory quality gate to a deterministic alert.

    Returns the original alert untouched plus a `quality_gate` block. The
    published `severity`/`is_alert` are NEVER modified — when an artifact or a
    systemic confound is suspected we recommend `hold_for_confirmation` so a human
    confirms the trace is real before it escalates; otherwise `pass_through`.
    """
    quality = evaluate_signal_quality(sweep, sample_rate, anesthesia_context=anesthesia_context)
    is_alert = bool(alert.get("is_alert"))
    confound = quality["anesthesia"]["confound_suspected"]
    artifact = quality["artifact_suspected"]

    if is_alert and (artifact or confound):
        reasons: list[str] = []
        if artifact:
            reasons.append(
                f"possible {quality['dominant_artifact'].replace('_', ' ')} "
                f"(confidence {quality['artifact_confidence']})"
            )
        if confound:
            reasons.extend(quality["anesthesia"]["factors"])
        recommendation = "hold_for_confirmation"
        advisory = (
            "The deterministic engine recorded a crossing, but signal quality / context suggest it may not "
            "be surgical: " + "; ".join(reasons) + ". Confirm the trace is real and reproducible before escalating."
        )
    elif is_alert:
        recommendation = "pass_through"
        advisory = "No artifact or systemic confound detected; the crossing looks genuine — proceed with the protocol."
    else:
        recommendation = "pass_through"
        advisory = "No published criterion crossed; quality assessment recorded for context."

    gated = {**alert}
    gated["quality_gate"] = {
        "recommendation": recommendation,
        "advisory": advisory,
        "artifact_suspected": artifact,
        "confound_suspected": confound,
        **quality,
    }
    return gated
