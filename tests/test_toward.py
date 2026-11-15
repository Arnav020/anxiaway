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
    monkeypatch.setattr(server, "REQUIRE_CHECK", False)
    server._llm_hits.clear()
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
    script = [("finish_review", {"step": "test", "pattern": "Too little yet.", "prediction_id": "p1", "next_action": "Try a small step.", "why": "Only one open."}),
              ("read_record", {"sections": ["get_record_stats", "list_open_predictions", "not_a_section"]}),
              ("finish_review", {"step": "test", "pattern": "Nine of 10.", "prediction_id": "p1", "next_action": "Try a small step.", "why": "Only one open."}),
              ("schedule_follow_up", {"prediction_id": "p1", "days": 2}),
              ("finish_review", {"step": "test", "pattern": "Too little yet.", "prediction_id": "p1", "next_action": "Try a small step.", "why": "Only one open."})]
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
    assert result["source"] == "Groq" and result["kind"] == "test" and len(calls) == 5
    assert [t["tool"] for t in result["trace"]] == ["safety_scan", "finish_review", "get_record_stats", "list_open_predictions", "finish_review", "schedule_follow_up", "finish_review"]
    assert result["trace"][1]["rejected"] and "Read the record" in result["trace"][1]["summary"], "the agent must read before it decides"
    assert all(t["by"] == "Groq" for t in result["trace"][1:]), "the agent chose every read itself"
    assert result["trace"][4]["rejected"], "digits are rejected by fixed code, and the model gets to try again"
    assert [tool["function"]["name"] for tool in calls[0]["tools"]] == ["read_record", "finish_review"] and "must_read_first" in calls[0]["messages"][1]["content"]
    assert calls[0]["model"] != calls[2]["model"], "a small model picks what to read, and the stronger one decides"
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
                    args = {"step": "add", "pattern": "in your record so far, one thing", "prediction_id": None, "next_action": "try it again", "why": "it fits"}
                    name = "get_record_stats" if len(seen) == 1 else "finish_review"
                    fn = type("F", (), {"name": name, "arguments": json.dumps({} if len(seen) == 1 else args)})
                    message = type("M", (), {"content": "", "tool_calls": [type("TC", (), {"id": "c1", "function": fn})]})
                    return type("R", (), {"choices": [type("C", (), {"message": message})]})

    result = review(_record([_pred(1, outcome="no", belief_after=30)]), 100, Fake(), "p1")
    assert "just_checked" in seen[0] and result["kind"] == "add"
    assert result["next_action"] == "Try it again."
    scripted = review(_record([_pred(1, outcome="no")]), 100, None, "p1")
    assert "next time" in scripted["next_action"]


# The anxiety check, the device link and the screening gate
NOW = 1_800_000_000_000
DAY = 86_400_000


def _checks(*scores, impact=None, step_days=14):
    from toward.checks import CheckEntry
    return [CheckEntry(at=NOW - (len(scores) - i) * step_days * DAY, score=s, impact=impact) for i, s in enumerate(scores)]


def test_check_keeps_the_three_sources_apart_and_never_merges_them():
    from toward import checks
    result = checks.evaluate([1, 1, 1, 1, 0, 1, 1], 1, False, False, False, {"state": "HIGH"}, [], NOW)
    assert result["status"] == "ok" and result["score"] == 6 and result["band"] == "Mild"
    assert result["agreement"]["kind"] == "differ" and "caffeine" in result["agreement"]["text"]
    assert "combined" not in result and "overall" not in result
    assert checks.agreement("Moderate", "LOW")["kind"] == "differ"
    assert checks.agreement("Mild", "ENGAGED")["kind"] == "agree"
    assert checks.agreement("Mild", None)["kind"] == "none"
    assert checks.agreement("Mild", "UNKNOWN")["kind"] == "untrusted"
    assert checks.agreement("Mild", "CALIBRATING")["kind"] == "baseline"


def test_check_routes_crisis_severe_and_heart_answers_in_fixed_code():
    from toward import checks
    crisis = checks.evaluate([0] * 7, None, True, False, False, None, [], NOW)
    assert crisis == {"status": "crisis", "exit_message": governor.CRISIS_RESOURCES}
    severe = checks.evaluate([3, 3, 3, 2, 2, 2, 2], 3, False, False, False, None, [], NOW)
    assert severe["status"] == "referred" and severe["check_token"] is None and severe["band"] == "Severe"
    heart = checks.evaluate([1, 0, 0, 0, 0, 0, 0], 0, False, True, False, None, [], NOW)
    assert heart["status"] == "referred" and heart["check_token"] is None and heart["exit_message"] == checks.HEART_NOTE


def test_daily_life_impact_and_a_rising_trend_are_flagged_early():
    from toward import checks
    low_but_hard = checks.evaluate([1, 1, 0, 0, 0, 0, 0], 2, False, False, False, None, [], NOW)
    assert low_but_hard["band"] == "Minimal" and checks.IMPACT_NOTE in low_but_hard["notes"]
    rising = checks.evaluate([2, 2, 2, 2, 1, 1, 1], 1, False, False, False, None, _checks(4, 7), NOW)
    assert rising["trend"]["rising"] and checks.RISING_NOTE in rising["notes"] and rising["trend"]["next"] == "person"
    steady = checks.trend(_checks(8, 7, 8, step_days=3), NOW)
    assert steady["direction"] == "about the same" and not steady["rising"] and steady["next"] is None
    assert checks.trend(_checks(6, step_days=20), NOW)["next"] == "screen"
    assert checks.trend([], NOW)["next"] == "screen"
    assert checks.parse_checks([{"at": 5, "score": 99}, {"at": 9, "score": 4}])[0].score == 4


def test_check_token_is_signed_recent_and_starts_a_session(monkeypatch):
    from toward import checks
    http, _ = client(monkeypatch)
    body = {"gad_items": [1, 1, 2, 1, 0, 1, 1], "impact": 1, "consent": True}
    assert http.post("/api/check", json={**body, "consent": False}).status_code == 422
    assert http.post("/api/check", json={**body, "gad_items": [9] * 7}).status_code == 422
    result = http.post("/api/check", json={**body, "body": {"state": "LOW"}}).json()
    token = result["check_token"]
    assert result["status"] == "ok" and checks.open_token(token) == {"gad_items": body["gad_items"], "impact": 1, "body_state": "LOW", "body_demo": True}
    with pytest.raises(BadToken):
        checks.open_token(token, result["at"] + 25 * 3_600_000)
    with pytest.raises(BadToken):
        checks.open_token(seal({"kind": "other", "at": NOW, "gad_items": [0] * 7}), NOW)
    setup = {"event": "Going somewhere crowded", "days_until": 5, "prediction": "I will have to leave", "belief_before": 70}
    started_ok = http.post("/api/session", json={**setup, "check_token": token}).json()
    assert started_ok["status"] == "planned" and started_ok["gad"]["score"] == 7
    assert http.post("/api/session", json={**setup, "check_token": "forged"}).status_code == 409
    assert http.post("/api/session", json=setup).status_code == 422


def test_demo_reading_is_classified_by_the_same_signal_layer():
    from toward import device
    base_hr, base_eda = device.demo_baseline()
    expected = {"calm": "LOW", "rising": "ENGAGED", "high": "HIGH", "movement": "UNKNOWN", "sensor_loss": "ENGAGED"}
    for scenario, state in expected.items():
        samples, cursor = device.demo_samples(scenario)
        summary = device.summarise(samples, base_hr, base_eda)
        assert summary["state"] == state and cursor["t"] == len(samples)
        assert summary["verdict"]["source"] == "Simulated" and summary["source"] == device.DEMO_LABEL
    lost = device.summarise(device.demo_samples("sensor_loss")[0], base_hr, base_eda)
    assert lost["heart_source"] == "PPG" and "ECG" not in lost["present"]
    eeg = next(m for m in lost["mask"] if m["sensor"] == "EEG")
    assert eeg == {"sensor": "EEG", "text": device.SENSOR_TEXT["EEG"], "on_device": False, "present": False}
    moving = device.summarise(device.demo_samples("movement")[0], base_hr, base_eda)
    assert moving["verdict"]["label"] == "unavailable" and moving["confidence"] == 0


def test_first_device_reading_sets_a_baseline_and_claims_nothing():
    from toward import device
    first = device.summarise(flat(72, 45), None, None)
    assert first["state"] == "CALIBRATING" and first["baseline"]["set_now"] and first["baseline"]["hr"] == 72
    assert first["verdict"] is None and first["hr_delta"] is None
    later = device.summarise(flat(100, 45), 72.0, 4.0, {"label": "high", "probability": 0.81}, device.DEVICE_LABEL)
    assert later["state"] == "HIGH" and later["hr_delta"] == 28.0
    assert later["verdict"] == {"label": "high", "probability": 0.81, "source": "Model"}


def test_device_api_reply_is_normalised_and_failures_give_no_reading(monkeypatch):
    from toward import device
    monkeypatch.delenv("TOWARD_DEVICE_API_URL", raising=False)
    preferred = {"samples": [{"t": 0, "ecg_hr": 80, "ppg_hr": 79, "eda": 4.1, "motion": 0.02, "quality": 0.9}],
                 "verdict": {"label": "moderate", "probability": 0.6}}
    got = device.normalise(preferred)
    assert got["samples"][0].ecg_hr == 80 and got["verdict"] == {"label": "moderate", "probability": 0.6}
    loose = device.normalise({"readings": [{"heart_rate": 91, "gsr": 5.2, "imu": 0.1}], "prediction": "anxious", "confidence": 0.9})
    assert loose["samples"][0].ecg_hr == 91 and loose["samples"][0].quality == 1.0 and loose["verdict"]["label"] == "high"
    assert device.normalise({"samples": []}) is None and device.normalise("nope") is None
    assert device.normalise({"samples": [{"hr": 80}], "verdict": {"label": "purple"}})["verdict"] is None

    assert not device.configured() and device.fetch_latest() is None and device.status()["label"] == "Integrating soon"
    monkeypatch.setenv("TOWARD_DEVICE_API_URL", "https://device.test/latest")
    monkeypatch.setenv("TOWARD_DEVICE_API_KEY", "k")
    seen = {}

    def ok(url, headers, timeout):
        seen.update(url=url, headers=headers)
        return httpx.Response(200, json=preferred, request=httpx.Request("GET", url))

    assert device.fetch_latest(ok)["samples"][0].ppg_hr == 79 and seen["headers"] == {"Authorization": "Bearer k"}
    assert device.status()["configured"] and all(s["present"] == s["on_device"] for s in device.status()["sensors"])

    def down(url, headers, timeout):
        raise httpx.ConnectError("down")

    assert device.fetch_latest(down) is None
    assert device.fetch_latest(lambda url, headers, timeout: httpx.Response(500, request=httpx.Request("GET", url))) is None


def test_reading_endpoint_serves_the_demo_and_refuses_a_missing_device(monkeypatch):
    http, _ = client(monkeypatch)
    monkeypatch.delenv("TOWARD_DEVICE_API_URL", raising=False)
    demo = http.post("/api/reading", json={"mode": "demo", "scenario": "high"}).json()
    assert demo["summary"]["state"] == "HIGH" and len(demo["samples"]) == 45 and demo["cursor"]["t"] == 45
    again = http.post("/api/reading", json={"mode": "demo", "scenario": "calm", **demo["cursor"]}).json()
    assert again["samples"][0]["t"] == 45
    assert http.post("/api/reading", json={"mode": "device"}).status_code == 409
    status = http.get("/api/device/status").json()
    assert status["configured"] is False and status["window_s"] == 10 and status["stride_s"] == 5


def test_screening_gates_the_agent_before_any_prediction_test():
    from toward.record import review
    record = _record([_pred(1), _pred(2, outcome="no", belief_after=30)])
    plain = review(record, NOW, None)
    assert plain["kind"] in ("check", "test") and plain["screening"] is None
    no_check = review(record, NOW, None, checks=[])
    assert no_check["kind"] == "screen" and no_check["prediction_id"] is None
    assert no_check["trace"][1]["tool"] == "get_screening_trend"
    stale = review(record, NOW, None, checks=_checks(6, step_days=20))
    assert stale["kind"] == "screen"
    severe = review(record, NOW, None, checks=_checks(17, step_days=1))
    assert severe["kind"] == "person" and "counsellor" in severe["next_action"]
    recent = review(record, NOW, None, checks=_checks(6, step_days=1))
    assert recent["kind"] == plain["kind"] and recent["screening"]["latest_band"] == "Mild"
    only_checks = review([], NOW, None, checks=_checks(6, step_days=1))
    assert only_checks["kind"] == "add"


def test_a_model_cannot_talk_its_way_past_the_screening_gate():
    from toward.record import Toolbox
    box = Toolbox(_record([_pred(1)]), NOW, _checks(18, step_days=1))
    accepted = box.call("finish_review", {"pattern": "in your record so far, one open item", "prediction_id": "p1",
                                          "next_action": "test this one today", "why": "it is open"})
    assert accepted == {"accepted": True, "kind": "person"} and box.result["prediction_id"] is None


def test_fixed_code_starts_practice_gently_and_says_when_a_reading_was_a_demo(monkeypatch):
    http, _ = client(monkeypatch)
    setup = {"event": "Going somewhere crowded", "days_until": 5, "prediction": "I will have to leave", "belief_before": 70}

    def plan_for(check):
        token = http.post("/api/check", json={"consent": True, **check}).json()["check_token"]
        return http.post("/api/session", json={**setup, "check_token": token}).json()

    plain = plan_for({"gad_items": [1, 1, 1, 1, 0, 1, 1], "impact": 1})
    assert plain["basis"] == {"impact": "Somewhat difficult", "body": None, "body_demo": False, "gentle": []}
    hard = plan_for({"gad_items": [1, 1, 1, 1, 0, 1, 1], "impact": 3})
    assert hard["basis"]["gentle"] == ["anxiety is making daily life very difficult"] and hard["level"] <= 2
    demo = plan_for({"gad_items": [1, 1, 1, 1, 0, 1, 1], "impact": 0, "body": {"state": "HIGH"}})
    assert demo["basis"]["body_demo"] and "demo body reading" in demo["basis"]["gentle"][0]
    real = plan_for({"gad_items": [2, 2, 2, 2, 1, 1, 1], "impact": 1, "body": {"state": "LOW", "source": "device"}})
    assert not real["basis"]["body_demo"] and real["basis"]["gentle"] == ["your screening is in the moderate range"]
    assert any("Gentle start" in entry["text"] for entry in real["trail"])


# Audit fixes
def test_reworded_and_disguised_crisis_language_is_caught():
    from toward.record import review, safety_scan, sharpen
    caught = ["I keep thinking about ending it all", "I will take my own life if I fail", "k1ll myself after the viva",
              "everyone would be better off without me", "I don't want to be alive anymore", "I wish I were dead",
              "main marna chahta hoon", "thinking of an overdose", "I might hang myself"]
    for text in caught:
        assert governor.check_user_text(text).action is Action.CRISIS, text
        assert safety_scan([text])["status"] == "crisis", text
        assert sharpen(text, None)["status"] == "crisis", text
        assert not governor.output_is_safe(text), text
    fine = ["I'll die of embarrassment if I forget my lines", "I want to end my presentation early", "My battery will die mid call",
            "I'm dead tired before the exam", "They will kill my idea in the meeting"]
    for text in fine:
        assert governor.check_user_text(text).action is Action.ALLOW, text

    class Never:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise AssertionError("A model saw crisis text")

    assert review(_record([_pred(1, text="I keep thinking about ending it all after results")]), 100, Never())["status"] == "crisis"


def test_a_model_that_reads_a_worry_as_self_harm_triggers_fixed_crisis_words():
    from toward.record import Sharpened, sharpen

    class Llm:
        def _call(self, model, system, payload, model_type):
            return Sharpened(prediction="I will end my life.", how_to_check="I notice I have taken a lethal dose.", theme="other", suitable=True)

    result = sharpen("I can't see a way through this term", Llm())
    assert result == {"status": "crisis", "message": governor.CRISIS_RESOURCES}


def test_the_agent_chooses_among_allowed_steps_and_fixed_code_holds_the_line():
    from toward.record import Toolbox, tools_for
    ok = {"pattern": "in your record so far, one open item", "next_action": "try one small thing", "why": "it fits"}
    free = Toolbox(_record([_pred(1)]), NOW, _checks(6, step_days=9))
    assert free.allowed_steps() == ["test", "add", "screen"]
    assert free.call("finish_review", {**ok, "step": "person", "prediction_id": None})["error"].startswith("step must be one of")
    assert free.call("finish_review", {**ok, "step": "screen", "prediction_id": "p1"}) == {"accepted": True, "kind": "screen"}
    assert free.result["prediction_id"] is None
    assert Toolbox(_record([_pred(1, due_at=50)]), NOW, _checks(6, step_days=1)).allowed_steps() == ["check"]
    assert Toolbox(_record([_pred(1)]), NOW, _checks(18, step_days=1)).allowed_steps() == ["person"]
    assert Toolbox(_record([]), NOW, None).allowed_steps() == ["add"]
    read = tools_for(free)[0]["function"]
    assert read["name"] == "read_record" and read["parameters"]["properties"]["sections"]["items"]["enum"][0] == "get_screening_trend"
    assert "get_screening_trend" not in tools_for(Toolbox([], NOW, None))[0]["function"]["parameters"]["properties"]["sections"]["items"]["enum"]
    assert "error" in free.call("read_record", {"sections": ["nonsense"]})
    strict = Toolbox(_record([_pred(1)]), NOW, _checks(6, step_days=1))
    strict.must_read = True
    assert "Read the record" in strict.call("finish_review", {**ok, "step": "test", "prediction_id": "p1"})["error"]
    got = strict.call("read_record", {"sections": ["get_record_stats", "get_screening_trend", "list_open_predictions"]})
    assert set(got) == {"get_record_stats", "get_screening_trend", "list_open_predictions"} and len(strict.trace) == 4
    assert strict.call("finish_review", {**ok, "step": "test", "prediction_id": "p1", "follow_up_days": 3}) == {"accepted": True, "kind": "test"}
    assert strict.follow_ups["p1"] == NOW + 3 * DAY and strict.trace[-2]["tool"] == "schedule_follow_up"


def test_an_agent_that_never_reads_falls_back_to_the_scripted_policy():
    from toward.record import review

    class Stubborn:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    args = {"step": "test", "pattern": "in your record so far, one item", "prediction_id": "p1", "next_action": "try it", "why": "open"}
                    fn = type("F", (), {"name": "finish_review", "arguments": json.dumps(args)})
                    message = type("M", (), {"content": "", "tool_calls": [type("TC", (), {"id": "c", "function": fn})]})
                    return type("R", (), {"choices": [type("C", (), {"message": message})]})

    result = review(_record([_pred(1)]), 100, Stubborn())
    assert result["source"] == "Scripted" and result["kind"] == "test" and result["prediction_id"] == "p1"
    assert sum(t["rejected"] for t in result["trace"]) == 5


def test_sessions_need_a_check_and_verdicts_need_the_device(monkeypatch):
    http, server = client(monkeypatch)
    monkeypatch.setattr(server, "REQUIRE_CHECK", True)
    monkeypatch.delenv("TOWARD_DEVICE_API_URL", raising=False)
    assert http.post("/api/session", json=SETUP).status_code == 409
    assert http.get("/openapi.json").status_code == 404
    token = http.post("/api/check", json={"gad_items": [1, 1, 1, 1, 0, 1, 1], "impact": 1, "consent": True}).json()["check_token"]
    view = http.post("/api/session", json={"event": "Crowd", "days_until": 5, "prediction": "I will have to leave", "belief_before": 70, "check_token": token}).json()
    view = http.post("/api/calibrate", json={"token": view["token"]}).json()
    view = http.post("/api/answer", json={"token": view["token"], "text": "I would stay", "model_verdict": {"label": "low", "probability": 0.01}}).json()
    assert view["verdict"]["source"] == "Simulated", "nobody can label their own verdict as the model's"


def test_llm_budget_falls_back_to_scripted_and_the_caller_header_cannot_dodge_limits(monkeypatch):
    http, server = client(monkeypatch)
    monkeypatch.setattr(server, "_LLM_PER_MINUTE", 2)
    assert server._llm_ok() and server._llm_ok() and not server._llm_ok()
    assert http.post("/api/record/sharpen", json={"worry": "They will all laugh at me"}).json()["source"] == "Scripted"
    request = type("R", (), {"headers": {"x-forwarded-for": "9.9.9.9", "x-real-ip": "1.2.3.4"}, "client": None})
    assert server._client_of(request) == "1.2.3.4"


def test_a_simulated_reading_is_never_described_as_the_persons_body():
    session = started()
    session.calibrate()
    session.answer("I would stay where I am", "typed", 2.0)
    session.stop()
    session.finish(60, {"outcome": "no", "peak_distress": 4})
    assert "simulated demo signal" in session.report["insight"] and session.report["verdict"]["source"] == "Simulated"


def test_plans_and_guide_lines_that_soothe_are_rejected():
    from toward.agents import LadderPlan, PlanStep, _OFF_SCENE, teaches_avoidance
    calm = LadderPlan(testable_version="x", target_pause_s=3, steps=[
        PlanStep(day=1, title="Breathe", difficulty=1, test="Do a two minute slow breathing exercise."),
        PlanStep(day=2, title="Go", difficulty=2, test="Stand in the queue."), PlanStep(day=3, title="Stay", difficulty=3, test="Stay in the crowd.")])
    assert teaches_avoidance(calm)
    assert _OFF_SCENE.search("Notice the sounds around you, like a gentle tide, how does that feel?")
    assert not _OFF_SCENE.search("The queue is long today. What do you do next?")
