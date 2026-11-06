"""Fixed-code Safety Governor. It can overrule every LLM.

Keyword detection will miss cases, and the cut-offs need counsellor review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .signal_layer import State

GAD7_REFER_AT = 15
SESSION_LIMIT_S = 20 * 60
MAX_LIMIT_EVENTS = 2

CRISIS_RESOURCES = (
    "You don't have to handle this alone. If you may be in danger, call your local emergency "
    "number now. In India, Tele-MANAS is 14416. In the UK and Ireland, Samaritans are 116 123. "
    "Please also contact someone you trust."
)
REFERRAL_TEXT = (
    "Toward isn't the right fit right now. Please speak to a counsellor or doctor, "
    "who can offer proper support."
)
MEDICAL_TEXT = (
    "We've ended the session. If you have chest pain, faintness or trouble breathing, "
    "please get medical help right away."
)

_CRISIS = re.compile(
    r"(suicid|kill myself|end my life|want to die|self[- ]?harm|hurt myself|no reason to live)",
    re.I,
)
_PHYSICAL = re.compile(
    r"(chest pain|chest hurts|faint|can'?t breathe|cannot breathe|trouble breathing|passing out)",
    re.I,
)
_STOP = re.compile(
    r"^\s*(stop|end session)\s*[.!]?\s*$|i want to stop|please stop|stop the session|end the session",
    re.I,
)
_UNSAFE_OUTPUT = re.compile(
    r"(suicid|self[- ]?harm|you (seem|look|sound) (anxious|nervous|stressed|scared)|"
    r"you have (anxiety|a disorder)|diagnos|your score|out of 10)",
    re.I,
)


class Action(str, Enum):
    ALLOW = "ALLOW"
    REFER = "REFER"
    PAUSE = "PAUSE"
    STOP = "STOP"
    CRISIS = "CRISIS"
    MEDICAL = "MEDICAL"


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    message: str = ""


ALLOW = Decision(Action.ALLOW, "No safety rule triggered")


def check_entry(gad7: int, self_harm: bool, heart_condition: bool, crisis: bool) -> Decision:
    if self_harm or crisis:
        return Decision(Action.CRISIS, "Entry: self harm response or current crisis", CRISIS_RESOURCES)
    if heart_condition:
        return Decision(Action.REFER, "Entry: heart condition", "Please get medical advice before using Toward.")
    if gad7 >= GAD7_REFER_AT:
        return Decision(Action.REFER, f"Entry: GAD-7 at or above {GAD7_REFER_AT}", REFERRAL_TEXT)
    return ALLOW


def check_user_text(text: str) -> Decision:
    if _CRISIS.search(text):
        return Decision(Action.CRISIS, "Self-harm language: fixed response, no LLM", CRISIS_RESOURCES)
    if _PHYSICAL.search(text):
        return Decision(Action.MEDICAL, "Physical symptom mentioned", MEDICAL_TEXT)
    if _STOP.search(text):
        return Decision(Action.STOP, "User asked to stop", "Session stopped. That's completely fine.")
    return ALLOW


def check_state(state: State, limit_events: int) -> Decision:
    """limit_events includes the current one."""
    if state is not State.LIMIT:
        return ALLOW
    if limit_events >= MAX_LIMIT_EVENTS:
        return Decision(
            Action.STOP,
            "Safety limit reached twice",
            "We've ended this session because the safety limit was reached twice.",
        )
    return Decision(
        Action.PAUSE,
        "Safety limit reached",
        "You can stop here or continue, and either choice is completely fine.",
    )


def check_session(elapsed_s: int) -> Decision:
    if elapsed_s >= SESSION_LIMIT_S:
        return Decision(Action.STOP, "Session time limit reached", "That's the session time limit. Well done for practising.")
    return ALLOW


def output_is_safe(text: str) -> bool:
    """Reject panel lines that comment on the user's state, diagnose or score."""
    return not _UNSAFE_OUTPUT.search(text)
