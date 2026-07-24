"""Management-PIN authentication for privileged browser operations.

Cabinets continue to authenticate with CONNECTOR_API_TOKEN. Browser sessions
use a signed, HttpOnly cookie so the PIN is never stored in localStorage or
placed in a WebSocket URL.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from threading import Lock

from fastapi import Header, HTTPException, Request, WebSocket, status

from .config import settings


COOKIE_NAME = "zucchini_management"
COOKIE_VERSION = "v1"
LOGIN_WINDOW_SECONDS = 60
LOGIN_MAX_FAILURES = 8
_login_lock = Lock()
_login_failures: dict[str, list[float]] = {}


def configured() -> bool:
    return bool(settings.management_pin)


def pin_matches(candidate: str) -> bool:
    return configured() and hmac.compare_digest(candidate, settings.management_pin)


def check_login_rate(client: str) -> None:
    now = time.monotonic()
    with _login_lock:
        recent = [
            attempt
            for attempt in _login_failures.get(client, [])
            if now - attempt < LOGIN_WINDOW_SECONDS
        ]
        _login_failures[client] = recent
        if len(recent) >= LOGIN_MAX_FAILURES:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many incorrect PIN attempts; wait one minute",
            )


def record_login(client: str, success: bool) -> None:
    with _login_lock:
        if success:
            _login_failures.pop(client, None)
        else:
            _login_failures.setdefault(client, []).append(time.monotonic())


def _sign(payload: str) -> str:
    key = hashlib.sha256(
        ("zucchini-management:" + settings.management_pin).encode("utf-8")
    ).digest()
    return hmac.new(key, payload.encode("ascii"), hashlib.sha256).hexdigest()


def issue_cookie() -> str:
    expires = int(time.time()) + settings.management_session_seconds
    nonce = base64.urlsafe_b64encode(secrets.token_bytes(18)).decode("ascii").rstrip("=")
    payload = f"{COOKIE_VERSION}.{expires}.{nonce}"
    return f"{payload}.{_sign(payload)}"


def cookie_valid(cookie: str | None) -> bool:
    if not configured() or not cookie:
        return False
    try:
        version, expires_text, nonce, signature = cookie.split(".", 3)
        expires = int(expires_text)
    except (TypeError, ValueError):
        return False
    if version != COOKIE_VERSION or not nonce or expires < int(time.time()):
        return False
    payload = f"{version}.{expires}.{nonce}"
    return hmac.compare_digest(signature, _sign(payload))


def _bearer_valid(authorization: str | None) -> bool:
    if not settings.api_token or not authorization:
        return False
    return hmac.compare_digest(authorization, f"Bearer {settings.api_token}")


def require_management(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    if cookie_valid(request.cookies.get(COOKIE_NAME)) or _bearer_valid(authorization):
        return
    if not configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Management PIN is not configured",
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Management PIN required",
    )


def websocket_authorized(websocket: WebSocket) -> bool:
    return cookie_valid(websocket.cookies.get(COOKIE_NAME))
