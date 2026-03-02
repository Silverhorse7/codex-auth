# codex-auth

Drop-in OAuth for the OpenAI Python SDK — use the ChatGPT Codex API with your Pro/Plus account instead of an API key. Obviously this is for personal usage users, not for production or so.

## Install

```bash
pip install codex-auth
```

## Usage

```python
import codex_auth

from openai import OpenAI
client = OpenAI()  # no API key needed

response = client.responses.create(
    model="gpt-5.1-codex-mini",
    input="Write a one-sentence bedtime story about a unicorn.",
)
print(response.output_text)
```

A browser window opens on first run for OAuth. Tokens are cached in
`~/.codex-auth/auth.json` and refreshed automatically.

Both streaming and non-streaming calls work — the library handles the
Codex endpoint's streaming requirement transparently.

### Streaming

```python
stream = client.responses.create(
    model="gpt-5.1-codex-mini",
    input="Write a hello-world in Rust.",
    stream=True,
)
for event in stream:
    if event.type == "response.completed":
        print(event.response.output_text)
```

### `chat.completions` compatibility

Existing code using `chat.completions` works too — requests are converted
to the Responses API format automatically:

```python
response = client.chat.completions.create(
    model="gpt-5.1-codex-mini",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

### Explicit client

If you prefer not to monkey-patch:

```python
from codex_auth import CodexClient

client = CodexClient()            # browser / device auth
client = CodexClient(token="…")   # existing token
```

Async variant:

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
3. Buffer SSE responses for non-streaming callers
4. Inject OAuth bearer tokens and refresh them transparently

Browser-based PKCE auth is used on desktop; device-code flow on headless/SSH.

## Token storage

`~/.codex-auth/auth.json` (mode `0600`). Override with `CODEX_AUTH_TOKEN` env var.

