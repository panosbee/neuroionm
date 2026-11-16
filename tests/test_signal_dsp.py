"""Tests for the advisory DSP measurement layer (item #8).

Synthetic evoked sweeps with known peaks/latencies validate that the pure-Python
DSP proposes the correct peak-to-peak amplitude, peak latency, latency shift and
spectral descriptors. Every measurement here is advisory input to the
deterministic alert engine; these tests pin the measurement maths, not a verdict.
"""

import math

import pytest

from backend.app.services import signal_dsp
from backend.app.services.signal_import import extract_channel_sweeps

from test_signal_import import build_edf


def _bump(n, center, amplitude, width=4.0):
    return [amplitude * math.exp(-((i - center) ** 2) / (2.0 * width * width)) for i in range(n)]


def _sine(n, sample_rate, freq, amplitude=1.0):
    return [amplitude * math.sin(2.0 * math.pi * freq * i / sample_rate) for i in range(n)]


def test_peak_to_peak_and_rms():
    samples = [0.0, 3.0, -1.0, 2.0]
    assert signal_dsp.peak_to_peak(samples) == 4.0
    assert signal_dsp.rms([3.0, 4.0]) == pytest.approx(math.sqrt(12.5))


def test_dominant_peak_finds_known_latency():
    sample_rate = 4000.0  # 0.25 ms / sample
    sweep = _bump(200, 80, 50.0)  # peak at sample 80 -> 20 ms
    peak = signal_dsp.dominant_peak(sweep, sample_rate, 15.0, 30.0)
    assert peak is not None
    assert peak["latency_ms"] == pytest.approx(20.0, abs=0.3)
    assert peak["amplitude"] == pytest.approx(50.0, abs=0.5)


def test_onset_latency_above_noise():
    sample_rate = 4000.0
    sweep = [0.0] * 200
    for i in range(60, 200):
        sweep[i] = 30.0  # response starts at 15 ms
    latency = signal_dsp.onset_latency(sweep, sample_rate, 1.0, 50.0, noise_rms=1.0, sigma=3.0)
    assert latency == pytest.approx(15.0, abs=0.3)


def test_cross_correlation_recovers_known_shift():
    baseline = _bump(120, 40, 1.0)
    current = _bump(120, 46, 1.0)  # delayed by 6 samples
    lag = signal_dsp.cross_correlation_lag(baseline, current, max_lag=20)
    assert lag == 6


def test_linear_slope():
    assert signal_dsp.linear_slope([0.0, 1.0, 2.0, 3.0, 4.0]) == pytest.approx(1.0)
    assert signal_dsp.linear_slope([5.0, 5.0, 5.0]) == pytest.approx(0.0)


def test_goertzel_detects_line_noise():
    sample_rate = 1000.0
    mains = _sine(1000, sample_rate, 50.0, amplitude=10.0)
    clean = _sine(1000, sample_rate, 5.0, amplitude=10.0)
    assert signal_dsp.line_noise_ratio(mains, sample_rate) > 0.8
    assert signal_dsp.line_noise_ratio(clean, sample_rate) < 0.1


def test_broadband_and_transient_primitives():
    sample_rate = 1000.0
    smooth = _sine(500, sample_rate, 10.0, amplitude=5.0)
    noisy = [(-1.0) ** i * 5.0 for i in range(500)]  # alternating = max high-frequency
    assert signal_dsp.broadband_ratio(noisy) > signal_dsp.broadband_ratio(smooth)

    flat = [1.0] * 100
    spike = [1.0] * 100
    spike[50] = 80.0
    assert signal_dsp.transient_ratio(spike) > signal_dsp.transient_ratio(flat)


def test_extract_measurements_proposes_drop_and_shift():
    sample_rate = 4000.0
    baseline = _bump(200, 80, 50.0)  # N20 at 20 ms, pp ~50 uV
    current = _bump(200, 88, 20.0)  # delayed to 22 ms, amplitude down to 20 uV
    result = signal_dsp.extract_measurements(baseline, current, sample_rate, "ssep_median")

    assert result["method"] == "advisory_dsp"
    assert result["baseline"]["latency_ms"] == pytest.approx(20.0, abs=0.3)
    assert result["current"]["latency_ms"] == pytest.approx(22.0, abs=0.3)
    assert result["derived"]["amplitude_drop_percent"] == pytest.approx(60.0, abs=3.0)
    assert result["derived"]["latency_shift_ms"] == pytest.approx(2.0, abs=0.3)
    assert "confirmation" in result["advisory"].lower()


def test_extract_measurements_feeds_alert_engine():
    from backend.app.services.alert_engine import analyze_signal

    sample_rate = 4000.0
    baseline = _bump(200, 80, 50.0)
    current = _bump(200, 80, 10.0)  # 80% amplitude loss, no latency change
    result = signal_dsp.extract_measurements(baseline, current, sample_rate, "ssep_median")

    verdict = analyze_signal(
        family="ssep_upper",
        baseline=result["baseline"],
        current=result["current"],
        channel_label="Median SSEP L",
    )
    assert verdict["metrics"]["amplitude_drop_percent"] >= 50.0
    assert verdict["is_alert"] is True


def test_extract_measurements_unknown_profile():
    with pytest.raises(ValueError):
        signal_dsp.extract_measurements([0.0], [0.0], 1000.0, "nonexistent")


def test_extract_channel_sweeps_decodes_full_waveform():
    signal = {"label": "SSEP L cortical", "dimension": "uV", "samples_per_record": 4}
    records = [
        [[330, -330, 200, -200]],
        [[230, -230, 100, -100]],
    ]
    raw = build_edf([signal], records, record_duration=0.001)
    extracted = extract_channel_sweeps(raw, 0)

    assert extracted["label"] == "SSEP L cortical"
    assert extracted["sample_rate_hz"] == pytest.approx(4000.0, abs=1.0)
    assert extracted["uv_factor"] == 1.0
    assert len(extracted["sweeps"]) == 2
    assert len(extracted["sweeps"][0]) == 4

    gain = 200.0 / 65535.0  # (phys_max - phys_min) / (dig_max - dig_min)
    assert extracted["sweeps"][0][0] == pytest.approx(330 * gain, abs=0.05)
    assert extracted["sweeps"][1][2] == pytest.approx(100 * gain, abs=0.05)


def test_extract_channel_sweeps_rejects_annotation_channel():
    ordinary = {"label": "TcMEP APB", "dimension": "uV", "samples_per_record": 2}
    annotation = {"label": "EDF Annotations", "dimension": "", "samples_per_record": 16}
    raw = build_edf([ordinary, annotation], [[[100, -100], b"+0\x14\x14\x00"]], reserved="EDF+C")
    with pytest.raises(Exception):
        extract_channel_sweeps(raw, 1)
