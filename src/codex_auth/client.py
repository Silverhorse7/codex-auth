from __future__ import annotations

import httpx
import openai

from .auth import authenticate
from .patch import AsyncCodexTransport, CodexTransport
from .tokens import AuthTokens, TokenStore


class CodexClient(openai.OpenAI):
    """``openai.OpenAI`` subclass that authenticates via Codex OAuth."""

    def __init__(self, *, token: str | None = None, **kwargs: object) -> None:
        store = TokenStore()
        auth = AuthTokens(access_token=token) if token else authenticate()

        kwargs.setdefault(
            "http_client",
            httpx.Client(transport=CodexTransport(auth_tokens=auth, token_store=store)),
        )
        kwargs.setdefault("api_key", "codex-auth-dummy-key")
        super().__init__(**kwargs)


class AsyncCodexClient(openai.AsyncOpenAI):
    """Async variant of :class:`CodexClient`."""

    def __init__(self, *, token: str | None = None, **kwargs: object) -> None:
        store = TokenStore()
        auth = AuthTokens(access_token=token) if token else authenticate()

        kwargs.setdefault(
            "http_client",
            httpx.AsyncClient(transport=AsyncCodexTransport(auth_tokens=auth, token_store=store)),
        )
        kwargs.setdefault("api_key", "codex-auth-dummy-key")
        super().__init__(**kwargs)
