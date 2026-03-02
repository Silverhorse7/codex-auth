"""Browser (PKCE) and device-code OAuth flows."""

from __future__ import annotations

import http.server
import logging
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from typing import Any

import httpx

from .tokens import (
    CLIENT_ID,
    ISSUER,
    OAUTH_PORT,
    OAUTH_SCOPES,
    TOKEN_URL,
    USER_AGENT,
    AuthTokens,
    TokenStore,
    generate_pkce,
    generate_state,
)

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_token_store = TokenStore()


def _exchange_code(code: str, redirect_uri: str, verifier: str) -> dict[str, Any]:
    r = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    r = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def _authorize_url(redirect_uri: str, challenge: str, state: str) -> str:
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "state": state,
        "originator": "codex-auth",
    })
    return f"{ISSUER}/oauth/authorize?{params}"


_HTML_OK = (
    "<!doctype html><html><head><title>Codex Auth</title>"
    "<style>body{font-family:system-ui,sans-serif;display:flex;justify-content:center;"
    "align-items:center;height:100vh;margin:0;background:#131010;color:#f1ecec}"
    ".c{text-align:center;padding:2rem}h1{margin-bottom:1rem}p{color:#b7b1b1}</style>"
    "</head><body><div class='c'><h1>Authorization Successful</h1>"
    "<p>You can close this window.</p></div>"
    "<script>setTimeout(()=>window.close(),2000)</script></body></html>"
)

_HTML_ERR = (
    "<!doctype html><html><head><title>Codex Auth</title>"
    "<style>body{font-family:system-ui,sans-serif;display:flex;justify-content:center;"
    "align-items:center;height:100vh;margin:0;background:#131010;color:#f1ecec}"
    ".c{text-align:center;padding:2rem}h1{color:#fc533a;margin-bottom:1rem}"
    "p{color:#b7b1b1}.e{color:#ff917b;font-family:monospace;margin-top:1rem;"
    "padding:1rem;background:#3c140d;border-radius:.5rem}</style>"
    "</head><body><div class='c'><h1>Authorization Failed</h1>"
    "<p>An error occurred.</p><div class='e'>%s</div></div></body></html>"
)


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None
    error: str | None = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return

        if "error" in qs:
            msg = qs.get("error_description", qs["error"])[0]
            _CallbackHandler.error = msg
            self._html(200, _HTML_ERR % msg)
            return

        code = qs.get("code", [None])[0]
        if not code:
            _CallbackHandler.error = "Missing authorization code"
            self._html(400, _HTML_ERR % "Missing authorization code")
            return

        _CallbackHandler.code = code
        _CallbackHandler.state = qs.get("state", [None])[0]
        self._html(200, _HTML_OK)

    def _html(self, status: int, body: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, format: str, *args: Any) -> None:
        pass


def browser_auth() -> AuthTokens:
    verifier, challenge = generate_pkce()
    state = generate_state()
    redirect = f"http://localhost:{OAUTH_PORT}/auth/callback"
    url = _authorize_url(redirect, challenge, state)

    _CallbackHandler.code = _CallbackHandler.state = _CallbackHandler.error = None

    srv = http.server.HTTPServer(("localhost", OAUTH_PORT), _CallbackHandler)
    srv.timeout = 300

    def serve() -> None:
        while _CallbackHandler.code is None and _CallbackHandler.error is None:
            srv.handle_request()

    t = threading.Thread(target=serve, daemon=True)
    t.start()

    print(f"Opening browser for authentication...\nIf it doesn't open, visit:\n  {url}")
    webbrowser.open(url)

    t.join(timeout=300)
    srv.server_close()

    if _CallbackHandler.error:
        raise RuntimeError(f"OAuth error: {_CallbackHandler.error}")
    if not _CallbackHandler.code:
        raise TimeoutError("OAuth callback timed out")
    if _CallbackHandler.state != state:
        raise RuntimeError("OAuth state mismatch — possible CSRF")

    raw = _exchange_code(_CallbackHandler.code, redirect, verifier)
    auth = AuthTokens.from_response(raw)
    _token_store.save(auth)
    return auth


def device_auth() -> AuthTokens:
    r = httpx.post(
        f"{ISSUER}/api/accounts/deviceauth/usercode",
        json={"client_id": CLIENT_ID},
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()

    device_id = data["device_auth_id"]
    user_code = data["user_code"]
    interval = max(int(data.get("interval", 5)), 1)

    print(f"\nTo authenticate, visit: {ISSUER}/codex/device")
    print(f"Enter code: {user_code}\n")

    for _ in range(300 // interval):
        time.sleep(interval + 3)

        poll = httpx.post(
            f"{ISSUER}/api/accounts/deviceauth/token",
            json={"device_auth_id": device_id, "user_code": user_code},
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            timeout=_TIMEOUT,
        )

        if poll.status_code in (403, 404):
            continue

        if poll.is_success:
            d = poll.json()
            tok = httpx.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": d["authorization_code"],
                    "redirect_uri": f"{ISSUER}/deviceauth/callback",
                    "client_id": CLIENT_ID,
                    "code_verifier": d["code_verifier"],
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=_TIMEOUT,
            )
            tok.raise_for_status()
            auth = AuthTokens.from_response(tok.json())
            _token_store.save(auth)
            print("Authentication successful!")
            return auth

        raise RuntimeError(f"Device auth failed: HTTP {poll.status_code}")

    raise TimeoutError("Device authentication timed out")


def _has_display() -> bool:
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def authenticate(force: bool = False) -> AuthTokens:
    """Return valid tokens — from cache, refresh, or a fresh OAuth flow."""
    if not force:
        existing = _token_store.load()
        if existing and existing.access_token:
            if not existing.is_expired():
                return existing
            if existing.refresh_token:
                try:
                    raw = refresh_access_token(existing.refresh_token)
                    auth = AuthTokens.from_response(
                        raw, existing.refresh_token, existing.account_id,
                    )
                    _token_store.save(auth)
                    return auth
                except Exception:
                    log.debug("Token refresh failed, re-authenticating", exc_info=True)

    if _has_display():
        try:
            return browser_auth()
        except Exception as exc:
            print(f"Browser auth failed ({exc}), trying device flow...")

    return device_auth()
