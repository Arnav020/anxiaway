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
    "You don't have to handle this alone. If you may be in danger right now, call 112 (India's emergency number) "
    "or your local emergency number. In India, Tele-MANAS is free on 14416, day and night. In the UK and Ireland, "
    "Samaritans are 116 123. Please also contact someone you trust, and stay with them if you can."
)
# Shown with the English text when the person writes in Hindi, in either script.
CRISIS_HINDI = (
    "आप अकेले नहीं हैं। अगर आप अभी खतरे में हैं, तो 112 पर कॉल करें। टेली-मानस 14416 पर दिन-रात "
    "मुफ़्त बात कर सकते हैं। किसी भरोसेमंद व्यक्ति से बात करें और हो सके तो उनके साथ रहें।"
)
REFERRAL_TEXT = (
    "AnxiAway isn't the right fit right now. Please speak to a counsellor or doctor, "
    "who can offer proper support."
)
MEDICAL_TEXT = (
    "We've ended the session. If you have chest pain, faintness or trouble breathing, call 112 (in India) or your "
    "local emergency number now, or ask someone near you to help. Please don't take medicine prescribed to someone else."
)
PANIC_TEXT = (
    "We've ended the session. What you describe can happen in a panic attack. It feels frightening, but panic rises, "
    "peaks and passes, usually within minutes. Breathe out slowly, longer than you breathe in, and name five things "
    "you can see. If you have chest pain, you faint, or this feels different from panic you've had before, call 112 "
    "(in India) or your local emergency number."
)
MEDICATION_TEXT = (
    "Please don't stop, start or change a medicine, or take one prescribed to someone else, without speaking to the "
    "doctor or pharmacist who prescribed it. That question is for them, not for this tool."
)
MINOR_TEXT = (
    "AnxiAway is built for adults, so it isn't the right tool for you. Please talk to a parent, a teacher you trust "
    "or your school counsellor. In India, Childline is 1098 and Tele-MANAS is 14416. In an emergency, call 112."
)
CONDITION_TEXT = (
    "Because of what you mentioned, practice that raises stress on purpose should be planned with a doctor or "
    "therapist first. Please talk to one before using guided practice here. Your record and the breathing pause stay open."
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
    r"(chest pain|chest (is|has been|feels) (really |very |so )?tight\b|chest hurts|\bfaint(ed|ing)?\b(?![- ]hearted)|can'?t breathe|cannot breathe|trouble breathing|passing out|"
    r"heart attack|seizure|lips? (are |is )?(turning |going )?blue|can'?t feel my (face|arm|arms|leg|legs|left side)|"
    r"took too many (of my )?(pills|tablets|meds))",
    re.I,
)
_PANIC = re.compile(r"(panic attack|panicking right now|having a panic|feels? unreal|everything (feels|looks|seems) unreal|derealis|depersonalis)", re.I)
_DRUGS = (r"(sertraline|fluoxetine|escitalopram|citalopram|paroxetine|venlafaxine|duloxetine|propranolol|alprazolam|xanax|"
          r"clonazepam|lorazepam|diazepam|valium|buspirone|mirtazapine|amitriptyline|lithium|antidepressants?|ssris?|"
          r"beta[- ]?blockers?|meds|medication|medicines?|tablets|pills)")
_MEDICATION = re.compile(
    rf"((stop|quit|skip|come off|coming off|cut down|double|increase|decrease|reduce|change|halve)\w* (taking |using )?(my |the |all my )?{_DRUGS}|"
    rf"(friend|mom|mum|dad|brother|sister|someone else|roommate|flatmate)'?s {_DRUGS}|"
    rf"(extra|more) {_DRUGS} (to calm|before (the|my))|{_DRUGS} without (a |my )?(prescription|doctor))",
    re.I,
)
_MINOR = re.compile(
    r"(\b(i'?m|i am|im|aged?) (1[0-7]|[6-9])( years?( old)?| yrs?( old)?| yo| y/o)?\b(?!\s*(minutes?|mins?|hours?|hrs?|percent|%|times|days|weeks|months|kg|seconds|secs|points?|marks?|am|pm|:|\.\d))|"
    r"\b(1[0-7]|[6-9])[- ]?(years?[- ]old|yrs?[- ]old|y/o)\b|"
    r"\bclass (6|7|8|9|10|vi|vii|viii|ix|x)(th)?\b(?!\s*(minutes?|mins?|hours?|hrs?|times|days|seconds|secs|:|\.\d))|\b(6|7|8|9|10)th (class|grade|standard|std)\b|\bgrade (6|7|8|9|10)\b)",
    re.I,
)
_CONDITION = re.compile(r"(\bptsd\b|post[- ]traumatic|\bpregnan(t|cy)\b|\bflashbacks?\b|\btrauma(tic)? (from|after|since)\b|"
                        r"eating disorder|\banorexi|\bbulimi|psychosis|bipolar)", re.I)
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_HINGLISH = re.compile(r"(marna|mar ja|jeena|jeene|zindagi|khud ?kushi|aa?tma ?hatya|jaan de|nahi chahiye|khatam)", re.I)

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
    "or a professional. If you may be in danger, call 112 (India's emergency number) or your local emergency number. "
    "In India, Tele-MANAS is 14416. In the UK and Ireland, Samaritans are 116 123."
)
HARM_OTHERS_TEXT = (
    "This sounds like someone could get hurt, so it isn't a worry to practise here. Please step away from the "
    "situation and talk to someone you trust now. If anyone is in immediate danger, call 112 (India's emergency number) "
    "or your local emergency number. Tele-MANAS (14416) can also help with strong anger, day and night."
)
ABUSE_TEXT = (
    "Nobody should hurt you, and this isn't something to practise or put up with. If you're in danger now, call 112 "
    "(India's emergency number) or your local emergency number. If you're under 18, Childline is 1098. Women in India "
    "can call the Women Helpline on 181. Tele-MANAS is 14416. Please tell an adult or a person you trust."
)
OFF_TOPIC_TEXT = "That doesn't read like a worry. Write what your anxiety says will happen, or a situation you've been avoiding."
_HARM_OTHERS = re.compile(
    r"((going to|gonna|will|want to|wanna|i'?ll) (hurt|kill|hit|beat|stab|attack|punch|shoot|harm) (him|her|them|someone|somebody|everyone|people|(my|the|that|this) (dad|father|mom|mum|mother|brother|sister|teacher|boss|examiner|interviewer|professor|classmate|friend|guy|man|woman|girl|boy|kid|neighbou?r))\b|"
    r"know where (he|she|they) (parks?|lives?|stays?)|make (him|her|them) pay|get revenge)",
    re.I,
)
_ABUSE = re.compile(r"(abus|hits? me|beats? me|beat me up|slaps? me|kicks? me|stalk|assault|rape|molest|threaten|violen|(unsafe|not safe|scared) at home)", re.I)
_DANGER = re.compile(f"({_ABUSE.pattern}|{_HARM_OTHERS.pattern})", re.I)


def crisis_message(text: str) -> str:
    """The fixed crisis text, with Hindi when the person wrote in Hindi."""
    if _DEVANAGARI.search(text) or _HINGLISH.search(text):
        return f"{CRISIS_RESOURCES} {CRISIS_HINDI}"
    return CRISIS_RESOURCES


def medical_message(text: str) -> str:
    """Medication questions, panic and physical warning signs each need different fixed words."""
    physical = _PHYSICAL.search(text)
    if _MEDICATION.search(text):
        return f"{MEDICAL_TEXT} {MEDICATION_TEXT}" if physical else MEDICATION_TEXT
    if _PANIC.search(text) and not re.search(r"chest pain|faint|heart attack|seizure|blue", text, re.I):
        return PANIC_TEXT
    return MEDICAL_TEXT


def danger_message(text: str) -> str:
    if _HARM_OTHERS.search(text):
        return HARM_OTHERS_TEXT
    if _ABUSE.search(text):
        return ABUSE_TEXT
    return DANGER_TEXT


def exclusion_message(text: str) -> str | None:
    """People this tool isn't for: children, and conditions where exposure needs a clinician's plan."""
    if _MINOR.search(text):
        return MINOR_TEXT
    if _CONDITION.search(text):
        return CONDITION_TEXT
    return None


ALLOW = Decision(Action.ALLOW, "No safety rule triggered")


def check_entry(gad7: int, self_harm: bool, heart_condition: bool, crisis: bool, adult: bool = True,
                other_condition: bool = False) -> Decision:
    if self_harm or crisis:
        return Decision(Action.CRISIS, "Entry: self harm response or current crisis", CRISIS_RESOURCES)
    if not adult:
        return Decision(Action.REFER, "Entry: under 18", MINOR_TEXT)
    if gad7 >= GAD7_REFER_AT:
        return Decision(Action.REFER, f"Entry: GAD-7 at or above {GAD7_REFER_AT}", REFERRAL_TEXT)
    if heart_condition:
        return Decision(Action.REFER, "Entry: heart condition", "Please get medical advice before a guided session.")
    if other_condition:
        return Decision(Action.REFER, "Entry: pregnancy, trauma, panic attacks, medication or substance use", CONDITION_TEXT)
    return ALLOW


def backup_rules(text: str) -> Decision:
    """The phrase rules. They decide alone only when the Safety agent can't be reached."""
    if crisis_in(text):
        return Decision(Action.CRISIS, "Backup rules: self-harm language, fixed response", crisis_message(text))
    if _PHYSICAL.search(text) or _PANIC.search(text) or _MEDICATION.search(text):
        return Decision(Action.MEDICAL, "Backup rules: physical symptom, panic or medication", medical_message(text))
    if _DANGER.search(text):
        return Decision(Action.DANGER, "Backup rules: harm from or to another person", danger_message(text))
    excluded = exclusion_message(text)
    if excluded:
        return Decision(Action.REFER, "Backup rules: under 18, pregnancy or a condition that needs a clinician", excluded)
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
    # The agent can add caution but never remove it: backup medical, danger or referral wording stands unless the agent sees a crisis.
    if backup.action in (Action.MEDICAL, Action.DANGER, Action.REFER) and not (verdict and verdict.label == "crisis"):
        return backup
    if verdict is None:
        return backup
    why = f"Safety agent: {verdict.why}" if verdict.why else "Safety agent"
    if verdict.label == "crisis":
        return Decision(Action.CRISIS, why, crisis_message(text), "Safety agent")
    if verdict.label == "medical":
        return Decision(Action.MEDICAL, why, medical_message(text), "Safety agent")
    if verdict.label == "danger":
        return Decision(Action.DANGER, why, danger_message(text), "Safety agent")
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
