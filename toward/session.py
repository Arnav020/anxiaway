"""One practice session: connects the Signal Layer, Governor and agents.

The whole session serialises to a dict, so it can travel in a signed token.
"""
from __future__ import annotations

import time

from . import care, checks, device, governor
from .agents import MAX_LEVEL, NEXT_STEP_TEXT, TACTIC_LABEL, AgentTeam
from .governor import Action
from .replay import stream_from_state
from .signal_layer import SignalLayer, State

MAX_TURNS = 6
GENTLE_LEVEL = 2
ADVANCE_IF_BELIEF_DROPS_BY = 15
READ_ALOUD = (
    "The river rises in the hills and moves slowly through the valley. Farmers along its banks "
    "plant rice in the wet season and wheat in the dry months. In the evening, boats return to "
    "the small harbour and the market closes for the night."
)
_POLICY_TEXT = {
    State.LOW: "Little change from baseline, so the challenge goes up at the next question.",
    State.ENGAGED: "Clear change from baseline, so the level holds and the test continues.",
    State.HIGH: "High arousal alone doesn't lower difficulty. The level holds, and stopping stays one tap away.",
    State.LIMIT: "Safety limit reached.",
    State.UNKNOWN: "Signal can't be trusted here, so body data isn't used. The level holds.",
}
_FIELDS = (
    "setup status level turn elapsed_s limit_events measured_tests ended_early exit_message question plan report "
    "history turn_log trail evidence last_answer verdicts post"
).split()


class _ScriptedTeam:
    """The scripted agents, called the way the live team is called."""

    def __init__(self, team: AgentTeam) -> None:
        self.scripted = team.scripted

    def take_notes(self) -> list[dict]:
        return []

    def direct(self, facts: dict):
        return self.scripted.direct(facts), "Scripted"

    def scene(self, facts: dict):
        return self.scripted.scene(facts), "Scripted"


def next_level(state: State, level: int) -> int:
    """Don't rescue by default: only LOW changes difficulty, and only upward."""
    return min(MAX_LEVEL, level + 1) if state is State.LOW else level


class Session:
    def __init__(self, setup: dict, team: AgentTeam, stream=None) -> None:
        self.team = team
        self.setup = setup
        self.stream = stream or stream_from_state(None)
        self.signal = SignalLayer()
        self.status = "created"
        self.level = 1
        self.turn = 0
        self.elapsed_s = 0
        self.limit_events = 0
        self.measured_tests = 0
        self.ended_early = False
        self.exit_message = ""
        self.question = None
        self.plan = None
        self.report = None
        self.last_answer = None
        self.history: list[dict] = []
        self.turn_log: list[dict] = []
        self.trail: list[dict] = []
        self.evidence: list[dict] = []
        self.verdicts: list[dict] = []
        self.post: dict = {}
        self.pipeline: list[dict] = []
        self.readings: list[dict] = []
        self.injected = False

    # Serialisation
    def to_state(self) -> dict:
        state = {name: getattr(self, name) for name in _FIELDS}
        state["signal"] = self.signal.to_state()
        state["stream"] = self.stream.to_state()
        return state

    @classmethod
    def from_state(cls, state: dict, team: AgentTeam) -> "Session":
        session = cls(state["setup"], team, stream_from_state(state["stream"]))
        for name in _FIELDS:
            setattr(session, name, state[name])
        session.signal = SignalLayer.from_state(state["signal"])
        return session

    # Logging
    def _log(self, who: str, text: str, ms: int | None = None) -> None:
        self.trail.append({"who": who, "text": text, "t": self.elapsed_s, "ms": ms})

    def _step(self, node: str, kind: str, summary: str, ms: float, source: str | None = None) -> None:
        self.pipeline.append({"node": node, "kind": kind, "summary": summary, "ms": round(ms, 1), "source": source})

    def _verifier_notes(self, team) -> None:
        for note in team.take_notes():
            self._step("verifier", "agent", note["summary"], note["ms"])
            self._log("Verifier agent", note["summary"], round(note["ms"]) or None)

    def _exit(self, decision: governor.Decision, status: str) -> None:
        self.status = status
        self.exit_message = decision.message
        self.question = None
        self._log("Safety Governor", decision.reason)

    # Flow
    def start(self) -> None:
        s = self.setup
        started = time.perf_counter()
        score = sum(s["gad_items"])
        decision = governor.check_entry(score, s["self_harm"], s["heart_condition"], s["crisis"])
        if decision.action is Action.CRISIS:
            return self._exit(decision, "crisis")
        if decision.action is Action.REFER:
            return self._exit(decision, "referred")
        # Free text is checked before any of it can reach the language model.
        # The situation and the prediction are read together, so a short situation like "Viva" has its context.
        for field in (f"Situation: {s['event']}. What my anxiety predicts: {s['prediction']}",):
            decision = governor.check_user_text(field, "worry")
            if decision.action is Action.CRISIS:
                return self._exit(decision, "crisis")
            if decision.action is Action.MEDICAL:
                return self._exit(decision, "medical")
            if decision.action is Action.DANGER:
                return self._exit(decision, "referred")
            if decision.action is Action.OFF_TOPIC:
                self.status = "unclear"
                self.exit_message = decision.message
                self._log(decision.source, decision.reason)
                return
        self._step("safety", "agent" if decision.source == "Safety agent" else "code", f"Entry checks passed ({decision.source.lower()})", (time.perf_counter() - started) * 1000)
        self._log(decision.source, "Entry checks passed: screening, safety answers, situation and prediction")

        started = time.perf_counter()
        impact, body_state = s.get("impact"), s.get("body_state")
        plan, source = self.team.plan({
            "event": s["event"], "days_until": s["days_until"], "prediction": s["prediction"],
            "gad_band": care.gad_band(score), "concerns": care.gad_concerns(s["gad_items"]),
            "record_note": s.get("record_note"),
            "daily_life_impact": None if impact is None else checks.IMPACT_OPTIONS[impact],
            "body_reading": device.BODY_TEXT[State(body_state)][0] if body_state else None,
        })
        ms = (time.perf_counter() - started) * 1000
        self._verifier_notes(self.team)
        self.plan = plan.model_dump()
        # Fixed code, not the planner, decides when the first step must start gently.
        reasons = self._gentle_reasons(care.gad_band(score), impact, body_state, bool(s.get("body_demo")))
        if reasons and self.plan["steps"][0]["difficulty"] > GENTLE_LEVEL:
            self.plan["steps"][0]["difficulty"] = GENTLE_LEVEL
        if reasons:
            self._log("Difficulty policy", f"Gentle start, because {' and '.join(reasons)}. The first step is capped at level {GENTLE_LEVEL}.")
        self.level = self.plan["steps"][0]["difficulty"]
        self.status = "planned"
        self._step("planner", "llm", f"Built a {len(plan.steps)} step ladder", ms, source)
        self._log(f"Ladder Planner ({source})", f"Built a {len(plan.steps)} step plan toward the event", round(ms))

    @staticmethod
    def _gentle_reasons(band: str, impact: int | None, body_state: str | None, demo: bool = False) -> list[str]:
        reasons = []
        if band == "Moderate":
            reasons.append("your screening is in the moderate range")
        if impact is not None and impact >= 2:
            reasons.append("anxiety is making daily life very difficult")
        if body_state in (State.HIGH.value, State.LIMIT.value):
            reasons.append(f"the {'demo ' if demo else ''}body reading at your check was well above {'the' if demo else 'your'} baseline")
        return reasons

    def _basis(self) -> dict:
        s = self.setup
        impact, body_state = s.get("impact"), s.get("body_state")
        return {
            "impact": None if impact is None else checks.IMPACT_OPTIONS[impact],
            "body": device.BODY_TEXT[State(body_state)][0] if body_state else None,
            "body_demo": bool(s.get("body_demo")),
            "gentle": self._gentle_reasons(care.gad_band(sum(s["gad_items"])), impact, body_state, bool(s.get("body_demo"))),
        }

    def calibrate(self) -> None:
        if self.status != "planned":
            raise ValueError("Calibration has already happened")
        started = time.perf_counter()
        samples = self.stream.calibration()
        self.elapsed_s += len(samples)
        ok = self.signal.calibrate(samples)
        text = ("Baseline set from neutral read aloud, so talking isn't mistaken for fear"
                if ok else "Calibration failed, so body data won't be used this session")
        self._step("signal", "code", text, (time.perf_counter() - started) * 1000)
        self._log("Signal Layer", text)
        self.status = "practice"
        self._ask(None, State.CALIBRATING, None, "unavailable")

    def _ask(self, answer: str | None, state: State, behaviour: dict | None, verdict_label: str = "unavailable") -> None:
        s = self.setup
        last = self.question["line"] if self.question else None
        started = time.perf_counter()
        # A turn after instruction-like text is written by the scripted agents, so the text can't steer them.
        team = _ScriptedTeam(self.team) if self.injected else self.team
        direction, source = team.direct({
            "event": s["event"], "prediction": s["prediction"], "state": state.value, "model_verdict": verdict_label, "turn": self.turn,
            "last_question": last, "user_answer": answer, "behaviour": behaviour,
        })
        ms = (time.perf_counter() - started) * 1000
        self._step("director", "llm", f"{TACTIC_LABEL[direction.tactic]}, {direction.speaker}", ms, source)
        self._log(f"Director ({source})", f"{TACTIC_LABEL[direction.tactic]} from {direction.speaker}. {direction.reason}", round(ms))

        started = time.perf_counter()
        line, source = team.scene({
            "event": s["event"], "difficulty": self.level, "tactic": direction.tactic, "speaker": direction.speaker,
            "turn": self.turn, "last_question": last, "user_answer": answer,
        })
        ms = (time.perf_counter() - started) * 1000
        self._verifier_notes(team)
        self._step("scene", "llm", f"Wrote a level {self.level} line", ms, source)
        self._log(f"Scene Agent ({source})", f"Wrote a level {self.level} line for {direction.speaker}", round(ms))
        self._step("check", "code", "Output check: no diagnosis, no scoring, no comment on nerves", 0.1)

        if self.status != "practice":
            return  # The user stopped while the line was being written. Stop wins.
        self.question = {"member": direction.speaker, "line": line, "tactic": direction.tactic,
                         "tactic_label": TACTIC_LABEL[direction.tactic], "level": self.level}

    def answer(self, text: str, source: str = "typed", pause_s: float | None = None,
               metrics: dict | None = None, inject: str | None = None, external_verdict: dict | None = None) -> None:
        if self.status != "practice":
            raise ValueError("Session isn't accepting answers")
        self.pipeline = []
        self.readings = []

        started = time.perf_counter()
        decision = governor.check_user_text(text, "answer")
        self._step("safety", "agent" if decision.source == "Safety agent" else "code", f"{decision.source} read the answer first: {'instruction-like text, treated as content' if decision.injection else 'no risk found' if decision.action is Action.ALLOW else decision.action.value.lower()}", (time.perf_counter() - started) * 1000)
        if decision.action is Action.CRISIS:
            return self._exit(decision, "crisis")
        if decision.action is Action.MEDICAL:
            return self._exit(decision, "medical")
        if decision.action is Action.DANGER:
            return self._exit(decision, "referred")
        self.injected = decision.injection
        if decision.action is Action.STOP:
            return self.stop(decision.reason)

        started = time.perf_counter()
        samples = self.stream.next_turn(inject)
        readings, message = self.signal.process(samples)
        self.readings = [
            {"t": r.t, "state": r.state.value, "delta": None if r.hr_delta is None else round(r.hr_delta, 2)} for r in readings
        ]
        self.elapsed_s += len(samples)
        channels = ", ".join(message.active) or "none"
        self._step("signal", "code", f"State {message.state.value}, sensors: {channels}", (time.perf_counter() - started) * 1000)
        self._log("Signal Layer", f"State {message.state.value}, confidence {message.confidence:.1f}, sensors: {channels}")

        verdict = care.model_verdict(message, external_verdict)
        self.verdicts.append({**verdict, "turn": self.turn + 1})
        odds = "" if verdict["probability"] is None else f" ({round(verdict['probability'] * 100)}%)"
        self._step("model", "model", f"{verdict['source']} verdict: {verdict['label']}{odds}", 0.1)
        self._log(f"Anxiety model ({verdict['source'].lower()})", f"Anxiety-like state: {verdict['label']}{odds}")
        # The model can only make the agent more careful. A high verdict blocks any step up.
        policy_state = State.ENGAGED if verdict["label"] == "high" and message.state is State.LOW else message.state

        metrics = metrics or {}
        target = self.plan["target_pause_s"]
        measured = source != "sample" and pause_s is not None
        held = bool(measured and pause_s >= target)
        self.measured_tests += 1 if held else 0
        self.evidence.append({
            "turn": self.turn + 1, "source": source, "pause_s": round(pause_s, 1) if measured else None,
            "target_s": target, "held": held, **{k: metrics.get(k) for k in ("words", "wpm", "longest_gap_s", "fillers", "apologies")},
        })
        self.history.append({"q": self.question["line"], "a": text})
        self.turn += 1
        self.turn_log.append({"turn": self.turn, "level": self.level, "state": message.state.value,
                              "confidence": message.confidence, "active": message.active, "injected": inject,
                              "verdict": verdict["label"], "probability": verdict["probability"]})

        if message.state is State.LIMIT:
            self.limit_events += 1
        started = time.perf_counter()
        decision = governor.check_state(message.state, self.limit_events)
        if decision.action is Action.ALLOW:
            decision = governor.check_session(self.elapsed_s)
        self._step("governor", "code", decision.reason, (time.perf_counter() - started) * 1000)

        if decision.action is Action.PAUSE:
            self.status = "paused"
            self.exit_message = decision.message
            self.question = None
            self._log("Safety Governor", decision.reason + ": paused and offered a choice")
            self.last_answer = text
            return
        if decision.action is Action.STOP:
            return self.stop(decision.reason, decision.message)

        old_level = self.level
        self.level = next_level(policy_state, self.level)
        change = f"Level {old_level} to {self.level}." if self.level != old_level else f"Level stays {self.level}."
        self._step("policy", "code", change, 0.1)
        self._log("Difficulty policy", f"{_POLICY_TEXT[policy_state]} {change}")
        self.last_answer = text

        if self.turn >= MAX_TURNS or self.stream.exhausted:
            self.status = "debrief"
            self.question = None
            self._log("Difficulty policy", "Planned questions finished")
            return
        behaviour = {"measured_pause_s": pause_s if measured else None, "apologies": metrics.get("apologies"),
                     "fillers": metrics.get("fillers"), "words": metrics.get("words")}
        self._ask(text, policy_state, behaviour, verdict["label"])

    def resume(self, choice: str) -> None:
        if self.status != "paused":
            raise ValueError("Session isn't paused")
        self.pipeline = []
        self.readings = []
        if choice == "stop":
            return self.stop("User chose to stop after the pause")
        self.status = "practice"
        self.exit_message = ""
        self.signal.restart_after_pause()
        self._log("Safety Governor", "User chose to continue. Level held, and a second limit ends the session.")
        self._ask(self.last_answer, State.LIMIT, None, "high")

    def stop(self, reason: str = "User pressed stop", message: str = "Session stopped. That's completely fine.") -> None:
        if self.status in ("crisis", "medical", "referred", "complete"):
            return
        self.ended_early = True
        self.status = "debrief"
        self.exit_message = message
        self.question = None
        self._log("Safety Governor", f"{reason}: stopped immediately, no persuasion")

    def finish(self, belief_after: int, post: dict | None = None) -> None:
        if self.status != "debrief":
            raise ValueError("Session isn't ready for a summary")
        post = post or {}
        note = post.get("note") or ""
        decision_note = governor.check_user_text(note, "note") if note.strip() else governor.ALLOW
        if decision_note.action is Action.CRISIS:
            return self._exit(decision_note, "crisis")
        if decision_note.action is Action.MEDICAL:
            return self._exit(decision_note, "medical")
        if decision_note.action is Action.DANGER:
            return self._exit(decision_note, "referred")
        self.pipeline = []
        self.readings = []
        self.post = post
        before = self.setup["belief_before"]
        change = belief_after - before
        peak_distress = post.get("peak_distress")
        distressed = peak_distress is not None and peak_distress >= 8
        if self.limit_events >= governor.MAX_LIMIT_EVENTS or (self.ended_early and change > 0):
            decision = "pause_plan"
        elif not self.ended_early and not distressed and -change >= ADVANCE_IF_BELIEF_DROPS_BY:
            decision = "advance"
        else:
            decision = "repeat"
        direction = "lower" if change < 0 else "higher" if change > 0 else "unchanged"
        score = sum(self.setup["gad_items"])
        band = care.gad_band(score)
        summary = care.verdict_summary(self.verdicts)
        started = time.perf_counter()
        debrief, source = self.team.debrief({
            "event": self.setup["event"], "prediction": self.setup["prediction"], "belief_before": before,
            "belief_after": belief_after, "belief_direction": direction, "ended_early": self.ended_early,
            "outcome": post.get("outcome"), "gad_band": band, "concerns": care.gad_concerns(self.setup["gad_items"]),
            "peak_distress_band": None if peak_distress is None else "high" if peak_distress >= 7 else "medium" if peak_distress >= 4 else "low",
            "symptoms": post.get("symptoms") or [], "note": note, "verdict_peak": summary["peak"], "verdict_source": summary["source"],
            "plan_decision": decision, "next_ladder_step": self._next_rung(),
        })
        ms = round((time.perf_counter() - started) * 1000)
        self._verifier_notes(self.team)
        if summary["source"] == "Simulated":
            # A simulated reading says nothing about the person, so no model may describe it as if it did.
            debrief.insight = (f"Your questionnaire was in the {band.lower()} range. The reading in this session was a simulated "
                               "demo signal, not your body, so it can't be compared with how you felt.")
        self._log("Difficulty policy", f"Plan decision: {decision.replace('_', ' ')} (decided in fixed code)")
        self._log(f"Care summary ({source})", "Wrote the summary. All numbers come from stored records.", ms)
        self.report = {
            "prediction": self.setup["prediction"], "event": self.setup["event"],
            "belief_before": before, "belief_after": belief_after,
            "measured_tests": self.measured_tests, "questions_answered": self.turn,
            "highest_level": max((t["level"] for t in self.turn_log), default=self.level),
            "limit_events": self.limit_events, "ended_early": self.ended_early,
            "decision": decision, "summary": debrief.summary, "insight": debrief.insight,
            "next_step": NEXT_STEP_TEXT[decision], "mission": debrief.mission, "next_practice": debrief.next_practice,
            "next_day": self._next_day(decision), "evidence": self.evidence, "source": source,
            "post": post, "verdict": summary, "verdicts": self.verdicts,
            "gad": self._gad_view(), "pathway": care.PATHWAYS[band],
        }
        self.status = "complete"

    def _next_rung(self) -> str | None:
        steps = (self.plan or {}).get("steps") or []
        return f"{steps[1]['title']}: {steps[1]['test']}" if len(steps) > 1 else None

    def _next_day(self, decision: str) -> int:
        steps = (self.plan or {}).get("steps") or []
        if decision == "advance" and len(steps) > 1:
            return max(1, steps[1]["day"] - steps[0]["day"])
        return 14 if decision == "pause_plan" else 2

    def _gad_view(self) -> dict:
        items = self.setup["gad_items"]
        score = sum(items)
        return {
            "score": score, "band": care.gad_band(score), "concerns": care.gad_concerns(items),
            "items": [{"text": care.GAD_ITEMS[i], "value": v, "answer": care.GAD_OPTIONS[v]} for i, v in enumerate(items)],
        }

    def view(self) -> dict:
        return {
            "status": self.status, "level": self.level, "turn": self.turn, "max_turns": MAX_TURNS,
            "elapsed_s": self.elapsed_s, "question": self.question, "exit_message": self.exit_message,
            "plan": self.plan, "read_aloud": READ_ALOUD, "report": self.report, "trail": self.trail,
            "turn_log": self.turn_log, "evidence": self.evidence, "belief_before": self.setup["belief_before"],
            "prediction": self.setup["prediction"], "event": self.setup["event"], "pipeline": self.pipeline,
            "readings": self.readings, "data_label": self.stream.label, "gad": self._gad_view(),
            "pathway": care.PATHWAYS[care.gad_band(sum(self.setup["gad_items"]))],
            "verdict": self.verdicts[-1] if self.verdicts else None, "record_note": self.setup.get("record_note"),
            "basis": self._basis(),
        }
