"""Toward API and local static server.

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
from toward.replay import INJECTABLE
from toward.session import Session
from toward.state import BadToken, seal, unseal

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
MAX_AUDIO_BYTES = 4 * 1024 * 1024
NOT_FOUND_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Page not found | Toward</title>
<meta name="robots" content="noindex"><link rel="icon" href="/img/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/css/base.css"><link rel="stylesheet" href="/css/site.css"></head>
<body><main class="notfound"><div class="notfound-face" aria-hidden="true"><i></i><i></i><b></b></div>
<h1 class="heading-3">The panel can't find that page.</h1>
<p class="body-3 muted">It may have moved, or the address may be wrong. Take your time.</p>
<a class="btn btn-cta btn-lg" href="/">Back to home</a></main></body></html>"""

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
app = FastAPI(title="Toward", docs_url=None, redoc_url=None)
team = AgentTeam()
voice = Voice()

# Best effort limit per server instance, to slow down misuse of the public demo.
_RATE_PER_MINUTE = 90
_hits: dict[str, deque] = defaultdict(deque)


def _limit(request: Request) -> None:
    client = request.headers.get("x-forwarded-for", request.client.host if request.client else "local").split(",")[0]
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
    gad_items: list[int] = Field(min_length=7, max_length=7)
    self_harm: bool
    heart_condition: bool
    crisis: bool
    consent: bool


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


def _load(token: str) -> Session:
    try:
        return Session.from_state(unseal(token), team)
    except BadToken as exc:
        raise HTTPException(409, "This session has expired. Please start again.") from exc


def _reply(session: Session) -> dict:
    return {**session.view(), "token": seal(session.to_state())}


@app.get("/api/status")
def status() -> dict:
    return {"llm": team.mode, "plan_model": PLAN_MODEL, "fast_model": FAST_MODEL, "injectable": list(INJECTABLE), "model_connected": False}


@app.post("/api/screen")
def screen(body: ScreenIn, request: Request) -> dict:
    """Fixed code only, so anyone who needs support gets it before any consent or language model."""
    _limit(request)
    if any(v not in (0, 1, 2, 3) for v in body.gad_items):
        raise HTTPException(422, "GAD-7 answers must each be from 0 to 3.")
    decision = check_entry(sum(body.gad_items), body.self_harm, body.heart_condition, body.crisis)
    status = {Action.CRISIS: "crisis", Action.REFER: "referred"}.get(decision.action, "allow")
    return {"status": status, "exit_message": decision.message}


@app.post("/api/session")
def create(setup: SetupIn, request: Request) -> dict:
    _limit(request)
    if not setup.consent:
        raise HTTPException(422, "Consent is required before starting.")
    if any(v not in (0, 1, 2, 3) for v in setup.gad_items):
        raise HTTPException(422, "GAD-7 answers must each be from 0 to 3.")
    session = Session(setup.model_dump(), team)
    session.start()
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
        verdict = body.model_verdict.model_dump() if body.model_verdict else None
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
