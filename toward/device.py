"""The wearable link: ready, and switched off until the device API is set.

Our capstone prototype streams ECG, PPG, skin conductance and motion to a mobile app, and that
app talks to its own backend. To connect it, set ANXIAWAY_DEVICE_API_URL to the endpoint that
returns the latest readings (and ANXIAWAY_DEVICE_API_KEY if it needs one). Nothing else changes:
the same Signal Layer, verdict slot and screens are used.

Until then every reading on screen comes from the synthetic stream, and is labelled that way.

Preferred response from the device API:
    {
      "samples": [{"t": 0, "ecg_hr": 78.2, "ppg_hr": 77.9, "eda": 4.01, "motion": 0.03, "quality": 0.95}],
      "verdict": {"label": "low | moderate | high", "probability": 0.72},
      "present": ["ECG", "PPG", "EDA", "IMU"]
    }
One sample a second is expected. Common other field names are accepted, see normalise().
"""
from __future__ import annotations

import logging
import os

import httpx

from . import care
from .replay import SEGMENTS, SyntheticStream
from .signal_layer import MIN_QUALITY, Sample, SignalLayer, State

log = logging.getLogger("toward.device")

SENSORS = ("ECG", "PPG", "EDA", "IMU", "EEG")
ON_DEVICE = ("ECG", "PPG", "EDA", "IMU")
SENSOR_TEXT = {
    "ECG": "Heart rhythm, from chest electrodes",
    "PPG": "Pulse, from light on the skin",
    "EDA": "Skin conductance",
    "IMU": "Movement",
    "EEG": "Brain activity",
}
WINDOW_S = 10
STRIDE_S = 5
READING_S = 45
BASELINE_S = 30
FETCH_TIMEOUT_S = 4.0
MAX_SAMPLES = 600
DEMO_SCENARIOS = ("calm", "rising", "high", "movement", "sensor_loss")
DEMO_LABEL = "Demo stream"
DEVICE_LABEL = "Wearable"

BODY_TEXT = {
    State.CALIBRATING: ("Baseline set", "This reading set your baseline. Later readings are compared with it."),
    State.LOW: ("Near your baseline", "Your heart rate stayed close to your own baseline."),
    State.ENGAGED: ("Above your baseline", "Your heart rate was clearly above your own baseline."),
    State.HIGH: ("Well above your baseline", "Your heart rate stayed well above your own baseline."),
    State.LIMIT: ("Far above your baseline", "Your heart rate stayed far above your own baseline."),
    State.UNKNOWN: ("Not trusted", "Movement or a weak signal, so this reading isn't used."),
}

_ALIASES = {
    "ecg_hr": ("ecg_hr", "ecg_bpm", "hr", "heart_rate", "bpm"),
    "ppg_hr": ("ppg_hr", "ppg_bpm", "pulse", "ppg"),
    "eda": ("eda", "eda_us", "gsr", "skin_conductance"),
    "motion": ("motion", "motion_g", "imu", "acc", "movement"),
    "quality": ("quality", "sqi", "signal_quality"),
}
_LABELS = {
    "low": "low", "calm": "low", "none": "low", "minimal": "low", "0": "low",
    "moderate": "moderate", "medium": "moderate", "mild": "moderate", "1": "moderate",
    "high": "high", "anxious": "high", "severe": "high", "2": "high",
}


def configured() -> bool:
    return bool(os.getenv("ANXIAWAY_DEVICE_API_URL") or os.getenv("TOWARD_DEVICE_API_URL"))


def availability(present: list[str] | tuple[str, ...]) -> list[dict]:
    """The mask the model receives: which of its five inputs are really there."""
    return [{"sensor": name, "text": SENSOR_TEXT[name], "on_device": name in ON_DEVICE, "present": name in present}
            for name in SENSORS]


def status() -> dict:
    return {
        "configured": configured(),
        "label": "Connected" if configured() else "Integrating soon",
        "sensors": availability(ON_DEVICE if configured() else ()),
        "window_s": WINDOW_S, "stride_s": STRIDE_S, "reading_s": READING_S,
        "scenarios": list(DEMO_SCENARIOS),
    }


def _number(row: dict, key: str) -> float | None:
    for name in _ALIASES[key]:
        value = row.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _verdict(payload: dict) -> dict | None:
    raw = payload.get("verdict") or payload.get("prediction_result") or payload
    if not isinstance(raw, dict):
        return None
    label = _LABELS.get(str(raw.get("label", raw.get("prediction", ""))).strip().lower())
    if label is None:
        return None
    probability = raw.get("probability", raw.get("confidence"))
    ok = isinstance(probability, (int, float)) and not isinstance(probability, bool) and 0 <= probability <= 1
    return {"label": label, "probability": round(float(probability), 2) if ok else None}


def normalise(payload: object) -> dict | None:
    """Turn the device API's reply into samples and an optional verdict. Anything unusable gives None."""
    if not isinstance(payload, dict):
        return None
    rows = next((payload[k] for k in ("samples", "readings", "windows", "data") if isinstance(payload.get(k), list)), None)
    if not rows:
        return None
    samples = []
    for i, row in enumerate(rows[-MAX_SAMPLES:]):
        if not isinstance(row, dict):
            continue
        quality = _number(row, "quality")
        t = row.get("t", row.get("t_s", i))
        samples.append(Sample(
            t=int(t) if isinstance(t, (int, float)) else i,
            ecg_hr=_number(row, "ecg_hr"), ppg_hr=_number(row, "ppg_hr"), eda=_number(row, "eda"),
            motion=_number(row, "motion"), quality=1.0 if quality is None else max(0.0, min(1.0, quality)),
        ))
    if not samples:
        return None
    return {"samples": samples, "verdict": _verdict(payload)}


def fetch_latest(get=httpx.get) -> dict | None:
    """Ask the device API for the latest readings. Any failure means no reading, never a guess."""
    url = os.getenv("ANXIAWAY_DEVICE_API_URL") or os.getenv("TOWARD_DEVICE_API_URL")
    if not url:
        return None
    key = os.getenv("ANXIAWAY_DEVICE_API_KEY") or os.getenv("TOWARD_DEVICE_API_KEY")
    try:
        response = get(url, headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=FETCH_TIMEOUT_S)
        response.raise_for_status()
        return normalise(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Device API unavailable (%s).", type(exc).__name__)
        return None


def demo_samples(scenario: str, t: int = 0, previous: float = 0.0) -> tuple[list[Sample], dict]:
    """One synthetic reading, continuing from where the last one ended."""
    name = scenario if scenario in DEMO_SCENARIOS else "calm"
    stream = SyntheticStream(t=max(0, int(t)), previous=float(previous) if abs(previous) <= SEGMENTS["limit"][0] else 0.0)
    samples = stream.next_turn(name)
    return samples, {"t": stream.t, "previous": stream.previous}


def demo_baseline() -> tuple[float, float]:
    layer = SignalLayer()
    layer.calibrate(SyntheticStream().calibration())
    return layer.baseline_hr, layer.baseline_eda


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def summarise(samples: list[Sample], baseline_hr: float | None, baseline_eda: float | None,
              external_verdict: dict | None = None, label: str = DEMO_LABEL) -> dict:
    """Fixed code only. The same Signal Layer the guided session uses reads the window."""
    layer = SignalLayer()
    first = baseline_hr is None
    if first:
        usable = layer.calibrate(samples)
        readings, message = [], None
        state = State.CALIBRATING if usable else State.UNKNOWN
    else:
        layer.baseline_hr, layer.baseline_eda = baseline_hr, baseline_eda
        found, message = layer.process(samples)
        readings = [{"t": r.t, "state": r.state.value, "delta": None if r.hr_delta is None else round(r.hr_delta, 2)} for r in found]
        state = message.state
    tail = samples[-WINDOW_S:]
    trusted = [s for s in tail if s.quality >= MIN_QUALITY]
    hr = _mean([s.hr for s in trusted if s.hr is not None])
    eda = _mean([s.eda for s in tail if s.eda is not None])
    present = [name for name in ON_DEVICE if any(name in s.active for s in tail)]
    base_hr = layer.baseline_hr
    title, text = BODY_TEXT[state]
    verdict = None
    if message is not None:
        verdict = care.model_verdict(message, external_verdict)
    elif external_verdict:
        verdict = {"label": external_verdict["label"], "probability": external_verdict.get("probability"), "source": "Model"}
    return {
        "source": label, "state": state.value, "title": title, "text": text,
        "hr": hr, "hr_delta": None if hr is None or base_hr is None or first else round(hr - base_hr, 1),
        "eda": eda, "motion": _mean([s.motion for s in tail if s.motion is not None]),
        "quality": _mean([s.quality for s in tail]),
        "confidence": 0.0 if message is None else round(message.confidence, 2),
        "heart_source": "ECG" if any(s.ecg_hr is not None for s in tail) else "PPG" if any(s.ppg_hr is not None for s in tail) else None,
        "present": present, "mask": availability(present),
        "baseline": {"hr": None if base_hr is None else round(base_hr, 2),
                     "eda": None if layer.baseline_eda is None else round(layer.baseline_eda, 3), "set_now": first and state is State.CALIBRATING},
        "windows": max(0, (len(samples) - WINDOW_S) // STRIDE_S + 1) if len(samples) >= WINDOW_S else 0,
        "seconds": len(samples), "readings": readings, "verdict": verdict,
    }


def sample_rows(samples: list[Sample]) -> list[dict]:
    return [{"t": s.t, "ecg_hr": s.ecg_hr, "ppg_hr": s.ppg_hr, "eda": s.eda, "motion": s.motion, "quality": s.quality} for s in samples]
