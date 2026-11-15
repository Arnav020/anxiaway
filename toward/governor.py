"""Fixed-code Safety Governor. It can overrule every LLM.

Phrase matching will still miss some wordings, so every language model output is scanned as well,
and the cut-offs need counsellor review.
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
    r"(suicid|kill(ing)? ?my ?self|\bkms\b|unalive|"
    r"end(ing|ed)? (it all|everything|my (own )?life|my existence)|tak(e|ing) my (own )?life|"
    r"(going to|gonna|will|want to|wanna|about to) end it\b|(end|stop) (it|this|the pain|everything|it all)( all)?,? (tonight|today|now|for good|forever|permanently)|(pain|suffering) to (end|stop),? (for good|forever|permanently)|"
    r"(letters?|notes?) to (my )?(parents|family|mum|mom|dad|friends|everyone)[^.]{0,20}explaining why|saying goodbye to (my )?(friends|family|everyone|people) one by one|(when|after|once) i'?m gone\b(?! for\b| on\b| to\b| away\b)|"
    r"can'?t go on( living| like this| anymore| any more)|"
    r"want(ed|ing)? to die|wish i (was|were) dead|rather be dead|better off (dead|without me)|"
    r"(not|n'?t) (want to )?(be|being) (alive|here) any ?more|do(n'?t| not) want to (live|be alive|exist|wake up)|"
    r"stop existing|never wake up|disappear forever|(nobody|no one|no-one) (will|would) (miss|notice) me|"
    r"(not|n'?t|isn'?t) worth living|no (reason|point) (to|in) liv|"
    r"self[- ]?harm|(hurt(ing)?|harm(ing)?|cut(ting)?|hang(ing)?|shoot(ing)?|drown(ing)?|poison(ing)?|burn(ing)?|stab(bing)?|starv(e|ing)) ?my ?self|"
    r"slit(ting)? my wrists?|overdos|lethal|"
    r"jump(ing)? (off|from|in front of) (a |the )?(bridge|building|roof|terrace|balcony|train|bus|truck|car|cliff|metro)|"
    r"(goodbye|suicide) note|saying goodbye to (everyone|everybody|you all)|"
    r"pills? (saved|stored|stockpiled)|saved up (pills|tablets)|how many (pills|tablets)|ways to die|how to die|planning (how to die|my death)|"
    r"marna chaht|mar jaana chaht|mar jana chaht|mar ja?ana hai|marna hai|jeena nahi|jeene ka ma?nn? nahi|zindagi (khatam|nahi chahiye)|"
    r"jaan de (du|doon|dunga|dungi)|khud ?kushi|aa?tma ?hatya|"
    r"खुदकुशी|आत्महत्या|मरना चाहत|मर जाना|जीना नहीं|जान दे)",
    re.I,
)
# People, and models, write around filters. These swaps undo the common ones before matching.
_LEET = str.maketrans({"1": "i", "!": "i", "3": "e", "0": "o", "@": "a", "$": "s", "4": "a", "5": "s", "*": "", "_": " ", "-": " "})


def crisis_in(text: str) -> bool:
    """True when a text reads as self harm, in plain or lightly disguised wording."""
    plain = re.sub(r"\s+", " ", text.lower())
    return bool(_CRISIS.search(plain) or _CRISIS.search(plain.translate(_LEET)))
_PHYSICAL = re.compile(
    r"(chest pain|chest hurts|\bfaint(ed|ing)?\b(?![- ]hearted)|can'?t breathe|cannot breathe|trouble breathing|passing out|"
    r"heart attack|seizure|lips? (are |is )?(turning |going )?blue|can'?t feel my (face|arm|arms|leg|legs|left side)|"
    r"took too many (of my )?(pills|tablets|meds))",
    re.I,
)
_STOP = re.compile(
    r"^\s*(stop|end session)\s*[.!]?\s*$|i want to stop( now| here| this| the session)?\s*[.!]?\s*$|please stop|stop the session|end the session",
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
    DANGER = "DANGER"
    OFF_TOPIC = "OFF_TOPIC"


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    message: str = ""
    source: str = "Backup rules"
    injection: bool = False


DANGER_TEXT = (
    "This sounds like a real safety concern, not a worry to test. Please talk to someone you trust "
    "or a professional. If you may be in danger, call your local emergency number. In India, "
    "Tele-MANAS is 14416. In the UK and Ireland, Samaritans are 116 123."
)
OFF_TOPIC_TEXT = "That doesn't read like a worry. Write what your anxiety says will happen, or a situation you've been avoiding."
_DANGER = re.compile(r"(abus|hits? me|beats? me|stalk|assault|rape|threaten|violen|(unsafe|not safe) at home)", re.I)


ALLOW = Decision(Action.ALLOW, "No safety rule triggered")


def check_entry(gad7: int, self_harm: bool, heart_condition: bool, crisis: bool) -> Decision:
    if self_harm or crisis:
        return Decision(Action.CRISIS, "Entry: self harm response or current crisis", CRISIS_RESOURCES)
    if heart_condition:
        return Decision(Action.REFER, "Entry: heart condition", "Please get medical advice before using Toward.")
    if gad7 >= GAD7_REFER_AT:
        return Decision(Action.REFER, f"Entry: GAD-7 at or above {GAD7_REFER_AT}", REFERRAL_TEXT)
    return ALLOW


def backup_rules(text: str) -> Decision:
    """The phrase rules. They decide alone only when the Safety agent can't be reached."""
    if crisis_in(text):
        return Decision(Action.CRISIS, "Backup rules: self-harm language, fixed response", CRISIS_RESOURCES)
    if _PHYSICAL.search(text):
        return Decision(Action.MEDICAL, "Backup rules: physical symptom mentioned", MEDICAL_TEXT)
    if _DANGER.search(text):
        return Decision(Action.DANGER, "Backup rules: harm from another person", DANGER_TEXT)
    if _STOP.search(text):
        return Decision(Action.STOP, "User asked to stop", "Session stopped. That's completely fine.")
    return ALLOW


def check_user_text(text: str, where: str = "answer") -> Decision:
    """Every typed message passes here before any other agent sees it."""
    from . import safety

    backup = backup_rules(text)
    # Stop always works, and backup crisis wording always counts: neither waits for a model.
    if backup.action in (Action.STOP, Action.CRISIS):
        return backup
    agent = safety.current()
    verdict = agent.screen(text, where) if agent and text.strip() else None
    # The agent can add caution but never remove it: backup medical or danger wording stands unless the agent sees a crisis.
    if backup.action in (Action.MEDICAL, Action.DANGER) and not (verdict and verdict.label == "crisis"):
        return backup
    if verdict is None:
        return backup
    why = f"Safety agent: {verdict.why}" if verdict.why else "Safety agent"
    if verdict.label == "crisis":
        return Decision(Action.CRISIS, why, CRISIS_RESOURCES, "Safety agent")
    if verdict.label == "medical":
        return Decision(Action.MEDICAL, why, MEDICAL_TEXT, "Safety agent")
    if verdict.label == "danger":
        return Decision(Action.DANGER, why, DANGER_TEXT, "Safety agent")
    if verdict.label == "off_topic":
        return Decision(Action.OFF_TOPIC, why, OFF_TOPIC_TEXT, "Safety agent")
    return Decision(Action.ALLOW, "Safety agent: no risk found", "", "Safety agent", verdict.injection)


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
    """Reject model text that comments on the user's state, diagnoses, scores, or touches self harm."""
    return not _UNSAFE_OUTPUT.search(text) and not crisis_in(text)
