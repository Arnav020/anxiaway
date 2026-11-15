"""The anxiety check, in fixed code: what the person reported, what their body showed, and how they compare.

The three sources are kept apart on purpose. Nothing here merges them into one invented score,
and nothing here is a diagnosis. Checks live in the person's browser. The server keeps none.
"""
from __future__ import annotations

import logging
import time
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from . import care
from .governor import Action, check_entry
from .signal_layer import State
from .state import BadToken, seal, unseal

log = logging.getLogger("toward.checks")

IMPACT_OPTIONS = ["Not difficult at all", "Somewhat difficult", "Very difficult", "Extremely difficult"]
FOCUS = ("Studies or work", "People and being judged", "Health", "The future", "No clear reason")
RECHECK_DAYS = 14
TOKEN_HOURS = 24
DAY_MS = 86_400_000
NOTICEABLE_CHANGE = 3
RISING_TOTAL = 5
_BAND_ORDER = {"Minimal": 0, "Mild": 1, "Moderate": 2, "Severe": 3}
_RAISED = {State.HIGH.value, State.LIMIT.value}
_NEAR = {State.LOW.value}

IMPACT_NOTE = (
    "You said this is making daily life very difficult. That matters more than the score, "
    "so it's worth speaking to a counsellor or doctor."
)
HEART_NOTE = "Guided sessions raise stress on purpose, so please get medical advice before trying one. The rest of Toward stays open to you."
RISING_NOTE = "Your screening scores have risen over your last checks. That's worth talking through with a counsellor or doctor."


class CheckEntry(BaseModel):
    at: int = Field(ge=0)
    score: int = Field(ge=0, le=21)
    impact: int | None = Field(default=None, ge=0, le=3)
    body_state: Literal["CALIBRATING", "LOW", "ENGAGED", "HIGH", "LIMIT", "UNKNOWN"] | None = None
    sample: bool = False


def parse_checks(raw: list[dict]) -> list[CheckEntry]:
    """A damaged entry in someone's browser must never break anything else."""
    parsed = []
    for item in raw:
        try:
            parsed.append(CheckEntry.model_validate(item))
        except ValidationError:
            log.warning("Dropped an invalid check entry.")
    return sorted(parsed, key=lambda c: c.at)


def trend(checks: list[CheckEntry], now_ms: int) -> dict:
    """Every number about the person's checks comes from here."""
    if not checks:
        return {"count": 0, "due": True, "latest_band": None, "latest_score": None, "days_since": None,
                "direction": None, "change": None, "rising": False, "impact": None, "body_state": None, "next": "screen"}
    latest = checks[-1]
    band = care.gad_band(latest.score)
    days = max(0, round((now_ms - latest.at) / DAY_MS))
    change = latest.score - checks[-2].score if len(checks) > 1 else None
    direction = None if change is None else "higher" if change >= NOTICEABLE_CHANGE else "lower" if change <= -NOTICEABLE_CHANGE else "about the same"
    last3 = [c.score for c in checks[-3:]]
    rising = len(last3) == 3 and last3[0] < last3[1] < last3[2] and last3[2] - last3[0] >= RISING_TOTAL
    hard = latest.impact is not None and latest.impact >= 2
    if band == "Severe" or (rising and _BAND_ORDER[band] >= 2) or hard:
        step = "person"
    elif days >= RECHECK_DAYS:
        step = "screen"
    else:
        step = None
    return {"count": len(checks), "due": days >= RECHECK_DAYS, "latest_band": band, "latest_score": latest.score,
            "days_since": days, "direction": direction, "change": change, "rising": rising,
            "impact": latest.impact, "body_state": latest.body_state, "next": step}


def agreement(band: str, body_state: str | None) -> dict:
    """How the questionnaire and the body reading compare. Neither is treated as the truth."""
    if body_state is None:
        return {"kind": "none", "text": "There was no body reading this time, so this result rests on your answers alone."}
    if body_state == State.UNKNOWN.value:
        return {"kind": "untrusted", "text": "The body reading wasn't trusted, because of movement or a weak signal, so it isn't used here."}
    if body_state == State.CALIBRATING.value:
        return {"kind": "baseline", "text": "This reading set your baseline. From your next check, your body reading is compared with it."}
    high_answers = _BAND_ORDER[band] >= 2
    if body_state in _RAISED and not high_answers:
        return {"kind": "differ", "text": f"Your answers were in the {band.lower()} range, while your body reading was well above your baseline. "
                "They often differ. A raised reading can also come from caffeine, exercise, illness or a warm room."}
    if body_state in _NEAR and high_answers:
        return {"kind": "differ", "text": f"Your answers were in the {band.lower()} range, while your body reading was near your baseline. "
                "Anxiety doesn't always show in the body at the moment of a reading."}
    return {"kind": "agree", "text": "Your answers and your body reading point the same way this time."}


def evaluate(gad_items: list[int], impact: int | None, self_harm: bool, heart_condition: bool, crisis: bool,
             body: dict | None, history: list[CheckEntry], now_ms: int) -> dict:
    score = sum(gad_items)
    band = care.gad_band(score)
    decision = check_entry(score, self_harm, heart_condition, crisis)
    if decision.action is Action.CRISIS:
        return {"status": "crisis", "exit_message": decision.message}
    status = "referred" if decision.action is Action.REFER else "ok"
    body_state = body["state"] if body else None
    entry = CheckEntry(at=now_ms, score=score, impact=impact, body_state=body_state)
    seen = trend([*history, entry], now_ms)
    notes = []
    if impact is not None and impact >= 2 and _BAND_ORDER[band] < 2:
        notes.append(IMPACT_NOTE)
    if seen["rising"]:
        notes.append(RISING_NOTE)
    result = {
        "status": status, "exit_message": "" if status == "ok" else HEART_NOTE if heart_condition and score < 15 else decision.message,
        "at": now_ms, "score": score, "band": band, "concerns": care.gad_concerns(gad_items),
        "impact": impact, "impact_text": None if impact is None else IMPACT_OPTIONS[impact],
        "items": [{"text": care.GAD_ITEMS[i], "value": v, "answer": care.GAD_OPTIONS[v]} for i, v in enumerate(gad_items)],
        "pathway": care.PATHWAYS[band], "notes": notes, "agreement": agreement(band, body_state),
        "trend": seen, "recheck_days": RECHECK_DAYS, "check_token": None,
    }
    if status == "ok":
        result["check_token"] = seal({"kind": "check", "at": now_ms, "gad_items": gad_items, "impact": impact,
                                      "body_state": body_state, "body_demo": bool(body) and body.get("source") != "device"})
    return result


def open_token(token: str, now_ms: int | None = None) -> dict:
    """The screening a guided session rests on. It must be recent, because safety questions come first."""
    state = unseal(token)
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    items = state.get("gad_items")
    if state.get("kind") != "check" or not isinstance(items, list) or len(items) != 7:
        raise BadToken("Not a check token")
    if now_ms - int(state.get("at", 0)) > TOKEN_HOURS * 3_600_000:
        raise BadToken("Check is too old")
    impact, body_state = state.get("impact"), state.get("body_state")
    return {"gad_items": [int(v) for v in items],
            "impact": impact if impact in (0, 1, 2, 3) else None,
            "body_state": body_state if body_state in {s.value for s in State} else None,
            "body_demo": bool(state.get("body_demo"))}
