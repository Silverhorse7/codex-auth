import time
from unittest.mock import patch

import httpx
import pytest

from codex_auth.auth import _exchange_code, authenticate, refresh_access_token
from codex_auth.tokens import AuthTokens, TokenStore

_REQ = httpx.Request("POST", "https://auth.openai.com/oauth/token")

_TOKEN_RESP = {
    "access_token": "test_access",
    "refresh_token": "test_refresh",
    "id_token": "header.eyJjaGF0Z3B0X2FjY291bnRfaWQiOiAiYWNjXzEyMyJ9.sig",
    "expires_in": 3600,
}


class TestExchangeCode:
    def test_success(self):
        resp = httpx.Response(200, json=_TOKEN_RESP, request=_REQ)
        with patch("codex_auth.auth.httpx.post", return_value=resp):
            tok = _exchange_code("code", "http://localhost:1455/auth/callback", "verifier")
            assert tok["access_token"] == "test_access"

    def test_failure(self):
        resp = httpx.Response(401, json={"error": "invalid_grant"}, request=_REQ)
        with patch("codex_auth.auth.httpx.post", return_value=resp):
            with pytest.raises(httpx.HTTPStatusError):
                _exchange_code("bad", "http://localhost:1455/auth/callback", "v")


class TestRefresh:
    def test_success(self):
        resp = httpx.Response(200, json=_TOKEN_RESP, request=_REQ)
        with patch("codex_auth.auth.httpx.post", return_value=resp):
            assert refresh_access_token("old")["access_token"] == "test_access"

    def test_failure(self):
        resp = httpx.Response(400, json={"error": "invalid_grant"}, request=_REQ)
        with patch("codex_auth.auth.httpx.post", return_value=resp):
            with pytest.raises(httpx.HTTPStatusError):
                refresh_access_token("bad")


class TestAuthenticate:
    def test_returns_cached(self, tmp_path):
        store = TokenStore(auth_file=tmp_path / "auth.json")
        store.save(AuthTokens("cached", "r", time.time() * 1000 + 60_000, "a"))
        with patch("codex_auth.auth._token_store", store):
            assert authenticate().access_token == "cached"

    def test_refreshes_expired(self, tmp_path):
        store = TokenStore(auth_file=tmp_path / "auth.json")
        store.save(AuthTokens("old", "refresh_tok", 1000, "a"))
        resp = httpx.Response(200, json=_TOKEN_RESP, request=_REQ)
        with (
            patch("codex_auth.auth._token_store", store),
            patch("codex_auth.auth.httpx.post", return_value=resp),
        ):
            assert authenticate().access_token == "test_access"
