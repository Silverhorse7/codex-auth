import base64
import hashlib
import json
import time
from pathlib import Path

import pytest

from codex_auth.tokens import (
    AuthTokens,
    TokenStore,
    extract_account_id,
    generate_pkce,
    generate_state,
    parse_jwt_claims,
)


def _make_jwt(payload: dict) -> str:
    hdr = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{hdr}.{body}.sig"


class TestPKCE:
    def test_length_and_charset(self):
        valid = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        verifier, _ = generate_pkce()
        assert len(verifier) == 64
        assert all(c in valid for c in verifier)

    def test_challenge_matches_verifier(self):
        verifier, challenge = generate_pkce()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        assert challenge == expected

    def test_unique(self):
        assert generate_pkce()[0] != generate_pkce()[0]


class TestGenerateState:
    def test_nonempty_and_unique(self):
        a, b = generate_state(), generate_state()
        assert a and b and a != b


class TestParseJwtClaims:
    def test_valid(self):
        claims = parse_jwt_claims(_make_jwt({"sub": "u1"}))
        assert claims and claims["sub"] == "u1"

    def test_invalid(self):
        assert parse_jwt_claims("notajwt") is None
        assert parse_jwt_claims("a.!!!.c") is None


class TestExtractAccountId:
    def test_direct_claim(self):
        assert extract_account_id({"id_token": _make_jwt({"chatgpt_account_id": "a1"})}) == "a1"

    def test_nested_claim(self):
        tok = _make_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "a2"}})
        assert extract_account_id({"id_token": tok}) == "a2"

    def test_org_fallback(self):
        tok = _make_jwt({"organizations": [{"id": "org_1"}]})
        assert extract_account_id({"id_token": tok}) == "org_1"

    def test_none_when_absent(self):
        assert extract_account_id({"id_token": _make_jwt({"sub": "x"})}) is None

    def test_falls_through_to_access_token(self):
        result = extract_account_id({
            "id_token": _make_jwt({"sub": "x"}),
            "access_token": _make_jwt({"chatgpt_account_id": "from_at"}),
        })
        assert result == "from_at"


class TestAuthTokens:
    def test_expiry(self):
        assert not AuthTokens(access_token="t").is_expired()
        assert not AuthTokens(access_token="t", expires=time.time() * 1000 + 60_000).is_expired()
        assert AuthTokens(access_token="t", expires=1000).is_expired()


class TestTokenStore:
    def test_roundtrip(self, tmp_path: Path):
        store = TokenStore(auth_file=tmp_path / "auth.json")
        store.save(AuthTokens("at", "rt", 9999999999999.0, "acc"))
        loaded = store.load()
        assert loaded and loaded.access_token == "at" and loaded.account_id == "acc"

    def test_permissions(self, tmp_path: Path):
        f = tmp_path / "auth.json"
        TokenStore(auth_file=f).save(AuthTokens(access_token="t"))
        assert oct(f.stat().st_mode & 0o777) == "0o600"

    def test_missing_file(self, tmp_path: Path):
        assert TokenStore(auth_file=tmp_path / "nope.json").load() is None

    def test_env_var_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        store = TokenStore(auth_file=tmp_path / "auth.json")
        store.save(AuthTokens(access_token="file"))
        monkeypatch.setenv("CODEX_AUTH_TOKEN", "env")
        loaded = store.load()
        assert loaded and loaded.access_token == "env"

    def test_clear(self, tmp_path: Path):
        f = tmp_path / "auth.json"
        store = TokenStore(auth_file=f)
        store.save(AuthTokens(access_token="t"))
        assert f.exists()
        store.clear()
        assert not f.exists()
