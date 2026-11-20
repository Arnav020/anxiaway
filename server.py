"""AnxiAway API and local static server.

Stateless, so it runs locally and on serverless hosts. On Vercel, files in public/ are
served by the CDN and this app only handles /api.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from toward.agents import FAST_MODEL, PLAN_MODEL, AgentTeam, Voice, speech_metrics
from toward.governor import Action, check_entry, output_is_safe
from toward import checks, device, record, safety
from toward.replay import INJECTABLE
from toward.session import Session
from toward.state import BadToken, seal, unseal

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
MAX_AUDIO_BYTES = 4 * 1024 * 1024
NOT_FOUND_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Page not found | AnxiAway</title>
<meta name="robots" content="noindex"><link rel="icon" href="/img/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/css/base.css"><link rel="stylesheet" href="/css/site.css"></head>
<body><main class="notfound"><div class="notfound-face" aria-hidden="true"><i></i><i></i><b></b></div>
<h1 class="heading-3">We can't find that page.</h1>
<p class="body-3 muted">It may have moved, or the address may be wrong. Take your time.</p>
<a class="btn btn-cta btn-lg" href="/">Back to home</a></main></body></html>"""

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
app = FastAPI(title="AnxiAway", docs_url=None, redoc_url=None, openapi_url=None)
# A guided session must rest on a recent check. Tests switch this off to exercise the session rules directly.
REQUIRE_CHECK = True
team = AgentTeam()
scripted_team = AgentTeam()
scripted_team.llm = None
safety.install(safety.SafetyAgent(team.llm.client) if team.llm else None)
voice = Voice()


@app.middleware("http")
async def always_fresh(request: Request, call_next):
    """Local pages and scripts are revalidated on every load, so an edit is never hidden by a stale copy."""
    response = await call_next(request)
    if not request.url.path.startswith(("/api/", "/img/", "/fonts/")):
        response.headers["Cache-Control"] = "no-cache"
    return response

# Best effort limit per server instance, to slow down misuse of the public demo.
_RATE_PER_MINUTE = 90
_hits: dict[str, deque] = defaultdict(deque)


# Language model calls per minute on this instance. Beyond it, scripted agents answer, so nothing breaks and the quota is protected.
_LLM_PER_MINUTE = 40
_llm_hits: deque = deque()


def _llm_ok() -> bool:
    now = time.monotonic()
    while _llm_hits and now - _llm_hits[0] > 60:
        _llm_hits.popleft()
    if len(_llm_hits) >= _LLM_PER_MINUTE:
        return False
    _llm_hits.append(now)
    return True


def _client_of(request: Request) -> str:
    # The host sets these two itself. x-forwarded-for can be written by the caller, so it comes last.
    for header in ("x-vercel-forwarded-for", "x-real-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "local"


def _limit(request: Request) -> None:
    client = _client_of(request)
    now = time.monotonic()
    hits = _hits[client]
    while hits and now - hits[0] > 60:
        hits.popleft()
    if len(hits) >= _RATE_PER_MINUTE:
        raise HTTPException(429, "Too many requests. Wait a minute and try again.")
    hits.append(now)


class SetupIn(BaseModel):
    event: str = Field(min_length=2, max_length=120)
    days_until: int = Field(ge=1, le=60)
    prediction: str = Field(min_length=5, max_length=300)
    belief_before: int = Field(ge=0, le=100)
    # Either a recent check token, or the screening answers themselves.
    check_token: str | None = Field(default=None, max_length=4000)
    gad_items: list[int] | None = Field(default=None, min_length=7, max_length=7)
    self_harm: bool = False
    heart_condition: bool = False
    crisis: bool = False
    consent: bool = False
    history: list[dict] = Field(default_factory=list, max_length=200)


class VerdictIn(BaseModel):
    """What the wearable app sends once our anxiety model is integrated."""

    label: str = Field(pattern="^(low|moderate|high|unavailable)$")
    probability: float | None = Field(default=None, ge=0, le=1)


class PostIn(BaseModel):
    peak_distress: int | None = Field(default=None, ge=0, le=10)
    symptoms: list[str] = Field(default_factory=list, max_length=12)
    outcome: str | None = Field(default=None, pattern="^(yes|partly|no)$")
    note: str = Field(default="", max_length=400)


class SpeakIn(BaseModel):
    token: str = Field(max_length=200_000)
    text: str = Field(min_length=1, max_length=400)
    member: str = Field(max_length=20)


class ScreenIn(BaseModel):
    gad_items: list[int] = Field(min_length=7, max_length=7)
    self_harm: bool = False
    heart_condition: bool = False
    crisis: bool = False
    adult: bool = True
    other_condition: bool = False


class WorryIn(BaseModel):
    worry: str = Field(min_length=5, max_length=300)


class RecordIn(BaseModel):
    predictions: list[dict] = Field(default_factory=list, max_length=200)
    just_checked: str | None = Field(default=None, max_length=24)
    checks: list[dict] | None = Field(default=None, max_length=200)


class BodyIn(BaseModel):
    state: str = Field(pattern="^(CALIBRATING|LOW|ENGAGED|HIGH|LIMIT|UNKNOWN)$")
    source: str = Field(default="demo", pattern="^(demo|device)$")


class CheckIn(BaseModel):
    gad_items: list[int] = Field(min_length=7, max_length=7)
    impact: int | None = Field(default=None, ge=0, le=3)
    self_harm: bool = False
    heart_condition: bool = False
    crisis: bool = False
    adult: bool = True
    other_condition: bool = False
    consent: bool = False
    body: BodyIn | None = None
    history: list[dict] = Field(default_factory=list, max_length=200)


class ReadingIn(BaseModel):
    mode: str = Field(default="demo", pattern="^(demo|device)$")
    scenario: str = Field(default="calm", max_length=20)
    t: int = Field(default=0, ge=0, le=10_000_000)
    previous: float = Field(default=0.0, ge=-60, le=60)
    baseline_hr: float | None = Field(default=None, ge=30, le=220)
    baseline_eda: float | None = Field(default=None, ge=0, le=100)


class TokenIn(BaseModel):
    token: str = Field(max_length=200_000)


class AnswerIn(TokenIn):
    text: str = Field(min_length=1, max_length=1500)
    source: str = Field(default="typed", pattern="^(typed|voice|sample)$")
    pause_s: float | None = Field(default=None, ge=0, le=120)
    words: list[dict] | None = Field(default=None, max_length=1000)
    inject: str | None = None
    model_verdict: VerdictIn | None = None


class ResumeIn(TokenIn):
    choice: str = Field(pattern="^(continue|stop)$")


class FinishIn(TokenIn):
    belief_after: int = Field(ge=0, le=100)
    post: PostIn = Field(default_factory=PostIn)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _load(token: str) -> Session:
    try:
        return Session.from_state(unseal(token), team)
    except BadToken as exc:
        raise HTTPException(409, "This session has expired. Please start again.") from exc


def _reply(session: Session) -> dict:
    return {**session.view(), "token": seal(session.to_state())}


@app.get("/api/status")
def status() -> dict:
    return {"llm": team.mode, "plan_model": PLAN_MODEL, "fast_model": FAST_MODEL, "injectable": list(INJECTABLE),
            "model_connected": device.configured(), "device": device.status()}


@app.get("/api/device/status")
def device_status() -> dict:
    return device.status()


@app.post("/api/reading")
def reading(body: ReadingIn, request: Request) -> dict:
    """One body reading, classified by fixed code. No language model sees it."""
    _limit(request)
    if body.mode == "device":
        if not device.configured():
            raise HTTPException(409, "The wearable isn't connected yet. Use the demo stream, or skip the reading.")
        latest = device.fetch_latest()
        if latest is None:
            raise HTTPException(503, "The wearable didn't send a usable reading. Check it's on, then try again.")
        window = latest["samples"][-device.READING_S:]
        summary = device.summarise(window, body.baseline_hr, body.baseline_eda, latest["verdict"], device.DEVICE_LABEL)
        return {"summary": summary, "samples": device.sample_rows(window), "cursor": None}
    samples, cursor = device.demo_samples(body.scenario, body.t, body.previous)
    base_hr, base_eda = device.demo_baseline()
    return {"summary": device.summarise(samples, base_hr, base_eda), "samples": device.sample_rows(samples), "cursor": cursor}


@app.post("/api/check")
def check(body: CheckIn, request: Request) -> dict:
    """The anxiety check. Fixed code only, and nothing is stored."""
    _limit(request)
    if not body.consent:
        raise HTTPException(422, "Consent is required before a check.")
    if any(v not in (0, 1, 2, 3) for v in body.gad_items):
        raise HTTPException(422, "GAD-7 answers must each be from 0 to 3.")
    return checks.evaluate(body.gad_items, body.impact, body.self_harm, body.heart_condition, body.crisis,
                           body.body.model_dump() if body.body else None, checks.parse_checks(body.history), _now_ms(),
                           body.adult, body.other_condition)


@app.post("/api/screen")
def screen(body: ScreenIn, request: Request) -> dict:
    """Fixed code only, so anyone who needs support gets it before any consent or language model."""
    _limit(request)
    if any(v not in (0, 1, 2, 3) for v in body.gad_items):
        raise HTTPException(422, "GAD-7 answers must each be from 0 to 3.")
    decision = check_entry(sum(body.gad_items), body.self_harm, body.heart_condition, body.crisis, body.adult, body.other_condition)
    status = "minor" if not body.adult and decision.action is Action.REFER else {Action.CRISIS: "crisis", Action.REFER: "referred"}.get(decision.action, "allow")
    return {"status": status, "exit_message": decision.message}


@app.post("/api/session")
def create(setup: SetupIn, request: Request) -> dict:
    _limit(request)
    if REQUIRE_CHECK and not setup.check_token:
        raise HTTPException(409, "Guided sessions need an anxiety check from the last 24 hours. Please do a check first.")
    if setup.check_token:
        # The check already asked the safety questions and took consent, and it must be recent.
        try:
            basis = checks.open_token(setup.check_token)
        except BadToken as exc:
            raise HTTPException(409, "Guided sessions need an anxiety check from the last 24 hours. Please do a check first.") from exc
        setup.gad_items = basis["gad_items"]
        setup.consent, setup.self_harm, setup.heart_condition, setup.crisis = True, False, False, False
    if not setup.consent:
        raise HTTPException(422, "Consent is required before starting.")
    if setup.gad_items is None or any(v not in (0, 1, 2, 3) for v in setup.gad_items):
        raise HTTPException(422, "GAD-7 answers must each be from 0 to 3.")
    # Only numbers and a theme from the record reach the planner, never the person's earlier words.
    facts = setup.model_dump(exclude={"history", "check_token"})
    facts["impact"] = basis["impact"] if setup.check_token else None
    facts["body_state"] = basis["body_state"] if setup.check_token else None
    facts["body_demo"] = basis["body_demo"] if setup.check_token else False
    facts["record_note"] = record.plan_note(record.parse_predictions(setup.history), _now_ms())
    session = Session(facts, team if _llm_ok() else scripted_team)
    session.start()
    if session.status == "unclear":
        raise HTTPException(422, session.exit_message)
    return _reply(session)


@app.post("/api/calibrate")
def calibrate(body: TokenIn, request: Request) -> dict:
    _limit(request)
    session = _load(body.token)
    try:
        session.calibrate()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _reply(session)


@app.post("/api/answer")
def answer(body: AnswerIn, request: Request) -> dict:
    _limit(request)
    session = _load(body.token)
    metrics = speech_metrics(body.text, body.words)
    try:
        # A verdict is only believed when the device link is switched on, so nobody can label their own as the model's.
        verdict = body.model_verdict.model_dump() if body.model_verdict and device.configured() else None
        session.answer(body.text, body.source, body.pause_s, metrics, body.inject, verdict)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _reply(session)


@app.post("/api/resume")
def resume(body: ResumeIn, request: Request) -> dict:
    _limit(request)
    session = _load(body.token)
    try:
        session.resume(body.choice)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _reply(session)


@app.post("/api/stop")
def stop(body: TokenIn) -> dict:
    # Never rate limited: stopping must always work.
    session = _load(body.token)
    session.stop()
    return _reply(session)


@app.post("/api/finish")
def finish(body: FinishIn, request: Request) -> dict:
    _limit(request)
    session = _load(body.token)
    try:
        session.finish(body.belief_after, body.post.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _reply(session)


@app.post("/api/transcribe")
async def transcribe(request: Request) -> dict:
    _limit(request)
    # Only a session that is waiting for a response may use speech recognition.
    if _load(request.headers.get("x-session-token", "")).status != "practice":
        raise HTTPException(409, "This session isn't waiting for a response.")
    audio = await request.body()
    if not audio:
        raise HTTPException(422, "No audio received.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "That recording is too long. Keep answers under a minute.")
    kind = request.headers.get("content-type", "audio/webm").split(";")[0]
    extension = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4", "audio/wav": "wav"}.get(kind, "webm")
    result = voice.transcribe(audio, f"answer.{extension}")
    if result is None:
        raise HTTPException(503, "Speech recognition isn't available right now. Type your answer instead.")
    return result


@app.post("/api/speak")
def speak(body: SpeakIn, request: Request) -> Response:
    _limit(request)
    # Only the guide's current line can be spoken, so this can't be used as a free voice service.
    question = _load(body.token).question
    if not question or question["line"] != body.text or not output_is_safe(body.text):
        raise HTTPException(422, "This text can't be spoken.")
    audio = voice.speak(body.text, body.member)
    if audio is None:
        raise HTTPException(503, "Server voice isn't available.")
    return Response(content=audio, media_type="audio/wav")


@app.post("/api/record/stats")
def record_stats(body: RecordIn, request: Request) -> dict:
    """Fixed code only: no language model, and nothing is stored."""
    _limit(request)
    return record.compute_stats(record.parse_predictions(body.predictions), _now_ms())


@app.post("/api/record/check")
def record_check(body: WorryIn, request: Request) -> dict:
    """Fixed code only: the final wording of a prediction, checked before it's saved."""
    _limit(request)
    return record.safety_scan([body.worry]) or {"status": "ok"}


@app.post("/api/record/sharpen")
def record_sharpen(body: WorryIn, request: Request) -> dict:
    _limit(request)
    return record.sharpen(body.worry, team.llm if _llm_ok() else None, team.verifier)


@app.post("/api/record/review")
def record_review(body: RecordIn, request: Request) -> dict:
    _limit(request)
    client = team.llm.client if team.llm and _llm_ok() else None
    seen = None if body.checks is None else checks.parse_checks(body.checks)
    return record.review(record.parse_predictions(body.predictions), _now_ms(), client, body.just_checked, seen, team.verifier)


# Local development only. On Vercel the CDN serves public/ directly.
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(PUBLIC / "index.html")


@app.get("/room", include_in_schema=False)
def old_room() -> RedirectResponse:
    return RedirectResponse("/checkin", status_code=308)


@app.get("/{page}", include_in_schema=False)
def page(page: str) -> FileResponse:
    target = PUBLIC / f"{page}.html"
    if target.is_file() and page.isidentifier():
        return FileResponse(target)
    candidate = PUBLIC / page
    if candidate.is_file() and candidate.parent == PUBLIC:
        return FileResponse(candidate)
    return _not_found()


def _not_found():
    # On serverless hosts public/ lives on the CDN, so it may not exist beside this function.
    page_file = PUBLIC / "404.html"
    if page_file.is_file():
        return FileResponse(page_file, status_code=404)
    return HTMLResponse(NOT_FOUND_HTML, status_code=404)


if PUBLIC.is_dir():
    for folder in ("css", "js", "fonts", "icons", "vendor", "img"):
        if (PUBLIC / folder).is_dir():
            app.mount(f"/{folder}", StaticFiles(directory=PUBLIC / folder), name=folder)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
