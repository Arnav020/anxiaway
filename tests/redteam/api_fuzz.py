"""Throw malformed, oversized, wrong-type and out-of-order requests at every endpoint. Any 500 is a failure."""
import random
import sys

sys.path.insert(0, ".")
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from toward import safety  # noqa: E402
from toward.agents import AgentTeam  # noqa: E402

team = AgentTeam(llm=None); team.llm = None
server.team = team; safety.install(None); server.REQUIRE_CHECK = False
server._RATE_PER_MINUTE = 10**9
http = TestClient(server.app, raise_server_exceptions=False)
SETUP = dict(event="Viva", days_until=6, prediction="If I pause, they'll think I'm incompetent", belief_before=80,
             gad_items=[1, 1, 2, 1, 0, 1, 1], self_harm=False, heart_condition=False, crisis=False, consent=True)
NASTY = [None, "", " ", "a" * 100_000, 0, -1, 10**30, 1.5e308, float("nan") if False else 3.3, True, [], {}, [None], {"$gt": ""},
         "<script>alert(1)</script>", "'; DROP TABLE x;--", "\u0000‮퟿", "😀" * 500, "../../etc/passwd", "{{7*7}}"]
POSTS = ["/api/reading", "/api/check", "/api/screen", "/api/session", "/api/calibrate", "/api/answer", "/api/resume", "/api/stop",
         "/api/finish", "/api/speak", "/api/record/stats", "/api/record/check", "/api/record/sharpen", "/api/record/review"]
bad = []


def hit(path, **kw):
    try:
        r = http.post(path, **kw)
    except Exception as exc:  # noqa: BLE001
        bad.append((path, "EXC " + type(exc).__name__, str(kw)[:120])); return None
    if r.status_code >= 500:
        bad.append((path, r.status_code, str(kw)[:160]))
    return r


random.seed(7)
for p in POSTS:
    hit(p, content=b"not json", headers={"content-type": "application/json"})
    hit(p, content=b"\xff\xfe\x00", headers={"content-type": "application/json"})
    hit(p, json=None); hit(p, json=[]); hit(p, json="x"); hit(p, json={})
    for v in NASTY:
        hit(p, json={k: v for k in ("token", "text", "worry", "predictions", "checks", "gad_items", "event", "state", "mode", "choice", "member", "just_checked")})
# Field-by-field corruption of a valid session setup
for k in SETUP:
    for v in NASTY:
        hit("/api/session", json={**SETUP, k: v})
# Real session, then out-of-order and replayed calls
view = http.post("/api/session", json=SETUP).json()
tok = view.get("token")
for p in ("/api/finish", "/api/resume", "/api/answer", "/api/calibrate", "/api/speak", "/api/stop"):
    hit(p, json={"token": tok, "text": "hi", "choice": "continue", "belief_after": 50, "member": "x"})
for t in [tok[:-3] + "abc", tok + "x", "", "a.b.c", tok.upper()]:
    hit("/api/answer", json={"token": t, "text": "hello"})
# Record endpoints with corrupted predictions
for v in NASTY:
    hit("/api/record/review", json={"predictions": [{"id": v, "text": v, "created": v, "due": v, "belief": v, "outcome": v}]})
    hit("/api/record/stats", json={"predictions": [v] * 3})
    hit("/api/record/check", json={"predictions": [{"text": v}], "checks": [{"x": v}]})
hit("/api/record/review", json={"predictions": [{"id": str(i), "text": "x" * 300, "created": i} for i in range(200)]})
# Static pages and path tricks
for path in ["/..%2f..%2fserver.py", "/server.py", "/.env", "/%2e%2e/.env", "/js/../../.env", "/css/..%2f..%2f.env", "/a" * 50, "/index.html"]:
    r = http.get(path)
    if r.status_code >= 500 or b"GROQ_API_KEY" in r.content:
        bad.append((path, r.status_code, "leak or crash"))
print("failures", len(bad))
for b in bad[:40]:
    print(" ", b)
