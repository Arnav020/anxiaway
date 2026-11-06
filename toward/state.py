"""Signed session tokens, so the server keeps nothing between requests.

Serverless hosts can't hold sessions in memory. The session travels with the browser,
signed so the safety state and limits can't be edited on the way back.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import zlib

_SECRET = (os.getenv("TOWARD_SECRET") or secrets.token_hex(32)).encode()


class BadToken(ValueError):
    pass


def seal(state: dict) -> str:
    raw = zlib.compress(json.dumps(state, separators=(",", ":")).encode())
    mac = hmac.new(_SECRET, raw, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(mac + raw).decode()


def unseal(token: str) -> dict:
    try:
        blob = base64.urlsafe_b64decode(token.encode())
        mac, raw = blob[:16], blob[16:]
        if not hmac.compare_digest(mac, hmac.new(_SECRET, raw, hashlib.sha256).digest()[:16]):
            raise BadToken("Session signature doesn't match")
        return json.loads(zlib.decompress(raw))
    except BadToken:
        raise
    except Exception as exc:
        raise BadToken("Session couldn't be read") from exc
