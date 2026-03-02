"""Monkey-patch the OpenAI SDK to route requests through Codex OAuth."""

from __future__ import annotations

import json
import logging

import httpx
import openai

from .auth import authenticate, refresh_access_token
from .tokens import CODEX_PARSED_URL, USER_AGENT, AuthTokens, TokenStore

log = logging.getLogger(__name__)

_original_init = None
_original_async_init = None
_patched = False


def _chat_completions_to_responses(body: dict) -> dict:
    """The Codex endpoint only speaks the Responses API."""
    messages = body.get("messages", [])
    instructions: list[str] = []
    items: list[dict] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role in ("system", "developer"):
            if isinstance(content, str):
                instructions.append(content)
            elif isinstance(content, list):
                instructions.extend(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
        elif role in ("user", "assistant"):
            items.append({"role": role, "content": content})

    result: dict = {
        "model": body.get("model", ""),
        "instructions": "\n".join(instructions) or "You are a helpful assistant.",
        "input": items,
        "store": False,
        "stream": True,
    }
    for key in ("temperature", "max_output_tokens", "top_p", "tools"):
        if key in body:
            result[key] = body[key]
    if "max_tokens" in body and "max_output_tokens" not in body:
        result["max_output_tokens"] = body["max_tokens"]
    return result


def _normalize_responses_body(body: dict) -> dict:
    body = body.copy()
    if isinstance(body.get("input"), str):
        body["input"] = [{"role": "user", "content": body["input"]}]
    body.setdefault("instructions", "You are a helpful assistant.")
    body["store"] = False
    body["stream"] = True
    return body


def _extract_sse_response(raw: bytes) -> dict | None:
    """Parse SSE bytes and return the response dict from the completed event."""
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        try:
            event = json.loads(line[6:])
            if event.get("type") == "response.completed":
                return event.get("response")
        except json.JSONDecodeError:
            continue
    return None


def _responses_to_chat_completion(resp: dict) -> dict:
    """Convert a Responses API response object to chat.completions format."""
    status = resp.get("status", "completed")
    return {
        "id": resp.get("id", ""),
        "object": "chat.completion",
        "created": int(resp.get("created_at", 0)),
        "model": resp.get("model", ""),
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": resp.get("output_text", "")},
            "finish_reason": "stop" if status == "completed" else "length",
        }],
        "usage": resp.get("usage", {}),
    }


def _buffer_sse(raw: bytes, as_chat_completion: bool = False) -> httpx.Response | None:
    """Turn raw SSE bytes into a JSON httpx.Response, or None on failure."""
    resp = _extract_sse_response(raw)
    if resp is None:
        return None
    if as_chat_completion:
        resp = _responses_to_chat_completion(resp)
    body = json.dumps(resp).encode()
    return httpx.Response(200, headers={"content-type": "application/json"}, content=body)


def _ensure_valid_auth(
    auth: AuthTokens | None, store: TokenStore,
) -> AuthTokens:
    if auth is None:
        return authenticate()

    if auth.is_expired() and auth.refresh_token:
        try:
            raw = refresh_access_token(auth.refresh_token)
            auth = AuthTokens.from_response(
                raw, auth.refresh_token, auth.account_id,
            )
            store.save(auth)
        except Exception:
            log.debug("Token refresh failed, re-authenticating", exc_info=True)
            auth = authenticate(force=True)

    return auth


def _is_codex_path(path: str) -> bool:
    return "/chat/completions" in path or "/v1/responses" in path


def _user_wants_stream(request: httpx.Request) -> bool:
    try:
        return json.loads(request.content).get("stream") is True
    except Exception:
        return False


def _rewrite_request(request: httpx.Request, auth: AuthTokens) -> httpx.Request:
    path = request.url.path
    is_chat = "/chat/completions" in path

    if not (is_chat or "/v1/responses" in path):
        request.headers["authorization"] = f"Bearer {auth.access_token}"
        if auth.account_id:
            request.headers["chatgpt-account-id"] = auth.account_id
        return request

    p = CODEX_PARSED_URL
    request.url = request.url.copy_with(
        scheme=p.scheme, host=p.hostname, port=p.port, path=p.path,
    )

    try:
        body = json.loads(request.content)
        body = _chat_completions_to_responses(body) if is_chat else _normalize_responses_body(body)
        content = json.dumps(body).encode()
    except (json.JSONDecodeError, UnicodeDecodeError):
        content = request.content

    headers = {
        k: v for k, v in request.headers.items()
        if not k.lower().startswith("x-stainless")
    }
    headers.update({
        "host": p.hostname,
        "user-agent": USER_AGENT,
        "originator": "codex-auth",
        "authorization": f"Bearer {auth.access_token}",
        "content-length": str(len(content)),
    })
    if auth.account_id:
        headers["chatgpt-account-id"] = auth.account_id

    return httpx.Request(
        method=request.method, url=request.url,
        headers=headers, content=content,
    )


class CodexTransport(httpx.BaseTransport):
    def __init__(
        self,
        auth_tokens: AuthTokens | None = None,
        token_store: TokenStore | None = None,
        wrapped: httpx.BaseTransport | None = None,
    ):
        self._auth = auth_tokens
        self._store = token_store or TokenStore()
        self._wrapped = wrapped or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self._auth = _ensure_valid_auth(self._auth, self._store)
        path = request.url.path
        needs_buffer = _is_codex_path(path) and not _user_wants_stream(request)
        is_chat = "/chat/completions" in path

        response = self._wrapped.handle_request(_rewrite_request(request, self._auth))

        if needs_buffer and response.status_code == 200:
            buffered = _buffer_sse(response.read(), as_chat_completion=is_chat)
            if buffered is not None:
                return buffered

        return response

    def close(self) -> None:
        self._wrapped.close()


class AsyncCodexTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        auth_tokens: AuthTokens | None = None,
        token_store: TokenStore | None = None,
        wrapped: httpx.AsyncBaseTransport | None = None,
    ):
        self._auth = auth_tokens
        self._store = token_store or TokenStore()
        self._wrapped = wrapped or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._auth = _ensure_valid_auth(self._auth, self._store)
        path = request.url.path
        needs_buffer = _is_codex_path(path) and not _user_wants_stream(request)
        is_chat = "/chat/completions" in path

        response = await self._wrapped.handle_async_request(
            _rewrite_request(request, self._auth)
        )

        if needs_buffer and response.status_code == 200:
            buffered = _buffer_sse(await response.aread(), as_chat_completion=is_chat)
            if buffered is not None:
                return buffered

        return response

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def apply_patch() -> None:
    global _original_init, _original_async_init, _patched
    if _patched:
        return

    _original_init = openai.OpenAI.__init__
    _original_async_init = openai.AsyncOpenAI.__init__

    # Capture in locals — the globals get nulled by remove_patch()
    real_init = _original_init
    real_async_init = _original_async_init

    def patched_init(self, **kw):
        kw.setdefault("http_client", httpx.Client(transport=CodexTransport()))
        kw.setdefault("api_key", "codex-auth-dummy-key")
        real_init(self, **kw)

    def patched_async_init(self, **kw):
        kw.setdefault("http_client", httpx.AsyncClient(transport=AsyncCodexTransport()))
        kw.setdefault("api_key", "codex-auth-dummy-key")
        real_async_init(self, **kw)

    openai.OpenAI.__init__ = patched_init  # type: ignore[assignment]
    openai.AsyncOpenAI.__init__ = patched_async_init  # type: ignore[assignment]
    _patched = True


def remove_patch() -> None:
    global _original_init, _original_async_init, _patched
    if not _patched or _original_init is None:
        return

    openai.OpenAI.__init__ = _original_init  # type: ignore[assignment]
    if _original_async_init is not None:
        openai.AsyncOpenAI.__init__ = _original_async_init  # type: ignore[assignment]

    _original_init = _original_async_init = None
    _patched = False
