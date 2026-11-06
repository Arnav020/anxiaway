import json

import groq
import httpx
import pytest
from fastapi.testclient import TestClient

from toward import governor
from toward.agents import ALLOWED_TACTICS, AgentTeam, GroqAgents, ScriptedAgents, speech_metrics
from toward.governor import Action
from toward.replay import CALIBRATION_S, TURN_S, SyntheticStream, generate, load_csv, write_csv
from toward.session import MAX_TURNS, Session, next_level
from toward.signal_layer import Sample, SignalLayer, State
from toward.state import BadToken, seal, unseal

EXPECTED_STATES = ["LOW", "LOW", "ENGAGED", "HIGH", "UNKNOWN", "ENGAGED", "LIMIT", "ENGAGED"]
SETUP = dict(event="Viva", days_until=6, prediction="If I pause, they'll think I'm incompetent",
             belief_before=80, gad7=9, self_harm=False, heart_condition=False, crisis=False, consent=True)


def scripted_team():
    team = AgentTeam(llm=None)
    team.llm = None
    return team


def flat(hr, seconds, motion=0.03, quality=0.95, eda=4.0, ecg=True):
    return [Sample(i, hr if ecg else None, hr, eda, motion, quality) for i in range(seconds)]


def calibrated():
    layer = SignalLayer()
    assert layer.calibrate(flat(78, 30))
    return layer


def started(setup=None, team=None):
    session = Session({**SETUP, **(setup or {})}, team or scripted_team())
    session.start()
    return session


class FlatStream:
    label = "Test"
    exhausted = False

    def __init__(self, hr):
        self.hr = hr

    def calibration(self):
        return flat(78, 30)

    def next_turn(self, inject=None):
        return flat(self.hr, 45)

    def to_state(self):
        return {"kind": "synthetic", "seed": 7, "t": 0, "previous": 0.0, "index": 0}


# Signal Layer
def test_synthetic_replay_produces_the_designed_states():
    layer = SignalLayer()
    samples = generate()
    assert layer.calibrate(samples[:CALIBRATION_S])
    states = []
    for i in range(MAX_TURNS):
        begin = CALIBRATION_S + i * TURN_S
        _, message = layer.process(samples[begin:begin + TURN_S])
        states.append(message.state.value)
        if message.state is State.LIMIT:
            layer.restart_after_pause()
    assert states == EXPECTED_STATES


def test_motion_rejects_a_heart_rate_rise():
    _, message = calibrated().process(flat(110, 40, motion=0.5))
    assert message.state is State.UNKNOWN and message.motion and message.confidence == 0


def test_poor_quality_and_missing_baseline_are_unknown():
    assert calibrated().process(flat(110, 40, quality=0.3))[1].state is State.UNKNOWN
    assert SignalLayer().process(flat(110, 40))[1].state is State.UNKNOWN


def test_too_little_calibration_data_fails():
    assert not SignalLayer().calibrate(flat(78, 5))


def test_ppg_replaces_a_lost_ecg_with_lower_confidence():
    with_ecg = calibrated().process(flat(90, 20))[1]
    without = calibrated().process(flat(90, 20, ecg=False))[1]
    assert without.state is State.ENGAGED and "ECG" not in without.active
    assert without.confidence < with_ecg.confidence


def test_eda_alone_cannot_trigger_a_state():
    assert calibrated().process(flat(78, 60, eda=20.0))[1].state is State.LOW


def test_high_needs_thirty_seconds_and_limit_needs_sustained_rise():
    assert calibrated().process(flat(100, 15))[1].state is State.ENGAGED
    assert calibrated().process(flat(100, 35))[1].state is State.HIGH
    assert calibrated().process(flat(120, 35))[1].state is State.LIMIT


def test_sustained_high_becomes_limit():
    assert calibrated().process(flat(100, 125))[1].state is State.LIMIT


def test_signal_state_survives_serialisation():
    layer = calibrated()
    layer.process(flat(100, 20))
    restored = SignalLayer.from_state(json.loads(json.dumps(layer.to_state())))
    assert restored.process(flat(100, 15))[1].state is State.HIGH


# Don't rescue by default
@pytest.mark.parametrize("state", [State.ENGAGED, State.HIGH, State.UNKNOWN, State.LIMIT])
def test_only_low_changes_difficulty(state):
    assert next_level(state, 3) == 3


def test_low_raises_and_caps_difficulty():
    assert next_level(State.LOW, 2) == 3 and next_level(State.LOW, 5) == 5


def test_high_arousal_never_allows_a_harder_tactic():
    assert "polite_challenge" not in ALLOWED_TACTICS[State.HIGH]
    assert "probe_assumption" not in ALLOWED_TACTICS[State.HIGH]
    assert ALLOWED_TACTICS[State.LIMIT] == {"open_question"}


# Governor
@pytest.mark.parametrize("args,action", [
    ((9, False, False, False), Action.ALLOW),
    ((15, False, False, False), Action.REFER),
    ((9, True, False, False), Action.CRISIS),
    ((9, False, True, False), Action.REFER),
    ((9, False, False, True), Action.CRISIS),
])
def test_entry_rules(args, action):
    assert governor.check_entry(*args).action is action


@pytest.mark.parametrize("text,action", [
    ("I want to kill myself", Action.CRISIS),
    ("I have chest pain", Action.MEDICAL),
    ("stop", Action.STOP),
    ("I want to stop", Action.STOP),
    ("I would stop and think about the sample size", Action.ALLOW),
])
def test_user_text_rules(text, action):
    assert governor.check_user_text(text).action is action


def test_panel_output_guard():
    assert not governor.output_is_safe("You seem anxious. Why is that?")
    assert not governor.output_is_safe("Your score is low.")
    assert governor.output_is_safe("What is the weakest part of your method?")


# Session
def run_all(session):
    session.calibrate()
    for _ in range(MAX_TURNS + 1):
        if session.status == "paused":
            session.resume("continue")
        if session.status != "practice":
            break
        session.answer("A sample answer about the method.", "sample")


def test_full_session_follows_the_policy():
    session = started()
    run_all(session)
    states = [t["state"] for t in session.turn_log]
    levels = [t["level"] for t in session.turn_log]
    assert states == EXPECTED_STATES
    assert levels == sorted(levels), "difficulty must never fall"
    assert levels[3] == levels[4], "HIGH must hold the level"
    assert session.status == "debrief"
    session.finish(55)
    assert session.report["belief_before"] == 80 and session.report["belief_after"] == 55
    assert session.report["decision"] == "advance" and session.status == "complete"


def test_full_session_works_through_tokens_alone():
    team = scripted_team()
    token = seal(started(team=team).to_state())
    session = Session.from_state(unseal(token), team)
    session.calibrate()
    for _ in range(MAX_TURNS + 1):
        session = Session.from_state(unseal(seal(session.to_state())), team)
        if session.status == "paused":
            session.resume("continue")
        if session.status != "practice":
            break
        session.answer("An answer.", "sample")
    assert [t["state"] for t in session.turn_log] == EXPECTED_STATES


def test_a_tampered_token_is_rejected():
    token = seal({"limit_events": 1})
    forged = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(BadToken):
        unseal(forged)


def test_judge_can_inject_a_spike():
    session = started()
    session.calibrate()
    session.answer("An answer.", "sample", inject="limit")
    assert session.status == "paused" and session.turn_log[-1]["injected"] == "limit"


def test_limit_pauses_and_offers_a_choice():
    session = started()
    session.calibrate()
    while session.status == "practice":
        session.answer("An answer.", "sample")
    assert session.status == "paused" and session.question is None
    session.resume("stop")
    assert session.status == "debrief" and session.ended_early


def test_a_second_real_limit_after_continuing_ends_the_session():
    session = Session(SETUP, scripted_team(), FlatStream(125))
    session.start()
    session.calibrate()
    session.answer("An answer.", "sample")
    assert session.status == "paused"
    session.resume("continue")
    session.answer("An answer.", "sample")
    assert session.status == "debrief" and session.limit_events == 2 and session.ended_early


def test_crisis_text_ends_with_fixed_resources_and_no_llm():
    session = started()
    session.calibrate()
    calls = []
    session.team.scene = lambda facts: calls.append(facts)
    session.team.direct = lambda facts: calls.append(facts)
    session.answer("I want to end my life", "voice")
    assert session.status == "crisis" and "14416" in session.exit_message and not calls
    with pytest.raises(ValueError):
        session.finish(10)


def test_crisis_text_in_setup_never_reaches_the_planner():
    team = scripted_team()
    called = []
    team.plan = lambda facts: called.append(facts)
    session = Session({**SETUP, "prediction": "I want to end my life after this viva"}, team)
    session.start()
    assert session.status == "crisis" and not called


def test_stop_during_a_slow_panel_line_wins():
    session = started()
    session.calibrate()
    original = session.team.scene

    def scene_with_stop(facts):
        session.stop()
        return original(facts)

    session.team.scene = scene_with_stop
    session.answer("An answer.", "sample")
    assert session.status == "debrief" and session.question is None


def test_entry_refusal_never_reaches_the_planner():
    team = scripted_team()
    called = []
    team.plan = lambda facts: called.append(facts)
    session = Session({**SETUP, "gad7": 18}, team)
    session.start()
    assert session.status == "referred" and not called


def test_measured_pause_counts_but_sample_answers_do_not():
    session = started()
    session.calibrate()
    target = session.plan["target_pause_s"]
    session.answer("An answer.", "voice", pause_s=target + 1)
    assert session.measured_tests == 1 and session.evidence[-1]["held"]
    session.answer("An answer.", "sample", pause_s=30)
    assert session.measured_tests == 1 and session.evidence[-1]["pause_s"] is None


def test_no_vitals_or_scores_reach_the_llm():
    seen = {"plan": [], "direct": [], "scene": [], "debrief": []}

    class Spy:
        source = "Groq"

        def plan(self, facts):
            seen["plan"].append(facts)

        def direct(self, facts):
            seen["direct"].append(facts)

        def scene(self, facts):
            seen["scene"].append(facts)

        def debrief(self, facts):
            seen["debrief"].append(facts)

    session = started(team=AgentTeam(llm=Spy()))
    run_all(session)
    session.finish(50)
    everything = [f for calls in seen.values() for f in calls]
    assert everything
    keys = {k.lower() for facts in everything for k in facts}
    for banned in ("hr", "hr_delta", "bpm", "eda", "motion", "confidence", "gad7"):
        assert banned not in keys
    blob = json.dumps(everything)
    assert "78." not in blob, "heart values must never appear"
    assert not any("state" in facts for facts in seen["scene"]), "the Scene Agent never sees the state"
    assert not any("state" in facts for facts in seen["plan"] + seen["debrief"])


def test_director_cannot_pick_a_forbidden_tactic():
    payload = {"tactic": "polite_challenge", "speaker": "Dr Rao", "reason": "Push harder."}
    team = AgentTeam(llm=GroqAgents(client=fake_client(json.dumps(payload))))
    direction, source = team.direct({"event": "Viva", "prediction": "p", "state": "HIGH", "turn": 1,
                                     "last_question": None, "user_answer": "a", "behaviour": None})
    assert source == "Scripted" and direction.tactic in ALLOWED_TACTICS[State.HIGH]


def test_director_can_choose_within_limits():
    payload = {"tactic": "hold_silence", "speaker": "Ms Kim", "reason": "The fear involves silence."}
    team = AgentTeam(llm=GroqAgents(client=fake_client(json.dumps(payload))))
    direction, source = team.direct({"event": "Viva", "prediction": "p", "state": "HIGH", "turn": 1,
                                     "last_question": None, "user_answer": "a", "behaviour": None})
    assert source == "Groq" and direction.speaker == "Ms Kim"


# Fallback and LLM boundaries
SCENE_FACTS = {"event": "Viva", "difficulty": 2, "tactic": "follow_up", "speaker": "Dr Rao", "turn": 1,
               "last_question": None, "user_answer": None}


def test_groq_failure_falls_back_to_scripted():
    class Down:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise groq.APIConnectionError(request=httpx.Request("POST", "https://example.test"))

    line, source = AgentTeam(llm=GroqAgents(client=Down())).scene(SCENE_FACTS)
    assert source == "Scripted" and line


def fake_client(content):
    class Fake:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    message = type("M", (), {"content": content})
                    choice = type("C", (), {"message": message})
                    return type("R", (), {"choices": [choice]})
    return Fake()


def test_unsafe_panel_line_is_replaced():
    team = AgentTeam(llm=GroqAgents(client=fake_client(json.dumps({"line": "You seem nervous. Why?"}))))
    line, source = team.scene(SCENE_FACTS)
    assert source == "Scripted" and "nervous" not in line


def test_malformed_llm_output_falls_back():
    team = AgentTeam(llm=GroqAgents(client=fake_client("not json")))
    plan, source = team.plan({"event": "Viva", "days_until": 6, "prediction": "I'll freeze"})
    assert source == "Scripted" and len(plan.steps) == 3


def test_debrief_with_invented_numbers_is_rejected():
    payload = {"summary": "Your belief fell by 40 percent.", "mission": "Ask one question."}
    team = AgentTeam(llm=GroqAgents(client=fake_client(json.dumps(payload))))
    facts = dict(event="Viva", prediction="p", belief_before=80, belief_after=40, belief_direction="lower", ended_early=False)
    debrief, source = team.debrief(facts)
    assert source == "Scripted" and "40" not in debrief.summary


def test_llm_plan_days_and_levels_are_clamped():
    payload = {"testable_version": "t", "target_pause_s": 99, "steps": [
        {"day": 99, "title": "a", "difficulty": 9, "test": "x"},
        {"day": 1, "title": "b", "difficulty": 1, "test": "y"},
        {"day": 2, "title": "c", "difficulty": 2, "test": "z"},
    ]}
    team = AgentTeam(llm=GroqAgents(client=fake_client(json.dumps(payload))))
    plan, source = team.plan({"event": "Viva", "days_until": 6, "prediction": "p"})
    assert source == "Groq"
    assert [s.difficulty for s in plan.steps] == [1, 2, 5]
    assert all(1 <= s.day <= 6 for s in plan.steps) and plan.target_pause_s == 10


@pytest.mark.parametrize("test_text", [
    "Keep any pause under 2 seconds.",
    "Answer with a maximum 4 second pause.",
    "Stay at no more than 3 seconds of silence.",
])
def test_plan_that_teaches_avoidance_is_rejected(test_text):
    payload = {"testable_version": "t", "target_pause_s": 4, "steps": [
        {"day": 1, "title": "a", "difficulty": 1, "test": test_text},
        {"day": 2, "title": "b", "difficulty": 2, "test": "Pause for 5 seconds on purpose."},
        {"day": 3, "title": "c", "difficulty": 3, "test": "Pause for 8 seconds on purpose."},
    ]}
    team = AgentTeam(llm=GroqAgents(client=fake_client(json.dumps(payload))))
    _, source = team.plan({"event": "Viva", "days_until": 6, "prediction": "p"})
    assert source == "Scripted"


# Speech measurements
def test_speech_metrics_measure_without_judging():
    words = [{"start": 0.0, "end": 0.5}, {"start": 0.6, "end": 1.0}, {"start": 4.0, "end": 4.5}]
    metrics = speech_metrics("Sorry, um, I think so", words)
    assert metrics["apologies"] == 1 and metrics["fillers"] == 1 and metrics["longest_gap_s"] == 3.0
    assert metrics["wpm"] == 40
    assert speech_metrics("Typed answer")["wpm"] is None


# Replay
def test_csv_round_trip_keeps_missing_sensors(tmp_path):
    path = tmp_path / "replay.csv"
    samples = generate()
    write_csv(samples, path)
    assert load_csv(path) == samples
    assert any(s.ecg_hr is None for s in samples)


def test_csv_with_missing_columns_is_rejected(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("t_s,ecg_hr\n0,70\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_csv(path)


def test_stream_state_resumes_exactly():
    a = SyntheticStream(3)
    a.calibration()
    a.next_turn()
    b = SyntheticStream(**{k: v for k, v in a.to_state().items() if k != "kind"})
    assert a.next_turn() == b.next_turn()


def test_scripted_plan_stays_within_available_days():
    plan = ScriptedAgents().plan({"event": "Viva", "days_until": 1, "prediction": "p"})
    assert all(s.day == 1 for s in plan.steps)


# API
def client(monkeypatch):
    import server
    monkeypatch.setattr(server, "team", scripted_team())
    server._hits.clear()
    return TestClient(server.app), server


def test_api_flow_and_consent(monkeypatch):
    http, _ = client(monkeypatch)
    assert http.get("/api/status").status_code == 200
    assert http.post("/api/session", json={**SETUP, "consent": False}).status_code == 422
    view = http.post("/api/session", json=SETUP).json()
    assert view["status"] == "planned"
    view = http.post("/api/calibrate", json={"token": view["token"]}).json()
    assert view["status"] == "practice" and view["question"]
    view = http.post("/api/answer", json={"token": view["token"], "text": "An answer.", "source": "sample"}).json()
    assert len(view["readings"]) == TURN_S and view["pipeline"]
    assert "GROQ_API_KEY" not in json.dumps(view) and "gsk_" not in json.dumps(view)
    view = http.post("/api/stop", json={"token": view["token"]}).json()
    assert view["status"] == "debrief"
    assert http.post("/api/finish", json={"token": view["token"], "belief_after": 70}).json()["status"] == "complete"
    assert http.post("/api/stop", json={"token": "garbage"}).status_code == 409


def test_stop_is_never_rate_limited(monkeypatch):
    http, server = client(monkeypatch)
    view = http.post("/api/session", json=SETUP).json()
    server._hits["testclient"].extend([1e12] * server._RATE_PER_MINUTE)
    assert http.post("/api/session", json=SETUP).status_code == 429
    assert http.post("/api/stop", json={"token": view["token"]}).status_code == 200


def test_speak_refuses_text_the_output_check_rejects(monkeypatch):
    http, _ = client(monkeypatch)
    assert http.post("/api/speak", json={"text": "You seem anxious.", "member": "Dr Rao"}).status_code == 422
