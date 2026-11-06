"""Care logic in fixed code: GAD-7 scoring, the model verdict and the care pathway.

The verdict is SIMULATED until the wearable app and our anxiety model are integrated.
When the app connects, it sends a real verdict, which replaces the simulated one.
Screening only. Nothing here is a diagnosis.
"""
from __future__ import annotations

from .signal_layer import SignalMessage, State

GAD_ITEMS = [
    "Feeling nervous, anxious or on edge",
    "Not being able to stop or control worrying",
    "Worrying too much about different things",
    "Trouble relaxing",
    "Being so restless that it's hard to sit still",
    "Becoming easily annoyed or irritable",
    "Feeling afraid, as if something awful might happen",
]
GAD_OPTIONS = ["Not at all", "Several days", "More than half the days", "Nearly every day"]
VERDICT_LABELS = ("low", "moderate", "high", "unavailable")

_SIMULATED = {
    State.LOW: ("low", 0.21),
    State.ENGAGED: ("moderate", 0.56),
    State.HIGH: ("high", 0.79),
    State.LIMIT: ("high", 0.93),
    State.UNKNOWN: ("unavailable", None),
    State.CALIBRATING: ("unavailable", None),
}
PATHWAYS = {
    "Minimal": {
        "name": "Stay aware",
        "why": "Your screening is in the minimal range, so light practice and tracking fit best.",
        "steps": ["Practise when this situation comes up", "Repeat this check-in in two weeks"],
    },
    "Mild": {
        "name": "Guided practice",
        "why": "Your screening is in the mild range, so step by step practice is a reasonable next step.",
        "steps": ["Follow your practice ladder", "Repeat this check-in in two weeks", "Talk to someone if things get worse"],
    },
    "Moderate": {
        "name": "Guided practice, with professional support",
        "why": "Your screening is in the moderate range. Practice can help you prepare, and it's worth also speaking to a counsellor or doctor.",
        "steps": ["Book a conversation with a counsellor or doctor", "Share this summary with them", "Follow your practice ladder at a comfortable pace"],
    },
    "Severe": {
        "name": "Professional care first",
        "why": "Your screening is in the severe range, so a counsellor or doctor is the right first step.",
        "steps": ["Speak to a counsellor or doctor"],
    },
}


def gad_band(score: int) -> str:
    if score >= 15:
        return "Severe"
    if score >= 10:
        return "Moderate"
    if score >= 5:
        return "Mild"
    return "Minimal"


def gad_concerns(items: list[int], limit: int = 3) -> list[str]:
    """The items answered most strongly, for the person to recognise themselves in."""
    ranked = sorted(range(len(items)), key=lambda i: -items[i])
    return [GAD_ITEMS[i] for i in ranked[:limit] if items[i] >= 1]


def model_verdict(message: SignalMessage, external: dict | None = None) -> dict:
    """The app's real verdict when connected. Otherwise a labelled simulation from the signal state."""
    if external:
        return {"label": external["label"], "probability": external.get("probability"), "source": "Model"}
    label, probability = _SIMULATED[message.state]
    return {"label": label, "probability": probability, "source": "Simulated"}


def verdict_summary(verdicts: list[dict]) -> dict:
    known = [v for v in verdicts if v["label"] != "unavailable"]
    order = {"low": 0, "moderate": 1, "high": 2}
    if not known:
        return {"peak": "unavailable", "high_share": 0, "readings": 0, "source": "Simulated"}
    peak = max(known, key=lambda v: order[v["label"]])["label"]
    return {
        "peak": peak,
        "high_share": round(sum(v["label"] == "high" for v in known) / len(known), 2),
        "readings": len(known),
        "source": known[-1]["source"],
    }
