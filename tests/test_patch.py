import time
from unittest.mock import MagicMock

import httpx

from codex_auth.patch import CodexTransport
from codex_auth.tokens import AuthTokens


def _auth() -> AuthTokens:
    return AuthTokens("test_tok", "ref", time.time() * 1000 + 60_000, "acc_1")


def _transport(auth=None):
    mock = MagicMock(spec=httpx.BaseTransport)
    mock.handle_request.return_value = httpx.Response(200, json={})
    return CodexTransport(auth_tokens=auth or _auth(), wrapped=mock), mock


class TestCodexTransport:
    def test_rewrites_chat_completions(self):
        tr, mock = _transport()
        tr.handle_request(httpx.Request("POST", "https://api.openai.com/v1/chat/completions", json={"model": "m", "messages": []}))
        url = str(mock.handle_request.call_args[0][0].url)
        assert "chatgpt.com" in url and "/backend-api/codex/responses" in url

    def test_rewrites_responses(self):
        tr, mock = _transport()
        tr.handle_request(httpx.Request("POST", "https://api.openai.com/v1/responses", json={"model": "m"}))
        assert "chatgpt.com" in str(mock.handle_request.call_args[0][0].url)

    def test_passthrough_other_urls(self):
        tr, mock = _transport()
        tr.handle_request(httpx.Request("GET", "https://api.openai.com/v1/models"))
        assert "api.openai.com" in str(mock.handle_request.call_args[0][0].url)

    def test_auth_header(self):
        tr, mock = _transport()
        tr.handle_request(httpx.Request("POST", "https://api.openai.com/v1/chat/completions", json={"messages": []}))
        assert mock.handle_request.call_args[0][0].headers["authorization"] == "Bearer test_tok"

    def test_account_id_header(self):
        tr, mock = _transport()
        tr.handle_request(httpx.Request("POST", "https://api.openai.com/v1/chat/completions", json={}))
        assert mock.handle_request.call_args[0][0].headers["chatgpt-account-id"] == "acc_1"

    def test_no_account_id_when_empty(self):
        auth = AuthTokens("tok", expires=time.time() * 1000 + 60_000)
        tr, mock = _transport(auth)
        tr.handle_request(httpx.Request("POST", "https://api.openai.com/v1/chat/completions", json={}))
        assert "chatgpt-account-id" not in mock.handle_request.call_args[0][0].headers


class TestPatchLifecycle:
    def test_sync_patch_unpatch(self):
        from codex_auth.patch import apply_patch, remove_patch, _original_init
        import openai

        real = _original_init
        remove_patch()
        assert openai.OpenAI.__init__ is real
        apply_patch()
        assert openai.OpenAI.__init__ is not real
        remove_patch()
        assert openai.OpenAI.__init__ is real
        apply_patch()

    def test_async_patch_unpatch(self):
        from codex_auth.patch import apply_patch, remove_patch, _original_async_init
        import openai

        real = _original_async_init
        remove_patch()
        assert openai.AsyncOpenAI.__init__ is real
        apply_patch()
        assert openai.AsyncOpenAI.__init__ is not real
        remove_patch()
        assert openai.AsyncOpenAI.__init__ is real
        apply_patch()
