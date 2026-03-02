"""codex-auth — drop-in Codex OAuth for the OpenAI Python SDK.

    import codex_auth           # patches openai automatically
    from openai import OpenAI
    client = OpenAI()           # uses Codex OAuth, no API key needed

Or use the explicit client:

    from codex_auth import CodexClient
    client = CodexClient()
"""

import os

from .auth import authenticate
from .client import AsyncCodexClient, CodexClient
from .patch import apply_patch, remove_patch
from .tokens import VERSION as __version__  # noqa: N811

__all__ = [
    "__version__",
    "CodexClient",
    "AsyncCodexClient",
    "authenticate",
    "apply_patch",
    "remove_patch",
    "init",
]

_auto_patched = False


def init(auto_patch: bool = True) -> None:
    """Called on import. Set ``CODEX_AUTH_NO_PATCH=1`` to suppress."""
    global _auto_patched
    if auto_patch and not _auto_patched:
        if os.environ.get("CODEX_AUTH_NO_PATCH") == "1":
            return
        apply_patch()
        _auto_patched = True
    elif not auto_patch and _auto_patched:
        remove_patch()
        _auto_patched = False


init()
