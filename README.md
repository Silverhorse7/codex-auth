# codex-auth

Drop-in OAuth for the OpenAI Python SDK — use the ChatGPT Codex API with your Pro/Plus account instead of an API key.

## Install

```bash
pip install codex-auth
```

## Usage

### Monkey-patch (one liner)

```python
import codex_auth

from openai import OpenAI
client = OpenAI()  # no API key needed

stream = client.responses.create(
    model="gpt-5.1-codex-mini",
    input="Write a hello-world in Rust.",
    stream=True,
)
for event in stream:
    if event.type == "response.completed":
        print(event.response.output_text)
```

A browser window opens on first run for OAuth. Tokens are cached in
`~/.codex-auth/auth.json` and refreshed automatically.

### Explicit client

```python
from codex_auth import CodexClient

client = CodexClient()            # browser / device auth
client = CodexClient(token="…")   # existing token
```

### Async

```python
from codex_auth import AsyncCodexClient
client = AsyncCodexClient()
```

### Disable auto-patch

```bash
export CODEX_AUTH_NO_PATCH=1
```

Or in code:

```python
import codex_auth
codex_auth.init(auto_patch=False)
```

## How it works

A custom [httpx transport](https://www.python-httpx.org/advanced/transports/) intercepts OpenAI SDK requests to:

1. Rewrite URLs to the Codex backend (`chatgpt.com/backend-api/codex/responses`)
2. Convert `chat.completions` payloads to the Responses API format
3. Inject OAuth bearer tokens and Codex-specific headers
4. Refresh tokens transparently

Browser-based PKCE auth is used on desktop; device-code flow on headless/SSH.

## Token storage

`~/.codex-auth/auth.json` (mode `0600`). Override with `CODEX_AUTH_TOKEN` env var.

## License

MIT
