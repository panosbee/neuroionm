"""Deterministic DSP measurement layer (advisory).

This turns a raw evoked-response sweep (physical-unit samples decoded by
:func:`signal_import.extract_channel_sweeps`) into the quantitative measurements
that the published alert engine consumes — peak-to-peak amplitude, peak/onset
latency, and the inter-sweep latency shift estimated by template
cross-correlation. It also exposes the spectral/temporal primitives used by the
artifact/context gate (line-noise, broadband, transient ratios).

Design constraints (deliberate):

* **Pure Python, no numpy/scipy.** Mirrors the dependency-free EDF reader, so the
  whole pipeline stays immediately deployable with no native build step. The
  Goertzel algorithm gives exact mains-frequency power without a full FFT, and a
  bounded-lag time-domain cross-correlation gives the latency shift.
* **Advisory only.** Every value here is an AI-PROPOSED *measurement*, never a
  decision. The deterministic alert engine still owns the thresholds and the
  verdict; a human confirms or corrects the proposed numbers before they are
  used clinically. Nothing here invents a threshold.
"""

from __future__ import annotations

import math
from typing import Any


# Expected response windows per modality profile. `latency_kind` selects the
# measurement convention: evoked potentials report the dominant labelled peak,
# CMAPs report the onset of the motor response above the pre-stimulus noise.
MODALITY_PROFILES: dict[str, dict[str, Any]] = {
    "ssep_median": {"latency_kind": "peak", "window_ms": (15.0, 30.0), "label": "Median SSEP cortical (N20)"},
    "ssep_tibial": {"latency_kind": "peak", "window_ms": (30.0, 50.0), "label": "Tibial SSEP cortical (P37)"},
    "ssep_cortical": {"latency_kind": "peak", "window_ms": (15.0, 50.0), "label": "Cortical SSEP"},
    "baep": {"latency_kind": "peak", "window_ms": (5.0, 7.0), "label": "BAEP wave V"},
    "cmap": {"latency_kind": "onset", "window_ms": (1.0, 50.0), "label": "CMAP (TcMEP / MEP)"},
    "dwave": {"latency_kind": "peak", "window_ms": (0.5, 20.0), "label": "Epidural D-wave"},
}


def _window_indices(n: int, sample_rate: float, lo_ms: float, hi_ms: float) -> tuple[int, int]:
    if sample_rate <= 0 or n == 0:
        return 0, n
    lo = max(0, int(round(lo_ms / 1000.0 * sample_rate)))
    hi = min(n, int(round(hi_ms / 1000.0 * sample_rate)))
    if hi <= lo:
        return 0, n
    return lo, hi


def peak_to_peak(samples: list[float], lo: int = 0, hi: int | None = None) -> float:
    segment = samples[lo:hi]
    if not segment:
        return 0.0
    return round(max(segment) - min(segment), 4)


def rms(samples: list[float], lo: int = 0, hi: int | None = None) -> float:
    segment = samples[lo:hi]
    if not segment:
        return 0.0
    return math.sqrt(sum(value * value for value in segment) / len(segment))


def dominant_peak(samples: list[float], sample_rate: float, lo_ms: float, hi_ms: float) -> dict[str, Any] | None:
    """The largest absolute deflection inside the response window (labelled peak)."""
    lo, hi = _window_indices(len(samples), sample_rate, lo_ms, hi_ms)
    segment = samples[lo:hi]
    if not segment:
        return None
    rel = max(range(len(segment)), key=lambda i: abs(segment[i]))
    index = lo + rel
    return {
        "index": index,
        "latency_ms": round(index / sample_rate * 1000.0, 2) if sample_rate else None,
        "amplitude": round(segment[rel], 4),
    }


def onset_latency(
    samples: list[float],
    sample_rate: float,
    lo_ms: float,
    hi_ms: float,
    noise_rms: float,
    sigma: float = 3.0,
) -> float | None:
    """First sample inside the window whose magnitude exceeds `sigma * noise_rms`."""
    if sample_rate <= 0:
        return None
    lo, hi = _window_indices(len(samples), sample_rate, lo_ms, hi_ms)
    threshold = sigma * noise_rms
    if threshold <= 0:
        return None
    for index in range(lo, hi):
        if abs(samples[index]) >= threshold:
            return round(index / sample_rate * 1000.0, 2)
    return None


def cross_correlation_lag(baseline: list[float], current: list[float], max_lag: int) -> int:
    """Bounded-lag time-domain cross-correlation.

    Returns the integer sample lag that best aligns `current` onto `baseline`.
    A positive lag means the current response is *delayed* relative to baseline
    (a latency increase); negative means earlier.
    """
    n = min(len(baseline), len(current))
    if n == 0 or max_lag <= 0:
        return 0
    base = baseline[:n]
    cur = current[:n]
    best_lag = 0
    best_score = -math.inf
    for lag in range(-max_lag, max_lag + 1):
        score = 0.0
        count = 0
        for i in range(n):
            j = i + lag
            if 0 <= j < n:
                score += base[i] * cur[j]
                count += 1
        if count:
            score /= count
            if score > best_score:
                best_score = score
                best_lag = lag
    return best_lag


def linear_slope(values: list[float]) -> float:
    """Least-squares slope of `values` per index (units of value per sample step)."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(values))
    denominator = sum((x - mean_x) ** 2 for x in range(n))
    return numerator / denominator if denominator else 0.0


def goertzel_power(samples: list[float], sample_rate: float, freq: float) -> float:
    """Power at `freq` (Goertzel), scaled to be comparable with mean-square power."""
    n = len(samples)
    if n == 0 or sample_rate <= 0 or freq <= 0:
        return 0.0
    k = int(round(n * freq / sample_rate))
    if k <= 0 or k >= n:
        return 0.0
    omega = (2.0 * math.pi / n) * k
    coeff = 2.0 * math.cos(omega)
    s_prev = 0.0
    s_prev2 = 0.0
    for value in samples:
        s = value + coeff * s_prev - s_prev2
        s_prev2 = s_prev
        s_prev = s
    magnitude_sq = s_prev * s_prev + s_prev2 * s_prev2 - coeff * s_prev * s_prev2
    # |X_k|^2 / n^2 ~ A^2/4 for a tone of amplitude A; *2 makes it match the
    # tone's mean-square power A^2/2 so the ratio against total power is a true
    # power fraction in [0, 1].
    return 2.0 * magnitude_sq / (n * n)


def line_noise_ratio(samples: list[float], sample_rate: float, mains: tuple[float, ...] = (50.0, 60.0)) -> float:
    """Fraction of signal power concentrated at mains frequencies (and harmonics)."""
    if not samples:
        return 0.0
    total = sum(value * value for value in samples) / len(samples)
    if total <= 0:
        return 0.0
    freqs: list[float] = []
    for base in mains:
        freqs.extend([base, base * 2.0])
    noise = sum(goertzel_power(samples, sample_rate, freq) for freq in freqs)
    return min(1.0, noise / total)


def broadband_ratio(samples: list[float]) -> float:
    """High-frequency content proxy (first-difference energy / signal energy).

    Elevated for broadband EMG contamination; low for smooth evoked responses.
    """
    if len(samples) < 2:
        return 0.0
    energy = sum(value * value for value in samples)
    if energy <= 0:
        return 0.0
    diff = sum((samples[i] - samples[i - 1]) ** 2 for i in range(1, len(samples)))
    return diff / (2.0 * energy)


def transient_ratio(samples: list[float]) -> float:
    """Largest sample-to-sample jump relative to the sweep RMS (electrode pop)."""
    if len(samples) < 2:
        return 0.0
    reference = rms(samples)
    if reference <= 0:
        return 0.0
    jump = max(abs(samples[i] - samples[i - 1]) for i in range(1, len(samples)))
    return jump / reference


def spectral_features(samples: list[float], sample_rate: float) -> dict[str, float]:
    """The temporal/spectral descriptors used by the artifact/context gate."""
    return {
        "line_noise_ratio": round(line_noise_ratio(samples, sample_rate), 4),
        "broadband_ratio": round(broadband_ratio(samples), 4),
        "transient_ratio": round(transient_ratio(samples), 4),
        "rms": round(rms(samples), 4),
    }


def _latency(
    sweep: list[float],
    sample_rate: float,
    profile: dict[str, Any],
    noise_rms: float,
) -> float | None:
    lo_ms, hi_ms = profile["window_ms"]
    if profile["latency_kind"] == "onset":
        return onset_latency(sweep, sample_rate, lo_ms, hi_ms, noise_rms)
    peak = dominant_peak(sweep, sample_rate, lo_ms, hi_ms)
    return peak["latency_ms"] if peak else None


def extract_measurements(
    baseline: list[float],
    current: list[float],
    sample_rate: float,
    profile_key: str,
    *,
    scale_to_uv: float | None = None,
    noise_window_ms: tuple[float, float] = (0.0, 5.0),
    max_lag_ms: float = 5.0,
) -> dict[str, Any]:
    """Propose amplitude/latency measurements for a baseline vs current sweep.

    `scale_to_uv` multiplies raw physical samples into microvolts (the alert
    engine's amplitude unit). Returns an advisory measurement set plus the
    `baseline`/`current` dicts ready to hand to `alert_engine.analyze_signal`.
    The caller (and ultimately a clinician) confirms before the numbers are used.
    """
    profile = MODALITY_PROFILES.get(profile_key)
    if profile is None:
        raise ValueError(f"Unknown modality profile: {profile_key!r}")

    factor = scale_to_uv if scale_to_uv is not None else 1.0
    base_uv = [value * factor for value in baseline]
    cur_uv = [value * factor for value in current]

    lo_ms, hi_ms = profile["window_ms"]
    lo_idx, hi_idx = _window_indices(len(base_uv), sample_rate, lo_ms, hi_ms)
    cur_lo, cur_hi = _window_indices(len(cur_uv), sample_rate, lo_ms, hi_ms)

    baseline_pp = peak_to_peak(base_uv, lo_idx, hi_idx)
    current_pp = peak_to_peak(cur_uv, cur_lo, cur_hi)

    noise_lo, noise_hi = _window_indices(len(base_uv), sample_rate, noise_window_ms[0], noise_window_ms[1])
    baseline_noise = rms(base_uv, noise_lo, noise_hi)
    current_noise = rms(cur_uv, noise_lo, noise_hi)

    baseline_latency = _latency(base_uv, sample_rate, profile, baseline_noise)
    current_latency = _latency(cur_uv, sample_rate, profile, current_noise)

    max_lag = max(1, int(round(max_lag_ms / 1000.0 * sample_rate))) if sample_rate else 0
    lag = cross_correlation_lag(base_uv[lo_idx:hi_idx], cur_uv[lo_idx:hi_idx], max_lag)
    latency_shift_ms = round(lag / sample_rate * 1000.0, 2) if sample_rate else None

    amplitude_drop_percent = None
    if baseline_pp > 0:
        amplitude_drop_percent = round(max(0.0, (baseline_pp - current_pp) / baseline_pp * 100.0), 1)

    latency_increase_percent = None
    if baseline_latency and current_latency and baseline_latency > 0:
        latency_increase_percent = round(max(0.0, (current_latency - baseline_latency) / baseline_latency * 100.0), 1)

    return {
        "method": "advisory_dsp",
        "profile": profile_key,
        "profile_label": profile["label"],
        "sample_rate_hz": sample_rate,
        "baseline": {"amplitude_uv": round(baseline_pp, 4), "latency_ms": baseline_latency},
        "current": {"amplitude_uv": round(current_pp, 4), "latency_ms": current_latency},
        "derived": {
            "amplitude_drop_percent": amplitude_drop_percent,
            "latency_increase_percent": latency_increase_percent,
            "latency_shift_ms": latency_shift_ms,
        },
        "quality": {
            "baseline_noise_rms_uv": round(baseline_noise, 4),
            "current_noise_rms_uv": round(current_noise, 4),
            "current_features": spectral_features(cur_uv, sample_rate),
        },
        "advisory": "AI-proposed measurements; require clinician confirmation before clinical use.",
    }
