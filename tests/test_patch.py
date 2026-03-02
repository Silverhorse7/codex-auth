import json
import time
from unittest.mock import MagicMock

import httpx

from codex_auth.patch import (
    CodexTransport,
    _buffer_sse,
    _chat_completions_to_responses,
    _extract_sse_response,
    _normalize_responses_body,
    _responses_to_chat_completion,
)
from codex_auth.tokens import AuthTokens


def _auth() -> AuthTokens:
    return AuthTokens("test_tok", "ref", time.time() * 1000 + 60_000, "acc_1")


def _transport(auth=None):
    mock = MagicMock(spec=httpx.BaseTransport)
    mock.handle_request.return_value = httpx.Response(200, json={})
    return CodexTransport(auth_tokens=auth or _auth(), wrapped=mock), mock


_SSE_BODY = (
    b'data: {"type":"response.created","response":{"id":"r1"}}\n\n'
    b'data: {"type":"response.output_text.delta","delta":"hello"}\n\n'
    b'data: {"type":"response.completed","response":{"id":"r1","object":"response","model":"m","status":"completed","output_text":"hello","created_at":1000,"usage":{"input_tokens":5,"output_tokens":2}}}\n\n'
    b"data: [DONE]\n\n"
)


def _sse_transport(auth=None):
    mock = MagicMock(spec=httpx.BaseTransport)
    mock.handle_request.return_value = httpx.Response(
        200, content=_SSE_BODY, headers={"content-type": "text/event-stream"},
    )
    return CodexTransport(auth_tokens=auth or _auth(), wrapped=mock), mock


class TestBodyTransforms:
    def test_normalize_preserves_tools(self):
        body = {"model": "m", "input": "hi", "tools": [{"type": "web_search"}]}
        out = _normalize_responses_body(body)
        assert out["tools"] == [{"type": "web_search"}]
        assert out["stream"] is True
        assert isinstance(out["input"], list)

    def test_normalize_preserves_instructions(self):
        body = {"model": "m", "input": "hi", "instructions": "be brief"}
        assert _normalize_responses_body(body)["instructions"] == "be brief"

    def test_chat_to_responses_forwards_tools(self):
        body = {"model": "m", "messages": [], "tools": [{"type": "function", "function": {"name": "f"}}]}
        out = _chat_completions_to_responses(body)
        assert out["tools"] == body["tools"]

    def test_chat_to_responses_maps_max_tokens(self):
        body = {"model": "m", "messages": [], "max_tokens": 100}
        assert _chat_completions_to_responses(body)["max_output_tokens"] == 100


class TestSSEBuffering:
    def test_extract_sse_response(self):
        resp = _extract_sse_response(_SSE_BODY)
        assert resp is not None
        assert resp["id"] == "r1"
        assert resp["output_text"] == "hello"

    def test_extract_returns_none_on_garbage(self):
        assert _extract_sse_response(b"not sse at all") is None

    def test_buffer_sse_as_responses(self):
        result = _buffer_sse(_SSE_BODY)
        assert result is not None
        data = json.loads(result.content)
        assert data["id"] == "r1"
        assert data["output_text"] == "hello"

    def test_buffer_sse_as_chat_completion(self):
        result = _buffer_sse(_SSE_BODY, as_chat_completion=True)
        assert result is not None
        data = json.loads(result.content)
        assert data["object"] == "chat.completion"
        assert data["choices"][0]["message"]["content"] == "hello"
        assert data["choices"][0]["finish_reason"] == "stop"

    def test_buffer_returns_none_on_bad_sse(self):
        assert _buffer_sse(b"garbage") is None


class TestResponseConversion:
    def test_responses_to_chat_completion(self):
        resp = {"id": "r1", "model": "m", "status": "completed", "output_text": "hi", "created_at": 99, "usage": {"input_tokens": 1}}
        cc = _responses_to_chat_completion(resp)
        assert cc["object"] == "chat.completion"
        assert cc["choices"][0]["message"]["content"] == "hi"
        assert cc["created"] == 99
        assert cc["usage"]["input_tokens"] == 1

    def test_incomplete_status_gives_length(self):
        resp = {"id": "r1", "model": "m", "status": "incomplete", "output_text": ""}
        assert _responses_to_chat_completion(resp)["choices"][0]["finish_reason"] == "length"


class TestTransportStreaming:
    def test_nonstreaming_responses_gets_buffered(self):
        tr, mock = _sse_transport()
        request = httpx.Request("POST", "https://api.openai.com/v1/responses", json={"model": "m", "input": "hi"})
        response = tr.handle_request(request)
        data = json.loads(response.content)
        assert data["id"] == "r1"
        assert data["output_text"] == "hello"

    def test_streaming_responses_passes_through(self):
        tr, mock = _sse_transport()
        request = httpx.Request("POST", "https://api.openai.com/v1/responses", json={"model": "m", "input": "hi", "stream": True})
        response = tr.handle_request(request)
        assert b"data:" in response.content

    def test_nonstreaming_chat_completions_gets_converted(self):
        tr, mock = _sse_transport()
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions", json={"model": "m", "messages": []})
        response = tr.handle_request(request)
        data = json.loads(response.content)
        assert data["object"] == "chat.completion"
        assert data["choices"][0]["message"]["content"] == "hello"


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
