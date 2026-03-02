"""Quick smoke test — requires a real ChatGPT Pro/Plus account."""


def main() -> None:
    import codex_auth  # noqa: F401
    from openai import OpenAI

    client = OpenAI()
    with client.responses.create(
        model="gpt-5.1-codex-mini",
        instructions="Reply with just the story, nothing else.",
        input=[{"role": "user", "content": "Give me a good night story about a unicorn."}],
        store=False,
        stream=True,
    ) as stream:
        for event in stream:
            if event.type == "response.completed":
                print(event.response.output_text)
                break


if __name__ == "__main__":
    main()
