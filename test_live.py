"""Quick smoke test — requires a real ChatGPT Pro/Plus account."""

import codex_auth  # Must be imported before OpenAI for patching to work
from openai import OpenAI

client = OpenAI()

# Non-streaming (matches OpenAI quickstart docs)
response = client.responses.create(
    model="gpt-5.1-codex-mini",
    input="Write a one-sentence bedtime story about a unicorn.",
)
print("Non-streaming:", response.output_text)

# Streaming
with client.responses.create(
    model="gpt-5.1-codex-mini",
    instructions="Reply with just the number.",
    input=[{"role": "user", "content": "What is 1 + 1?"}],
    store=False,
    stream=True,
) as stream:
    for event in stream:
        if event.type == "response.completed":
            print("Streaming:", event.response.output_text)
            break
