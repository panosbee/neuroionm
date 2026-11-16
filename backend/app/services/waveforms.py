import random
import re
import zlib
from math import exp, pi, sin
from typing import Any


# Sweep resolution. Real averaged evoked-potential traces are rendered from a
# few hundred points per sweep; 320 keeps morphology smooth without bloating
# the JSON payload.
SAMPLE_POINTS = 320

DEFAULT_PARAMETERS = {
    "amplitude_drop_percent": 55,
    "latency_shift_ms": 3.0,
    "threshold_ma": 7.0,
    "noise_scale": 1.25,
}

# Nominal peak amplitude (µV), baseline noise floor (µV) and reference latency
# (ms) per modality, anchored to published intraoperative neuromonitoring
# values (median/tibial SSEP, TcMEP CMAP, triggered EMG, BAEP, D-wave).
_BASE_AMPLITUDE_UV = {
    "ssep_upper": 1.9,
    "ssep_lower": 1.3,
    "baep": 0.45,
    "dwave": 32.0,
    "tcmep": 720.0,
    "temg": 480.0,
    "cranial_emg": 360.0,
}
_BASE_NOISE_UV = {
    "ssep_upper": 0.18,
    "ssep_lower": 0.16,
    "baep": 0.05,
    "dwave": 1.2,
    "tcmep": 38.0,
    "temg": 26.0,
    "cranial_emg": 22.0,
}
_BASE_LATENCY_MS = {
    "ssep_upper": 19.5,
    "ssep_lower": 37.0,
    "baep": 5.7,
    "dwave": 3.5,
    "tcmep": 22.0,
    "temg": 6.5,
    "cranial_emg": 7.5,
}
# Effective averaging quality per scenario. Higher = cleaner trace (more sweeps
# in the running average → lower residual noise).
_SCENARIO_AVERAGING = {
    "baseline": 1.0,
    "amplitude_drop": 0.9,
    "latency_shift": 0.9,
    "combined_warning": 0.8,
    "threshold_change": 0.85,
    "anesthetic_suppression": 0.95,
    "artifact": 0.4,
    "stimulation_failure": 0.3,
}
# Mains/line-interference weight per scenario (50 Hz). Artifact and acquisition
# failure show heavy line pickup; baseline is well rejected.
_SCENARIO_LINE = {
    "baseline": 0.25,
    "amplitude_drop": 0.4,
    "latency_shift": 0.4,
    "combined_warning": 0.6,
    "threshold_change": 0.5,
    "anesthetic_suppression": 0.3,
    "artifact": 2.4,
    "stimulation_failure": 1.6,
}


def build_waveform_lab(planner: dict[str, Any], parameters: dict[str, float] | None = None) -> dict[str, Any]:
    node = planner["node"]
    modality = infer_modality(planner)
    channels = build_waveform_channels(planner, modality)
    controls = normalize_parameters(parameters)
    amplitude_scale = max(0.05, 1 - controls["amplitude_drop_percent"] / 100)
    latency_shift = controls["latency_shift_ms"]
    threshold_ma = controls["threshold_ma"]
    noise_scale = controls["noise_scale"]
    scenarios = [
        build_scenario("baseline", "Baseline", modality, channels, amplitude_scale=1.0, latency_shift=0.0, noise_scale=0.25),
        build_scenario("amplitude_drop", "Amplitude drop", modality, channels, amplitude_scale=amplitude_scale, latency_shift=0.6, noise_scale=0.35),
        build_scenario("latency_shift", "Latency shift", modality, channels, amplitude_scale=0.85, latency_shift=latency_shift, noise_scale=0.35),
        build_scenario("combined_warning", "Amplitude + latency warning", modality, channels, amplitude_scale=amplitude_scale, latency_shift=latency_shift, noise_scale=0.55),
        build_scenario("threshold_change", "Threshold change", modality, channels, amplitude_scale=0.62, latency_shift=1.0, noise_scale=0.45, threshold_ma=threshold_ma),
        build_scenario("anesthetic_suppression", "Anesthetic suppression pattern", modality, channels, amplitude_scale=0.52, latency_shift=1.2, noise_scale=0.28),
        build_scenario("artifact", "Artifact / noise", modality, channels, amplitude_scale=0.8, latency_shift=0.8, noise_scale=noise_scale),
        build_scenario("stimulation_failure", "Stimulation / acquisition failure", modality, channels, amplitude_scale=0.08, latency_shift=0.0, noise_scale=0.5),
    ]
    return {
        "node": node,
        "modality": modality,
        "parameters": controls,
        "controls": [
            {"key": "amplitude_drop_percent", "label": "Amplitude drop", "unit": "%", "min": 10, "max": 95, "step": 5},
            {"key": "latency_shift_ms", "label": "Latency shift", "unit": "ms", "min": 0, "max": 12, "step": 0.5},
            {"key": "threshold_ma", "label": "Threshold", "unit": "mA", "min": 1, "max": 30, "step": 1},
            {"key": "noise_scale", "label": "Noise", "unit": "x", "min": 0.2, "max": 3, "step": 0.1},
        ],
        "model": {
            "scope": "academy_training",
            "source": "knowledge_montage_derived",
            "notice": "Physiologically modelled training traces built from the selected montage/channel plan with realistic peak morphology, stimulus artifact, mains interference and averaging noise. Educational simulation, not patient or device recordings.",
        },
        "scenarios": scenarios,
        "quiz": build_waveform_quiz(modality, scenarios),
    }


def normalize_parameters(parameters: dict[str, float] | None = None) -> dict[str, float]:
    incoming = parameters or {}
    return {
        "amplitude_drop_percent": clamp(float(incoming.get("amplitude_drop_percent", DEFAULT_PARAMETERS["amplitude_drop_percent"])), 10, 95),
        "latency_shift_ms": clamp(float(incoming.get("latency_shift_ms", DEFAULT_PARAMETERS["latency_shift_ms"])), 0, 12),
        "threshold_ma": clamp(float(incoming.get("threshold_ma", DEFAULT_PARAMETERS["threshold_ma"])), 1, 30),
        "noise_scale": clamp(float(incoming.get("noise_scale", DEFAULT_PARAMETERS["noise_scale"])), 0.2, 3),
    }


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def infer_modality(planner: dict[str, Any]) -> dict[str, Any]:
    node = planner["node"]
    key = (node.get("key") or "").lower()
    title = f"{node.get('title_el') or ''} {node.get('title_en') or ''}".lower()
    domain = node.get("domain") or ""
    node_type = node.get("node_type") or ""
    metadata = node.get("metadata") or {}
    linked_muscles = node.get("linked_muscles") or []
    roots = [str(root).upper() for root in planner.get("roots", [])]
    text = f"{key} {title}"

    if any(token in text for token in ("baep", "auditory", "acoustic", "brainstem")) or "Auditory brainstem responses" in linked_muscles:
        return _modality_baep()
    if "d-wave" in text or "dwave" in text or "d wave" in text:
        return _modality_dwave()

    montage = metadata.get("ssep_montage") or {}
    potential = (montage.get("potential") or "").upper()
    if domain == "ssep" or "ssep" in text or potential:
        is_lower = (
            "tibial" in text
            or "peroneal" in text
            or "P37" in potential
            or any(root[:1] in {"L", "S"} for root in roots if root)
        )
        return _modality_ssep_lower() if is_lower else _modality_ssep_upper()

    if domain == "pedicle" or "pedicle" in text or node_type == "pedicle_level":
        return _modality_temg()

    if domain in {"thyroid", "cranial"}:
        return _modality_cranial_emg()

    return _modality_tcmep()


def _modality(
    key: str,
    label: str,
    family: str,
    time_max: float,
    ms_per_div: float,
    sensitivity_uv_per_div: float,
    averages: int,
    lff_hz: float,
    hff_hz: float,
    notch_hz: float,
    primary_metric: str,
    description: str,
    stim_artifact: bool = True,
) -> dict[str, Any]:
    return {
        "key": key,
        "family": family,
        "label": label,
        "time_unit": "ms",
        "time_min": 0,
        "time_max": time_max,
        "value_unit": "uV",
        "primary_metric": primary_metric,
        "description": description,
        "device": {
            "sweep_ms": time_max,
            "time_base_ms_per_div": ms_per_div,
            "sensitivity_uv_per_div": sensitivity_uv_per_div,
            "averages": averages,
            "lff_hz": lff_hz,
            "hff_hz": hff_hz,
            "notch_hz": notch_hz,
            "stim_artifact": stim_artifact,
        },
        "grid": {"x_divisions": max(1, round(time_max / ms_per_div)), "y_divisions": 8},
    }


def _modality_ssep_upper() -> dict[str, Any]:
    return _modality(
        "ssep_upper", "SSEP — άνω άκρο (N9/N13/N20)", "evoked",
        time_max=50, ms_per_div=5, sensitivity_uv_per_div=1.0, averages=300,
        lff_hz=30, hff_hz=500, notch_hz=50, primary_metric="latency_ms",
        description="Άνω-άκρου SSEP: σημείο Erb N9, αυχενικό N13, φλοιικό N20/P25 με κεντρικό χρόνο αγωγής N13–N20.",
    )


def _modality_ssep_lower() -> dict[str, Any]:
    return _modality(
        "ssep_lower", "SSEP — κάτω άκρο (N22/P37)", "evoked",
        time_max=80, ms_per_div=8, sensitivity_uv_per_div=1.0, averages=500,
        lff_hz=30, hff_hz=500, notch_hz=50, primary_metric="latency_ms",
        description="Κάτω-άκρου SSEP: ιγνυακό βόθρο, οσφυϊκό N22, φλοιικό P37/N45.",
    )


def _modality_baep() -> dict[str, Any]:
    return _modality(
        "baep", "BAEP — κύματα I–V", "evoked",
        time_max=10, ms_per_div=1, sensitivity_uv_per_div=0.2, averages=1500,
        lff_hz=150, hff_hz=3000, notch_hz=0, primary_metric="latency_ms",
        description="Ακουστικά προκλητά εγκεφαλικού στελέχους: κύματα I–V, διακορυφικοί χρόνοι I–III, III–V, I–V.",
    )


def _modality_dwave() -> dict[str, Any]:
    return _modality(
        "dwave", "D-wave (επισκληρίδιο)", "evoked",
        time_max=20, ms_per_div=2, sensitivity_uv_per_div=10.0, averages=1,
        lff_hz=30, hff_hz=1500, notch_hz=0, primary_metric="amplitude_uv",
        description="Άμεσο φλοιονωτιαίο D-wave (επισκληρίδια καταγραφή)· κριτήριο σημαντικότητας 50% πτώση πλάτους.",
    )


def _modality_temg() -> dict[str, Any]:
    return _modality(
        "temg", "Triggered EMG (βίδα / ρίζα)", "cmap",
        time_max=40, ms_per_div=4, sensitivity_uv_per_div=100.0, averages=1,
        lff_hz=30, hff_hz=1500, notch_hz=0, primary_metric="threshold_ma",
        description="Διεγερτικό EMG: μυϊκό CMAP με κατώφλι διέγερσης (mA) για εγγύτητα βίδας/ρίζας.",
    )


def _modality_cranial_emg() -> dict[str, Any]:
    return _modality(
        "cranial_emg", "Κρανιακό / RLN EMG", "cmap",
        time_max=50, ms_per_div=5, sensitivity_uv_per_div=100.0, averages=1,
        lff_hz=30, hff_hz=1500, notch_hz=0, primary_metric="amplitude_uv",
        description="Κρανιακό/παλίνδρομο λαρυγγικό EMG: CMAP διεγερτικής απόκρισης και ελεύθερης δραστηριότητας.",
    )


def _modality_tcmep() -> dict[str, Any]:
    return _modality(
        "tcmep", "TcMEP — μυϊκό CMAP", "cmap",
        time_max=60, ms_per_div=6, sensitivity_uv_per_div=200.0, averages=1,
        lff_hz=30, hff_hz=1500, notch_hz=0, primary_metric="amplitude_uv",
        description="Διακρανιακό κινητικό προκλητό: πολυφασικό μυϊκό CMAP από train διέγερσης.",
    )


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d*\.?\d+", str(value))
    return float(match.group()) if match else None


def build_waveform_channels(planner: dict[str, Any], modality: dict[str, Any]) -> list[dict[str, Any]]:
    node = planner["node"]
    metadata = node.get("metadata") or {}
    key = modality["key"]

    # SSEP montage channels (Erb N9 / cervical N13 / cortical N20–P37) when the
    # node carries a curated ssep_montage so each generator trace matches the
    # real recording site rather than a generic muscle channel.
    if key in {"ssep_upper", "ssep_lower"}:
        montage_channels = (metadata.get("ssep_montage") or {}).get("channels") or []
        built = []
        for index, channel in enumerate(montage_channels[:6]):
            name = channel.get("name") or f"Κανάλι {index + 1}"
            built.append(
                {
                    "channel_id": f"ssep-{index + 1}-{name}",
                    "label": name,
                    "peak_label": name,
                    "lat_name": None,
                    "montage": channel.get("montage"),
                    "role": channel.get("role"),
                    "onset_ms": _to_float(channel.get("latency_ms")),
                    "roots": planner.get("roots", []),
                    "cranial_nerves": [],
                    "placement": channel.get("montage"),
                }
            )
        if built:
            return built

    # Triggered-EMG recording myotomes from the pedicle screw mapping metadata.
    if key == "temg":
        myotomes = (metadata.get("screw_mapping") or {}).get("recording_myotomes") or []
        built = []
        for index, muscle in enumerate(myotomes[:6]):
            built.append(
                {
                    "channel_id": f"temg-{index + 1}-{muscle}",
                    "label": muscle,
                    "lat_name": muscle,
                    "roots": planner.get("roots", []),
                    "cranial_nerves": [],
                    "placement": "Myotome CMAP (belly–tendon)",
                }
            )
        if built:
            return built

    if key == "baep":
        return [
            {
                "channel_id": "baep-i-v",
                "label": "BAEP I–V (Cz–A1/A2)",
                "peak_label": "BAEP",
                "lat_name": None,
                "roots": [],
                "cranial_nerves": ["VIII"],
                "placement": "Cz–A1/A2",
            }
        ]
    if key == "dwave":
        return [
            {
                "channel_id": "d-wave-epidural",
                "label": "D-wave (επισκληρίδιο)",
                "peak_label": "DWAVE",
                "lat_name": None,
                "roots": planner.get("roots", []),
                "cranial_nerves": [],
                "placement": "Epidural",
            }
        ]

    channel_plan = planner.get("clinical_plan", {}).get("channel_plan", [])
    if channel_plan:
        return [
            {
                "channel_id": channel.get("lat_name") or f"channel-{index + 1}",
                "label": channel.get("name_el") or channel.get("lat_name") or f"Channel {index + 1}",
                "lat_name": channel.get("lat_name"),
                "roots": channel.get("roots", []),
                "cranial_nerves": channel.get("cranial_nerves", []),
                "placement": channel.get("placement"),
            }
            for index, channel in enumerate(channel_plan[:6])
        ]

    label = node.get("title_el") or node.get("key") or modality["label"]
    return [
        {
            "channel_id": node.get("key"),
            "label": label,
            "lat_name": None,
            "roots": planner.get("roots", []),
            "cranial_nerves": planner.get("cranial_nerves", []),
            "placement": None,
        }
    ]


def build_scenario(
    scenario_key: str,
    title: str,
    modality: dict[str, Any],
    channels: list[dict[str, Any]],
    amplitude_scale: float,
    latency_shift: float,
    noise_scale: float,
    threshold_ma: float | None = None,
) -> dict[str, Any]:
    averaging = _SCENARIO_AVERAGING.get(scenario_key, 0.8)
    line_weight = _SCENARIO_LINE.get(scenario_key, 0.5)
    channel_payload = []
    for index, channel in enumerate(channels):
        spec = _channel_spec(modality, channel, index, scenario_key, amplitude_scale, latency_shift)
        metrics = channel_metrics(spec, modality, scenario_key, noise_scale, averaging, threshold_ma)
        samples = waveform_samples(spec, modality, scenario_key, metrics["noise_uv"], line_weight, index)
        channel_payload.append(
            {
                **channel,
                "samples": samples,
                "metrics": metrics,
                "status": channel_status(scenario_key),
                "teaching_points": teaching_points(modality, scenario_key, channel),
            }
        )

    return {
        "scenario_key": scenario_key,
        "title": title,
        "status": scenario_status(scenario_key),
        "summary": scenario_summary(modality, scenario_key),
        "acquisition": {
            "averages": _scenario_averages(modality, scenario_key),
            "averaging_quality": round(averaging, 2),
            "line_interference": "high" if line_weight >= 1.5 else "moderate" if line_weight >= 0.5 else "low",
        },
        "channels": channel_payload,
        "interpretation_tasks": interpretation_tasks(modality, scenario_key),
    }


def _scenario_averages(modality: dict[str, Any], scenario_key: str) -> int:
    nominal = int(modality.get("device", {}).get("averages", 1) or 1)
    if nominal <= 1:
        return 1
    return max(1, int(nominal * _SCENARIO_AVERAGING.get(scenario_key, 0.8)))


def _root_latency_offset(channel: dict[str, Any]) -> float:
    """Approximate the extra peripheral conduction time for distal myotomes so a
    lower-limb CMAP onsets later than an upper-limb CMAP."""
    roots = [str(root).upper() for root in (channel.get("roots") or [])]
    if any(root.startswith("S") for root in roots):
        return 16.0
    if any(root.startswith("L") for root in roots):
        return 12.0
    if any(root.startswith("T") for root in roots):
        return 6.0
    return 0.0


def _channel_spec(
    modality: dict[str, Any],
    channel: dict[str, Any],
    index: int,
    scenario_key: str,
    amplitude_scale: float,
    latency_shift: float,
) -> dict[str, Any]:
    key = modality["key"]
    family = modality["family"]
    base_amplitude = _BASE_AMPLITUDE_UV.get(key, 600.0)
    base_latency = _BASE_LATENCY_MS.get(key, 20.0)

    # A montage/curated channel carries its own real latency; only spread the
    # fallback muscle channels apart so multiple CMAP traces are distinguishable.
    onset = channel.get("onset_ms")
    if onset is None:
        onset = base_latency + _root_latency_offset(channel) + min(index, 5) * 0.6
    onset = float(onset) + latency_shift

    amplitude = base_amplitude * amplitude_scale * (1 - min(index, 5) * 0.04)
    if scenario_key == "stimulation_failure":
        amplitude *= 0.12

    if family == "evoked":
        return {
            "family": "evoked",
            "key": key,
            "onset": onset,
            "amplitude": amplitude,
            "profile": _evoked_profile(key, channel, onset),
        }

    duration = {"tcmep": 12.0, "temg": 6.0, "cranial_emg": 7.0}.get(key, 10.0)
    phases = {"tcmep": 3.2, "temg": 2.6, "cranial_emg": 2.6}.get(key, 3.0)
    return {
        "family": "cmap",
        "key": key,
        "onset": onset,
        "amplitude": amplitude,
        "duration": duration,
        "phases": phases,
    }


def _evoked_profile(key: str, channel: dict[str, Any], onset: float) -> list[tuple[str, float, int, float, float]]:
    """Named evoked-potential peaks as (label, centre_ms, sign, weight, width_ms)."""
    if key == "baep":
        return [
            ("I", 1.5, 1, 0.5, 0.18),
            ("II", 2.6, 1, 0.32, 0.20),
            ("III", 3.6, 1, 0.72, 0.22),
            ("IV", 4.6, 1, 0.50, 0.24),
            ("V", 5.6, 1, 1.0, 0.28),
            ("SN10", 7.6, -1, 0.30, 0.60),
        ]
    if key == "dwave":
        return [("D", onset, -1, 1.0, 0.7)]

    name = (channel.get("peak_label") or channel.get("label") or "").upper()
    base = onset
    if "N9" in name or "ERB" in name:
        return [("N9", base, -1, 1.0, 1.0)]
    if "N13" in name:
        return [("N13", base, -1, 0.9, 1.3)]
    if "N18" in name:
        return [("N18", base, -1, 0.55, 1.7)]
    if "N20" in name:
        return [("N20", base, -1, 1.0, 1.3), ("P25", base + 5.0, 1, 0.7, 2.1), ("N33", base + 13.0, -1, 0.3, 3.0)]
    if "P37" in name:
        return [("P37", base, 1, 1.0, 2.3), ("N45", base + 8.0, -1, 0.7, 3.1)]
    if "N22" in name or "LP" in name or "LUMBAR" in name:
        return [("N22", base, -1, 0.95, 1.7)]
    if "PF" in name or "POPLITEAL" in name or "IGN" in name:
        return [("PF", base, -1, 1.0, 1.1)]
    return [("N", base, -1, 1.0, 1.6), ("P", base + 5.0, 1, 0.55, 2.2)]


def _signal_value(spec: dict[str, Any], t: float) -> float:
    if spec["family"] == "evoked":
        total = 0.0
        for _label, center, sign, weight, width in spec["profile"]:
            total += sign * weight * gaussian(t, center, width)
        return spec["amplitude"] * total
    return spec["amplitude"] * _cmap_unit(t, spec["onset"], spec["duration"], spec["phases"])


def _cmap_unit(t: float, onset: float, duration: float, phases: float) -> float:
    """Polyphasic compound muscle action potential: a Gaussian envelope around the
    motor latency modulating a negative-leading high-frequency oscillation."""
    if t <= onset:
        return 0.0
    tau = t - onset
    if tau >= duration * 1.7:
        return 0.0
    envelope = exp(-((tau - duration * 0.45) ** 2) / (2 * (duration * 0.32) ** 2))
    oscillation = sin(2 * pi * phases * tau / duration)
    return -envelope * oscillation


def _spec_components(spec: dict[str, Any]) -> list[dict[str, Any]]:
    if spec["family"] == "evoked":
        amplitude = spec["amplitude"]
        return [
            {"label": label, "latency_ms": round(center, 2), "amplitude_uv": round(amplitude * weight, 3)}
            for (label, center, _sign, weight, _width) in spec["profile"]
        ]
    onset = spec["onset"]
    return [
        {"label": "onset", "latency_ms": round(onset, 2), "amplitude_uv": 0.0},
        {"label": "peak", "latency_ms": round(onset + spec["duration"] * 0.45, 2), "amplitude_uv": round(spec["amplitude"], 2)},
    ]


def _primary_latency(spec: dict[str, Any]) -> float:
    if spec["family"] == "cmap":
        return spec["onset"]
    dominant = max(spec["profile"], key=lambda item: item[3])
    return dominant[1]


def _reproducibility(scenario_key: str) -> int:
    return {
        "baseline": 96,
        "amplitude_drop": 72,
        "latency_shift": 78,
        "combined_warning": 60,
        "threshold_change": 80,
        "anesthetic_suppression": 70,
        "artifact": 38,
        "stimulation_failure": 12,
    }.get(scenario_key, 70)


def channel_metrics(
    spec: dict[str, Any],
    modality: dict[str, Any],
    scenario_key: str,
    noise_scale: float,
    averaging: float,
    threshold_ma: float | None = None,
) -> dict[str, Any]:
    key = modality["key"]
    base_noise = _BASE_NOISE_UV.get(key, 20.0)
    noise = base_noise * noise_scale / max(averaging, 0.2)
    amplitude = spec["amplitude"]
    metrics = {
        "amplitude_uv": round(amplitude, 2),
        "latency_ms": round(_primary_latency(spec), 2),
        "noise_uv": round(noise, 2),
        "signal_to_noise": round(abs(amplitude) / max(noise, 0.001), 2),
        "reproducibility_percent": _reproducibility(scenario_key),
        "components": _spec_components(spec),
    }
    if key == "temg":
        metrics["threshold_ma"] = threshold_ma_for_scenario(scenario_key, threshold_ma)
    return metrics


def threshold_ma_for_scenario(scenario_key: str, threshold_ma: float | None) -> float:
    if scenario_key == "baseline":
        return 14
    if scenario_key == "threshold_change":
        return round(float(threshold_ma or DEFAULT_PARAMETERS["threshold_ma"]), 1)
    if scenario_key in {"amplitude_drop", "latency_shift", "combined_warning"}:
        return 8
    if scenario_key == "stimulation_failure":
        return 30
    return 11


def waveform_samples(
    spec: dict[str, Any],
    modality: dict[str, Any],
    scenario_key: str,
    noise_uv: float,
    line_weight: float,
    channel_index: int,
) -> list[dict[str, float]]:
    time_min = float(modality["time_min"])
    time_max = float(modality["time_max"])
    device = modality.get("device", {})
    notch = device.get("notch_hz")
    has_artifact = device.get("stim_artifact", True)
    points = SAMPLE_POINTS

    seed = _seed_for(modality["key"], spec.get("onset"), scenario_key, channel_index)
    rng = random.Random(seed)
    # Band-limited baseline noise: a lightly smoothed Gaussian series looks like
    # filtered physiological/electrode noise rather than white static.
    smoothed = _smooth([rng.gauss(0.0, 1.0) for _ in range(points)])

    # Mains interference is strongly attenuated when a notch filter is engaged.
    line_uv = noise_uv * line_weight * (0.18 if notch else 1.0)
    line_hz = float(notch) if notch else 50.0

    samples = []
    for index in range(points):
        t = time_min + (time_max - time_min) * index / (points - 1)
        value = _signal_value(spec, t)
        value += smoothed[index] * noise_uv
        value += line_uv * sin(2 * pi * line_hz * t / 1000.0)
        if has_artifact:
            value += _stim_artifact(t, spec["amplitude"])
        samples.append({"t": round(t, 2), "value": round(value, 3)})
    return samples


def _stim_artifact(t: float, amplitude: float) -> float:
    """Sharp stimulus artifact at sweep onset that decays within ~1.5 ms, before
    the earliest physiologic peak."""
    if t < 0 or t > 4.0:
        return 0.0
    return abs(amplitude) * 1.4 * exp(-t / 0.4)


def _smooth(values: list[float], window: int = 3) -> list[float]:
    if window <= 1:
        return values
    half = window // 2
    count = len(values)
    smoothed = []
    for index in range(count):
        low = max(0, index - half)
        high = min(count, index + half + 1)
        smoothed.append(sum(values[low:high]) / (high - low))
    return smoothed


def _seed_for(*parts: Any) -> int:
    return zlib.crc32("|".join(str(part) for part in parts).encode("utf-8"))


def gaussian(t: float, center: float, width: float) -> float:
    return exp(-((t - center) ** 2) / (2 * width**2))


def channel_status(scenario_key: str) -> str:
    if scenario_key == "baseline":
        return "acceptable"
    if scenario_key in {"amplitude_drop", "latency_shift", "combined_warning", "threshold_change", "anesthetic_suppression"}:
        return "review"
    return "artifact"


def scenario_status(scenario_key: str) -> str:
    return {
        "baseline": "stable",
        "amplitude_drop": "warning",
        "latency_shift": "warning",
        "combined_warning": "warning",
        "threshold_change": "warning",
        "anesthetic_suppression": "review",
        "artifact": "technical",
        "stimulation_failure": "technical",
    }.get(scenario_key, "review")


def scenario_summary(modality: dict[str, Any], scenario_key: str) -> str:
    if scenario_key == "baseline":
        return f"Stable {modality['label']} training response with usable morphology and signal-to-noise."
    if scenario_key == "amplitude_drop":
        return "Dominant amplitude loss pattern requiring channel-specific comparison with baseline and technical checks."
    if scenario_key == "latency_shift":
        return "Latency shift pattern requiring temperature, anesthetic, stimulation and physiologic correlation."
    if scenario_key == "combined_warning":
        return "Combined amplitude loss and latency shift pattern that should trigger structured escalation workflow."
    if scenario_key == "threshold_change":
        return "Threshold-response change drill for stimulation safety and screw/root proximity reasoning."
    if scenario_key == "anesthetic_suppression":
        return "Global suppression pattern that must be differentiated from focal neural compromise."
    if scenario_key == "stimulation_failure":
        return "Near-absent response pattern requiring stimulation/acquisition chain troubleshooting."
    return "High noise/artifact pattern requiring technical troubleshooting before clinical interpretation."


def teaching_points(modality: dict[str, Any], scenario_key: str, channel: dict[str, Any]) -> list[str]:
    label = channel.get("label") or channel.get("channel_id") or "channel"
    if scenario_key == "baseline":
        return [
            f"Confirm reproducible morphology for {label} before using it as a comparator.",
            "Document baseline after positioning and before high-risk surgical steps.",
        ]
    if scenario_key in {"amplitude_drop", "latency_shift", "combined_warning", "threshold_change"}:
        return [
            f"Compare {label} against its own baseline rather than another muscle/channel.",
            "Check whether amplitude loss, latency shift and noise changed together or separately.",
        ]
    if scenario_key == "anesthetic_suppression":
        return [
            f"Check whether {label} changes in parallel with multiple channels rather than as a focal deficit.",
            "Correlate with anesthetic bolus, neuromuscular blockade, blood pressure and temperature.",
        ]
    if scenario_key == "stimulation_failure":
        return [
            f"Verify stimulation output, electrode connection and acquisition chain before interpreting {label} as absent.",
            "Document the technical correction that restores a reproducible response.",
        ]
    return [
        f"Treat {label} as technically suspect until impedance, cable path and stimulation settings are checked.",
        "Avoid interpreting an artifact-heavy trace as neural deterioration without corroboration.",
    ]


def interpretation_tasks(modality: dict[str, Any], scenario_key: str) -> list[str]:
    if scenario_key == "baseline":
        return [
            "Identify the main peak/response window.",
            "Record amplitude, latency and noise floor as baseline comparators.",
        ]
    if scenario_key == "amplitude_drop":
        return [
            "Quantify amplitude loss relative to baseline.",
            "List technical and anesthetic checks before escalation.",
        ]
    if scenario_key == "latency_shift":
        return [
            "Quantify latency shift and decide whether morphology remains interpretable.",
            "Correlate latency change with temperature, stimulation and physiologic context.",
        ]
    if scenario_key == "combined_warning":
        return [
            "Decide whether the dominant change is amplitude loss, latency shift or both.",
            "State what technical and clinical checks should happen before escalation.",
        ]
    if scenario_key == "threshold_change":
        return [
            "Interpret whether threshold change is reassuring, borderline or concerning for the modality.",
            "Name the technical factors that can falsely lower or raise stimulation threshold.",
        ]
    if scenario_key == "anesthetic_suppression":
        return [
            "Decide whether the pattern is global or focal.",
            "State which anesthesia/physiology variables must be checked.",
        ]
    if scenario_key == "stimulation_failure":
        return [
            "Separate absent response from acquisition/stimulation failure.",
            "List the checks needed before accepting the trace as true loss.",
        ]
    return [
        "Separate artifact from a physiologic signal change.",
        "List the technical corrections needed before accepting the trace.",
    ]


def build_waveform_quiz(modality: dict[str, Any], scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scenario_options = [{"value": scenario["scenario_key"], "label": scenario["title"]} for scenario in scenarios]
    warning_keys = [scenario["scenario_key"] for scenario in scenarios if scenario.get("status") == "warning"]
    return [
        {
            "id": "artifact_recognition",
            "type": "single_select",
            "prompt": "Which scenario must be treated as artifact/noise before clinical interpretation?",
            "options": scenario_options,
            "correct_values": ["artifact"],
        },
        {
            "id": "warning_patterns",
            "type": "multi_select",
            "prompt": f"Select the {modality['label']} scenarios that require warning/escalation reasoning.",
            "options": scenario_options,
            "correct_values": warning_keys,
        },
        {
            "id": "technical_first",
            "type": "multi_select",
            "prompt": "Before declaring true neurologic deterioration, which technical/physiology checks are required?",
            "options": [
                {"value": "baseline_comparison", "label": "Compare with the same channel baseline"},
                {"value": "impedance_cables", "label": "Check impedance, cable path and electrodes"},
                {"value": "anesthesia_physiology", "label": "Review anesthesia, temperature and blood pressure"},
                {"value": "ignore_noise", "label": "Ignore noise if amplitude is still visible"},
            ],
            "correct_values": ["baseline_comparison", "impedance_cables", "anesthesia_physiology"],
        },
    ]


def grade_waveform_quiz(lab: dict[str, Any], answers: dict[str, list[str]]) -> dict[str, Any]:
    results = []
    for question in lab.get("quiz", []):
        correct = set(question.get("correct_values", []))
        selected = set(answers.get(question["id"], []))
        passed = selected == correct
        results.append(
            {
                "question_id": question["id"],
                "passed": passed,
                "selected_values": sorted(selected),
                "correct_values": sorted(correct),
                "missing_values": sorted(correct - selected),
                "extra_values": sorted(selected - correct),
            }
        )
    passed_count = sum(1 for result in results if result["passed"])
    total_count = len(results)
    score = round((passed_count / total_count) * 100) if total_count else 0
    return {
        "score_percent": score,
        "passed": total_count > 0 and score >= 80,
        "passed_count": passed_count,
        "total_count": total_count,
        "results": results,
        "recommendations": waveform_recommendations(results),
    }


def waveform_recommendations(results: list[dict[str, Any]]) -> list[str]:
    recommendations = []
    for result in results:
        if result["passed"]:
            continue
        if result["question_id"] == "artifact_recognition":
            recommendations.append("Revisit artifact/noise recognition before interpreting physiologic signal loss.")
        elif result["question_id"] == "warning_patterns":
            recommendations.append("Recheck amplitude-drop, latency-shift and combined-warning waveform patterns.")
        else:
            recommendations.append("Review the technical-first checklist before escalation decisions.")
    return recommendations or ["Waveform quiz passed. Continue with case correlation drills."]