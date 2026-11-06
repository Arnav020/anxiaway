"""Sensor replay. Default data is SYNTHETIC, so it shows logic, never results.

A judge can choose what the next reading looks like, to watch the agent respond.
A real recording can replace it with TOWARD_REPLAY_CSV.
CSV columns: t_s, ecg_hr, ppg_hr, eda_us, motion_g, quality. Leave a value blank when a sensor is missing.
"""
from __future__ import annotations

import csv
import os
import random
from pathlib import Path

from .signal_layer import Sample

COLUMNS = ["t_s", "ecg_hr", "ppg_hr", "eda_us", "motion_g", "quality"]
CALIBRATION_S = 30
TURN_S = 45

_BASE_HR = 78.0
_BASE_EDA = 4.0
_RAMP_S = 6

# name: (bpm above baseline, motion in g, ECG connected, signal quality)
SEGMENTS = {
    "neutral": (0, 0.03, True, 0.95),
    "calm": (2, 0.03, True, 0.95),
    "steady": (3, 0.03, True, 0.95),
    "rising": (12, 0.03, True, 0.95),
    "high": (25, 0.03, True, 0.95),
    "movement": (26, 0.45, True, 0.70),
    "sensor_loss": (15, 0.03, False, 0.90),
    "limit": (39, 0.03, True, 0.95),
    "settled": (16, 0.03, True, 0.95),
}
INJECTABLE = ("calm", "rising", "high", "limit", "movement", "sensor_loss")
SCRIPT = ["calm", "rising", "high", "movement", "limit", "settled"]


class SyntheticStream:
    def __init__(self, seed: int = 7, t: int = 0, previous: float = 0.0, index: int = 0) -> None:
        self.seed, self.t, self.previous, self.index = seed, t, previous, index

    label = "Synthetic replay"

    def _segment(self, name: str, seconds: int) -> list[Sample]:
        delta, motion, ecg_on, quality = SEGMENTS[name]
        rng = random.Random(f"{self.seed}:{self.t}")
        samples = []
        for i in range(seconds):
            weight = min(1.0, (i + 1) / _RAMP_S)
            current = self.previous + (delta - self.previous) * weight
            hr = _BASE_HR + current
            samples.append(
                Sample(
                    t=self.t + i,
                    ecg_hr=round(hr + rng.gauss(0, 0.6), 2) if ecg_on else None,
                    ppg_hr=round(hr + rng.gauss(0, 1.2), 2),
                    eda=round(_BASE_EDA * (1 + current * 0.006) + rng.gauss(0, 0.02), 3),
                    motion=round(max(0.0, motion + rng.gauss(0, 0.008)), 3),
                    quality=quality,
                )
            )
        self.t += seconds
        self.previous = float(delta)
        return samples

    def calibration(self) -> list[Sample]:
        return self._segment("neutral", CALIBRATION_S)

    def next_turn(self, inject: str | None = None) -> list[Sample]:
        scripted = SCRIPT[min(self.index, len(SCRIPT) - 1)]
        self.index += 1
        return self._segment(inject if inject in INJECTABLE else scripted, TURN_S)

    @property
    def exhausted(self) -> bool:
        return False

    def to_state(self) -> dict:
        return {"kind": "synthetic", "seed": self.seed, "t": self.t, "previous": self.previous, "index": self.index}


class CsvStream:
    label = "Recorded replay"

    def __init__(self, samples: list[Sample], position: int = 0) -> None:
        self._samples, self._position = samples, position

    def _take(self, seconds: int) -> list[Sample]:
        chunk = self._samples[self._position : self._position + seconds]
        self._position += len(chunk)
        return chunk

    def calibration(self) -> list[Sample]:
        return self._take(CALIBRATION_S)

    def next_turn(self, inject: str | None = None) -> list[Sample]:
        return self._take(TURN_S)

    @property
    def exhausted(self) -> bool:
        return self._position >= len(self._samples)

    def to_state(self) -> dict:
        return {"kind": "csv", "position": self._position}


def generate(seed: int = 7) -> list[Sample]:
    stream = SyntheticStream(seed)
    samples = stream.calibration()
    for _ in SCRIPT:
        samples.extend(stream.next_turn())
    return samples


def write_csv(samples: list[Sample], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for s in samples:
            writer.writerow(["" if v is None else v for v in (s.t, s.ecg_hr, s.ppg_hr, s.eda, s.motion, s.quality)])


def _number(value: str) -> float | None:
    return float(value) if value.strip() else None


def load_csv(path: Path) -> list[Sample]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Replay CSV is missing columns: {sorted(missing)}")
        return [
            Sample(
                t=int(float(row["t_s"])),
                ecg_hr=_number(row["ecg_hr"]),
                ppg_hr=_number(row["ppg_hr"]),
                eda=_number(row["eda_us"]),
                motion=_number(row["motion_g"]),
                quality=float(row["quality"]),
            )
            for row in reader
        ]


def stream_from_state(state: dict | None):
    """Build the replay source, from saved state when there is one."""
    csv_path = os.getenv("TOWARD_REPLAY_CSV")
    if csv_path:
        return CsvStream(load_csv(Path(csv_path)), (state or {}).get("position", 0))
    if state and state.get("kind") == "synthetic":
        return SyntheticStream(state["seed"], state["t"], state["previous"], state["index"])
    return SyntheticStream()
