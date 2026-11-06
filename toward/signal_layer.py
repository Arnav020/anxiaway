"""Fixed-code Signal Layer: turns sensor samples into a state label.

It never decides anything clinical, and no LLM reads its raw values.
Thresholds are starting design values, not clinically validated.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    CALIBRATING = "CALIBRATING"
    LOW = "LOW"
    ENGAGED = "ENGAGED"
    HIGH = "HIGH"
    LIMIT = "LIMIT"
    UNKNOWN = "UNKNOWN"


# Starting values from solution_blueprint.md, in bpm above read-aloud baseline.
LOW_BELOW = 5.0
HIGH_ABOVE = 20.0
LIMIT_ABOVE = 35.0
HIGH_HOLD_S = 30
LIMIT_HOLD_S = 30
SUSTAINED_HIGH_S = 120
HR_WINDOW_S = 10
MOTION_WINDOW_S = 5
MOTION_REJECT_G = 0.25
MIN_QUALITY = 0.5
MIN_CALIBRATION_SAMPLES = 15
EDA_RISE_RATIO = 1.10


@dataclass(frozen=True)
class Sample:
    t: int
    ecg_hr: float | None
    ppg_hr: float | None
    eda: float | None
    motion: float | None
    quality: float

    @property
    def hr(self) -> float | None:
        return self.ecg_hr if self.ecg_hr is not None else self.ppg_hr

    @property
    def active(self) -> list[str]:
        channels = [
            ("ECG", self.ecg_hr),
            ("PPG", self.ppg_hr),
            ("EDA", self.eda),
            ("IMU", self.motion),
        ]
        return [name for name, value in channels if value is not None]


@dataclass(frozen=True)
class Reading:
    t: int
    state: State
    hr_delta: float | None
    eda: float | None
    motion: float | None


@dataclass(frozen=True)
class SignalMessage:
    """The only output other components may rely on."""

    state: State
    confidence: float
    active: list[str]
    motion: bool

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "confidence": round(self.confidence, 2),
            "active": self.active,
            "motion": self.motion,
        }


class SignalLayer:
    def __init__(self) -> None:
        self.baseline_hr: float | None = None
        self.baseline_eda: float | None = None
        self._hr = deque(maxlen=HR_WINDOW_S)
        self._motion = deque(maxlen=MOTION_WINDOW_S)
        self._above_high = 0
        self._above_limit = 0

    def to_state(self) -> dict:
        return {
            "baseline_hr": self.baseline_hr,
            "baseline_eda": self.baseline_eda,
            "hr": list(self._hr),
            "motion": list(self._motion),
            "above_high": self._above_high,
            "above_limit": self._above_limit,
        }

    @classmethod
    def from_state(cls, state: dict) -> "SignalLayer":
        layer = cls()
        layer.baseline_hr = state["baseline_hr"]
        layer.baseline_eda = state["baseline_eda"]
        layer._hr.extend(state["hr"])
        layer._motion.extend(state["motion"])
        layer._above_high = state["above_high"]
        layer._above_limit = state["above_limit"]
        return layer

    def calibrate(self, samples: list[Sample]) -> bool:
        """Set the baseline from neutral read-aloud, so talking isn't mistaken for fear."""
        hrs = [s.hr for s in samples if s.hr is not None and s.quality >= MIN_QUALITY]
        if len(hrs) < MIN_CALIBRATION_SAMPLES:
            return False
        self.baseline_hr = sum(hrs) / len(hrs)
        edas = [s.eda for s in samples if s.eda is not None]
        self.baseline_eda = sum(edas) / len(edas) if edas else None
        return True

    def _reset_holds(self) -> None:
        self._above_high = 0
        self._above_limit = 0

    def restart_after_pause(self) -> None:
        """A safety pause ends the episode, so the old reading can't count as a new limit."""
        self._reset_holds()
        self._hr.clear()
        self._motion.clear()

    def _classify(self, sample: Sample) -> tuple[State, float | None]:
        if sample.motion is not None:
            self._motion.append(sample.motion)
        moving = bool(self._motion) and (
            sum(self._motion) / len(self._motion) > MOTION_REJECT_G
        )
        hr = sample.hr
        if self.baseline_hr is None or hr is None or sample.quality < MIN_QUALITY or moving:
            self._reset_holds()
            self._hr.clear()
            return State.UNKNOWN, None

        self._hr.append(hr)
        delta = sum(self._hr) / len(self._hr) - self.baseline_hr
        self._above_high = self._above_high + 1 if delta > HIGH_ABOVE else 0
        self._above_limit = self._above_limit + 1 if delta > LIMIT_ABOVE else 0

        if self._above_limit >= LIMIT_HOLD_S or self._above_high >= SUSTAINED_HIGH_S:
            return State.LIMIT, delta
        if self._above_high >= HIGH_HOLD_S:
            return State.HIGH, delta
        if delta < LOW_BELOW:
            return State.LOW, delta
        return State.ENGAGED, delta

    def _confidence(self, state: State, sample: Sample) -> float:
        if state is State.UNKNOWN:
            return 0.0
        score = 0.6 if sample.ecg_hr is not None else 0.5
        if sample.motion is not None:
            score += 0.1
        # EDA can only raise confidence. It can never trigger a state alone.
        if (
            sample.eda is not None
            and self.baseline_eda
            and sample.eda >= self.baseline_eda * EDA_RISE_RATIO
        ):
            score += 0.2
        return min(score, 0.9)

    def process(self, samples: list[Sample]) -> tuple[list[Reading], SignalMessage]:
        """Classify one question turn. Any LIMIT inside the turn counts."""
        readings: list[Reading] = []
        states: list[State] = []
        for sample in samples:
            state, delta = self._classify(sample)
            states.append(state)
            readings.append(Reading(sample.t, state, delta, sample.eda, sample.motion))

        if not samples:
            return readings, SignalMessage(State.UNKNOWN, 0.0, [], False)

        final = State.LIMIT if State.LIMIT in states else states[-1]
        last = samples[-1]
        moving = bool(self._motion) and (
            sum(self._motion) / len(self._motion) > MOTION_REJECT_G
        )
        return readings, SignalMessage(final, self._confidence(final, last), last.active, moving)
