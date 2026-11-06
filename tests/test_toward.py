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

EXPECTED_STATES = ["LOW", "ENGAGED", "HIGH", "UNKNOWN", "LIMIT", "ENGAGED"]
SETUP = dict(event="Viva", days_until=6, prediction="If I pause, they'll think I'm incompetent",
             belief_before=80, gad_items=[1, 1, 2, 1, 0, 1, 1], self_harm=False, heart_condition=False, crisis=False, consent=True)


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
    assert levels[2] == levels[3], "HIGH must hold the level"
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
    session = Session({**SETUP, "gad_items": [3, 3, 3, 3, 2, 2, 2]}, team)
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
    for banned in ("hr", "hr_delta", "bpm", "eda", "motion", "confidence", "gad_items", "gad7"):
        assert banned not in keys
    blob = json.dumps(everything)
    assert "78." not in blob, "heart values must never appear"
    assert not any("state" in facts for facts in seen["scene"]), "the Scene Agent never sees the state"
    assert not any("state" in facts for facts in seen["plan"] + seen["debrief"])


def test_director_cannot_pick_a_forbidden_tactic():
    payload = {"tactic": "polite_challenge", "speaker": "Asha Rao", "reason": "Push harder."}
    team = AgentTeam(llm=GroqAgents(client=fake_client(json.dumps(payload))))
    direction, source = team.direct({"event": "Viva", "prediction": "p", "state": "HIGH", "turn": 1,
                                     "last_question": None, "user_answer": "a", "behaviour": None})
    assert source == "Scripted" and direction.tactic in ALLOWED_TACTICS[State.HIGH]


def test_director_can_choose_within_limits():
    payload = {"tactic": "hold_silence", "speaker": "Mina Kim", "reason": "The fear involves silence."}
    team = AgentTeam(llm=GroqAgents(client=fake_client(json.dumps(payload))))
    direction, source = team.direct({"event": "Viva", "prediction": "p", "state": "HIGH", "turn": 1,
                                     "last_question": None, "user_answer": "a", "behaviour": None})
    assert source == "Groq" and direction.speaker == "Mina Kim"


# Fallback and LLM boundaries
SCENE_FACTS = {"event": "Viva", "difficulty": 2, "tactic": "follow_up", "speaker": "Asha Rao", "turn": 1,
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
    payload = {"summary": "Your belief fell by 40 percent.", "insight": "i", "mission": "Ask one question.", "next_practice": "Try again."}
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
    stream = SyntheticStream()
    samples = stream.calibration() + stream.next_turn("sensor_loss")
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


def test_unknown_page_is_a_404_even_without_public_files(monkeypatch, tmp_path):
    http, server = client(monkeypatch)
    assert http.get("/does-not-exist").status_code == 404
    monkeypatch.setattr(server, "PUBLIC", tmp_path)
    response = http.get("/does-not-exist")
    assert response.status_code == 404 and "can't find" in response.text


def test_a_line_using_a_guide_name_is_retried_then_scripted():
    from toward.agents import GroqAgents

    llm = GroqAgents(client=object())
    lines = iter(["Hey Asha, what is your idea?", "What is your idea?"])
    llm._call = lambda *a: type("R", (), {"line": next(lines)})()
    facts = dict.fromkeys(("event", "difficulty", "tactic", "speaker", "last_question", "user_answer"), "x")
    assert llm.scene(facts) == "What is your idea?"
    llm._call = lambda *a: type("R", (), {"line": "Tom, go on?"})()
    assert llm.scene(facts) is None


def test_screen_routes_support_before_consent_or_any_llm(monkeypatch):
    http, server = client(monkeypatch)
    calls = []
    server.team.plan = lambda facts: calls.append(facts)
    severe = http.post("/api/screen", json={"gad_items": [3, 3, 3, 3, 2, 2, 2]}).json()
    crisis = http.post("/api/screen", json={"gad_items": [0] * 7, "crisis": True}).json()
    fine = http.post("/api/screen", json={"gad_items": [1] * 7}).json()
    assert severe["status"] == "referred" and crisis["status"] == "crisis" and "14416" in crisis["exit_message"]
    assert fine["status"] == "allow" and not calls


def test_voice_endpoints_only_work_inside_a_session(monkeypatch):
    http, server = client(monkeypatch)
    spoken = []
    monkeypatch.setattr(server.voice, "speak", lambda text, member: spoken.append(text) or b"wav")
    assert http.post("/api/speak", json={"token": "x", "text": "Any free text", "member": "Tom Ellis"}).status_code == 409
    assert http.post("/api/transcribe", content=b"audio", headers={"Content-Type": "audio/webm"}).status_code == 409
    view = http.post("/api/session", json=SETUP).json()
    view = http.post("/api/calibrate", json={"token": view["token"]}).json()
    line = view["question"]["line"]
    assert http.post("/api/speak", json={"token": view["token"], "text": "Something else", "member": "Tom Ellis"}).status_code == 422
    assert http.post("/api/speak", json={"token": view["token"], "text": line, "member": "Tom Ellis"}).status_code == 200
    assert spoken == [line]


def test_summary_includes_a_replanned_next_step(monkeypatch):
    http, _ = client(monkeypatch)
    view = http.post("/api/session", json=SETUP).json()
    view = http.post("/api/stop", json={"token": view["token"]}).json()
    report = http.post("/api/finish", json={"token": view["token"], "belief_after": 60}).json()["report"]
    assert report["next_practice"] and report["next_day"] >= 1


def test_gad_answers_are_validated_and_used(monkeypatch):
    http, _ = client(monkeypatch)
    assert http.post("/api/session", json={**SETUP, "gad_items": [9, 0, 0, 0, 0, 0, 0]}).status_code == 422
    view = http.post("/api/session", json=SETUP).json()
    assert view["gad"]["score"] == 7 and view["gad"]["band"] == "Mild"
    assert view["gad"]["items"][2]["answer"] == "More than half the days"
    assert "Worrying too much" in view["gad"]["concerns"][0]


def test_finish_keeps_post_session_answers(monkeypatch):
    http, _ = client(monkeypatch)
    view = http.post("/api/session", json=SETUP).json()
    view = http.post("/api/stop", json={"token": view["token"]}).json()
    post = {"peak_distress": 6, "symptoms": ["Racing heart"], "outcome": "partly", "note": "Calmer than I expected."}
    view = http.post("/api/finish", json={"token": view["token"], "belief_after": 60, "post": post}).json()
    assert view["report"]["post"]["symptoms"] == ["Racing heart"] and view["report"]["gad"]["band"] == "Mild"
    assert view["report"]["pathway"]["name"] == "Guided practice" and view["report"]["insight"]


# Care logic
@pytest.mark.parametrize("score,band", [(0, "Minimal"), (4, "Minimal"), (5, "Mild"), (9, "Mild"), (10, "Moderate"), (14, "Moderate"), (15, "Severe")])
def test_gad_bands(score, band):
    from toward.care import gad_band
    assert gad_band(score) == band


def test_verdict_is_labelled_simulated_until_the_app_connects():
    session = started()
    session.calibrate()
    session.answer("An answer.", "sample")
    assert session.verdicts[-1]["source"] == "Simulated"
    session.answer("An answer.", "sample", external_verdict={"label": "moderate", "probability": 0.6})
    assert session.verdicts[-1]["source"] == "Model" and session.verdicts[-1]["label"] == "moderate"


def test_a_high_model_verdict_blocks_any_step_up():
    session = started()
    session.calibrate()
    level = session.level
    session.answer("An answer.", "sample", inject="calm", external_verdict={"label": "high", "probability": 0.8})
    assert session.turn_log[-1]["state"] == "LOW" and session.level == level


def test_crisis_text_in_the_post_session_note_gets_fixed_support():
    session = started()
    session.calibrate()
    session.stop()
    session.finish(70, {"note": "I want to end my life"})
    assert session.status == "crisis" and session.report is None


def test_high_reported_distress_stops_the_plan_advancing():
    session = started()
    run_all(session)
    session.finish(40, {"peak_distress": 9})
    assert session.report["decision"] == "repeat"


# ---------- The prediction track record ----------
def _pred(i, **kw):
    base = dict(id=f"p{i}", text=f"If I do thing {i}, it will go badly", theme="being judged", belief_before=80,
                created_at=i, due_at=0, outcome=None)
    return {**base, **kw}


def _record(raw):
    from toward import record
    return record.parse_predictions(raw)


def test_stats_come_from_fixed_code_and_need_enough_data():
    from toward.record import compute_stats
    few = compute_stats(_record([_pred(1, outcome="no"), _pred(2, outcome="yes", coped=1)]), 100)
    assert few["state"] == "too_few" and few["checked"] == 2 and few["actual_pct"] == 50 and few["got_through"] == 1
    many = compute_stats(_record([_pred(i, outcome="no") for i in range(4)] + [_pred(9, outcome="partly")]), 100)
    assert many["state"] == "overestimates" and many["forecast_pct"] == 80 and many["actual_pct"] == 10
    close = compute_stats(_record([_pred(i, outcome="yes") for i in range(5)]), 100)
    assert close["state"] == "close"
    assert compute_stats([], 100)["forecast_pct"] is None


def test_support_is_flagged_when_coping_is_hard():
    from toward.record import compute_stats
    stats = compute_stats(_record([_pred(1, outcome="yes", coped=0), _pred(2, outcome="partly", coped=0)]), 100)
    assert stats["needs_support"]


def test_a_damaged_entry_is_dropped_and_breaks_nothing():
    good = _pred(1)
    assert [p.id for p in _record([good, {"id": "BAD!"}, {"nonsense": True}])] == ["p1"]


def test_agent_tools_enforce_the_rules():
    from toward.record import Toolbox
    box = Toolbox(_record([_pred(1, due_at=50), _pred(2, due_at=5000), _pred(3, outcome="yes")]), 100)
    ok = dict(pattern="Too little to tell.", next_action="Check it.", why="It is due.")
    assert "error" in box.call("schedule_follow_up", {"prediction_id": "p1", "days": 2})
    assert box.call("schedule_follow_up", {"prediction_id": "p2", "days": 99})["days"] == 14
    assert "error" in box.call("schedule_follow_up", {"prediction_id": "p3", "days": 2})
    assert "error" in box.call("finish_review", {**ok, "prediction_id": "p2"}), "an overdue check must come first"
    assert "error" in box.call("finish_review", {**ok, "prediction_id": "p1", "pattern": "Nine of ten, 90 percent."})
    assert "error" in box.call("finish_review", {**ok, "prediction_id": "p1", "pattern": "Your anxiety is wrong."})
    assert "error" in box.call("finish_review", {**ok, "prediction_id": "p1", "why": "These rarely happen."}), "too few to claim"
    assert "error" in box.call("no_such_tool", {})
    assert box.call("finish_review", {**ok, "prediction_id": "p1"})["kind"] == "check"
    assert box.result["prediction_id"] == "p1"
    assert any(t["rejected"] for t in box.trace)


def test_review_falls_back_when_the_model_is_missing_or_fails():
    from toward.record import review

    class Broken:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise groq.APIConnectionError(request=httpx.Request("POST", "http://x"))

    for client in (None, Broken()):
        result = review(_record([_pred(1, due_at=50), _pred(2)]), 100, client)
        assert result["status"] == "ok" and result["source"] == "Scripted"
        assert result["prediction_id"] == "p1" and result["kind"] == "check"
        assert result["trace"][0]["tool"] == "safety_scan"


def test_review_runs_tools_from_the_model_then_decides():
    from toward.record import review
    script = [("finish_review", {"pattern": "Nine of 10.", "prediction_id": "p1", "next_action": "Try a small step.", "why": "Only one open."}),
              ("schedule_follow_up", {"prediction_id": "p1", "days": 2}),
              ("finish_review", {"pattern": "Too little yet.", "prediction_id": "p1", "next_action": "Try a small step.", "why": "Only one open."})]
    calls = []

    class Fake:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    name, args = script[len(calls) - 1]
                    fn = type("F", (), {"name": name, "arguments": json.dumps(args)})
                    call = type("TC", (), {"id": f"c{len(calls)}", "function": fn})
                    message = type("M", (), {"content": "", "tool_calls": [call]})
                    return type("R", (), {"choices": [type("C", (), {"message": message})]})

    result = review(_record([_pred(1)]), 100, Fake())
    assert result["source"] == "Groq" and result["kind"] == "test" and len(calls) == 3
    assert [t["tool"] for t in result["trace"]] == ["safety_scan", "get_record_stats", "list_checked_predictions", "list_open_predictions", "compare_themes", "finish_review", "schedule_follow_up", "finish_review"]
    assert result["trace"][5]["rejected"], "digits are rejected by fixed code, and the model gets to try again"
    assert result["pattern"] == "Too little yet." and "p1" in result["follow_ups"]


def test_crisis_language_stops_the_record_before_any_model():
    from toward.record import review

    class Never:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise AssertionError("A model saw crisis text")

    result = review(_record([_pred(1, text="If this fails I want to end my life")]), 100, Never())
    assert result["status"] == "crisis" and "14416" in result["message"]


def test_sharpen_blocks_danger_and_keeps_words_when_scripted():
    from toward.record import sharpen
    assert sharpen("I want to end my life tomorrow", None)["status"] == "crisis"
    assert sharpen("My partner hits me when I speak", None)["status"] == "support"
    kept = sharpen("Everyone will laugh at me", None)
    assert kept["status"] == "ok" and kept["prediction"] == "Everyone will laugh at me" and kept["source"] == "Scripted"


def test_record_endpoints_and_session_history(monkeypatch):
    http, server = client(monkeypatch)
    raw = [_pred(1, outcome="no"), {"bad": "entry"}]
    assert http.post("/api/record/stats", json={"predictions": raw}).json()["checked"] == 1
    assert http.post("/api/record/review", json={"predictions": raw}).json()["status"] == "ok"
    assert http.post("/api/record/sharpen", json={"worry": "They will all laugh at me"}).json()["status"] == "ok"
    with_history = http.post("/api/session", json={**SETUP, "history": raw}).json()
    assert with_history["status"] == "planned" and "1 earlier predictions" in with_history["record_note"]
    without = http.post("/api/session", json=SETUP).json()
    assert without["status"] == "planned" and without["record_note"] is None


def test_final_wording_is_checked_and_a_just_checked_result_reaches_the_agent(monkeypatch):
    from toward.record import review
    http, _ = client(monkeypatch)
    assert http.post("/api/record/check", json={"worry": "I want to end my life"}).json()["status"] == "crisis"
    assert http.post("/api/record/check", json={"worry": "They will all laugh"}).json() == {"status": "ok"}
    seen = []

    class Fake:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    seen.append(kwargs["messages"][1]["content"])
                    args = {"pattern": "in your record so far, one thing", "prediction_id": None, "next_action": "try it again", "why": "it fits"}
                    fn = type("F", (), {"name": "finish_review", "arguments": json.dumps(args)})
                    message = type("M", (), {"content": "", "tool_calls": [type("TC", (), {"id": "c1", "function": fn})]})
                    return type("R", (), {"choices": [type("C", (), {"message": message})]})

    result = review(_record([_pred(1, outcome="no", belief_after=30)]), 100, Fake(), "p1")
    assert "just_checked" in seen[0] and result["kind"] == "add"
    assert result["next_action"] == "Try it again."
    scripted = review(_record([_pred(1, outcome="no")]), 100, None, "p1")
    assert "next time" in scripted["next_action"]
