from __future__ import annotations

import base64
import hashlib
import json
import os
import platform as _platform
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

VERSION = "0.1.1"

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
TOKEN_URL = f"{ISSUER}/oauth/token"
CODEX_API_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
CODEX_PARSED_URL = urlparse(CODEX_API_ENDPOINT)
OAUTH_PORT = 1455
OAUTH_SCOPES = "openid profile email offline_access"

AUTH_DIR = Path.home() / ".codex-auth"
AUTH_FILE = AUTH_DIR / "auth.json"

USER_AGENT = (
    f"codex-auth/{VERSION}"
    f" ({_platform.system()} {_platform.release()}; {_platform.machine()})"
)


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    verifier = "".join(secrets.choice(alphabet) for _ in range(64))
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def generate_state() -> str:
    return _base64url(secrets.token_bytes(32))


def parse_jwt_claims(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None


def extract_account_id(tokens: dict[str, str]) -> str | None:
    """Try id_token then access_token, checking several known claim locations."""
    for key in ("id_token", "access_token"):
        raw = tokens.get(key)
        if not raw:
            continue
        claims = parse_jwt_claims(raw)
        if not claims:
            continue

        if acct := claims.get("chatgpt_account_id"):
            return acct

        nested = claims.get("https://api.openai.com/auth")
        if isinstance(nested, dict) and (acct := nested.get("chatgpt_account_id")):
            return acct

        orgs = claims.get("organizations", [])
        if orgs and isinstance(orgs[0], dict) and (acct := orgs[0].get("id")):
            return acct

    return None


@dataclass
class AuthTokens:
    access_token: str
    refresh_token: str = ""
    expires: float = 0.0
    account_id: str = ""

    def is_expired(self) -> bool:
        return bool(self.expires) and time.time() * 1000 >= self.expires

    @classmethod
    def from_response(
        cls,
        raw: dict[str, Any],
        fallback_refresh: str = "",
        fallback_account_id: str = "",
    ) -> AuthTokens:
        return cls(
            access_token=raw["access_token"],
            refresh_token=raw.get("refresh_token", fallback_refresh),
            expires=time.time() * 1000 + raw.get("expires_in", 3600) * 1000,
            account_id=extract_account_id(raw) or fallback_account_id,
        )


@dataclass
class TokenStore:
    auth_file: Path = field(default_factory=lambda: AUTH_FILE)

    def load(self) -> AuthTokens | None:
        if env_token := os.environ.get("CODEX_AUTH_TOKEN"):
            return AuthTokens(access_token=env_token)
        try:
            data = json.loads(self.auth_file.read_text())
            return AuthTokens(
                access_token=data.get("access_token", ""),
                refresh_token=data.get("refresh_token", ""),
                expires=data.get("expires", 0),
                account_id=data.get("account_id", ""),
            )
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, tokens: AuthTokens) -> None:
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        self.auth_file.write_text(json.dumps({
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires": tokens.expires,
            "account_id": tokens.account_id,
        }, indent=2))
        self.auth_file.chmod(0o600)

    def clear(self) -> None:
        self.auth_file.unlink(missing_ok=True)
