"""Demo: one gateway endpoint, many providers, four inference APIs.

The gateway exposes the model providers behind a single `/inference` path. Which
API you call depends on what you want to do and which model family you target:

  PART 1  GET  /inference/v1/models            Discover the aggregated catalog.
  PART 2  POST /inference/v1/chat/completions  Stateless chat (OpenAI-compatible
                                               families: openai.*, mistral.*,
                                               qwen.*, deepseek.*, gemma, ...).
  PART 3  POST /inference/v1/responses         Stateful / agentic (OpenAI
                                               Responses API; built-in tools,
                                               conversation state).
  PART 4  POST /inference/v1/messages          Anthropic-native Messages API
                                               (Claude models: anthropic.*).

Inbound auth is Cognito CUSTOM_JWT: the gateway JWT is passed as the SDK
credential. The OpenAI SDK sends it as `Authorization: Bearer` via `api_key`;
the Anthropic SDK needs `auth_token` (its `api_key` goes to `x-api-key`, which
the gateway does not accept for inbound auth). Provider API keys never leave the
gateway.

Requires GATEWAY_URL and COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/unified-multi-provider/invoke.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from env_utils import get_required_env, load_env
from inference_demo import (
    build_inference_clients,
    gateway_token,
    inference_base_url,
)


def section(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def main():
    load_env(os.path.join(os.path.dirname(__file__), ".env"))

    gateway_url = get_required_env("GATEWAY_URL")
    cognito_stack = get_required_env("COGNITO_STACK_NAME")

    # The inbound credential is the Cognito gateway JWT (not a provider key).
    token = gateway_token(cognito_stack)
    openai_client, anthropic_client = build_inference_clients(gateway_url, token)

    print(f"Inference endpoint: {inference_base_url(gateway_url, '/inference')}")

    # ------------------------------------------------------------------ #
    # PART 1: Models API  (GET /inference/v1/models)
    # Discover every model across all configured targets. The catalog is
    # aggregated and each id is prefixed with its target name.
    # ------------------------------------------------------------------ #
    section("PART 1: GET /inference/v1/models  (aggregated catalog)")
    try:
        for model in openai_client.models.list().data:
            print(f"  {model.id}")
    except Exception as e:  # noqa: BLE001
        print(f"  Failed: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------ #
    # PART 2: Chat Completions  (POST /inference/v1/chat/completions)
    # Stateless multi-turn chat. You send the full message list each time.
    # Used by OpenAI-compatible model families. Switch providers by changing
    # only the model string.
    # ------------------------------------------------------------------ #
    section("PART 2: POST /inference/v1/chat/completions  (stateless chat)")
    for label, model in [
        ("Bedrock (OpenAI OSS)", "bedrock-mantle/openai.gpt-oss-120b"),
        ("OpenAI", "openai/gpt-4o-mini"),
    ]:
        print(f"\n  {label} -> model={model!r}")
        try:
            resp = openai_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": "Reply with one short sentence."}
                ],
            )
            print(f"    {resp.choices[0].message.content}")
        except Exception as e:  # noqa: BLE001 - show unconfigured/unsupported providers
            print(f"    Skipped ({type(e).__name__}): {e}")

    # ------------------------------------------------------------------ #
    # PART 3: Responses API  (POST /inference/v1/responses)
    # OpenAI's stateful, agentic API: built-in tools and server-side
    # conversation state. Same OpenAI SDK, different method (responses.create).
    # ------------------------------------------------------------------ #
    section("PART 3: POST /inference/v1/responses  (stateful / agentic)")
    model = "bedrock-mantle/openai.gpt-oss-120b"
    print(f"\n  Bedrock (OpenAI OSS) -> model={model!r}")
    try:
        resp = openai_client.responses.create(
            model=model, input="Reply with one short sentence."
        )
        print(f"    {resp.output_text}")
    except Exception as e:  # noqa: BLE001
        print(f"    Skipped ({type(e).__name__}): {e}")

    # ------------------------------------------------------------------ #
    # PART 4: Messages API  (POST /inference/v1/messages)
    # Anthropic-native format for Claude models. Uses the Anthropic SDK; note
    # auth_token (not api_key) so the JWT lands in Authorization: Bearer.
    # ------------------------------------------------------------------ #
    section("PART 4: POST /inference/v1/messages  (Anthropic-native)")
    model = "bedrock-mantle/anthropic.claude-haiku-4-5"
    print(f"\n  Bedrock (Claude) -> model={model!r}")
    try:
        resp = anthropic_client.messages.create(
            model=model,
            max_tokens=256,
            messages=[{"role": "user", "content": "Reply with one short sentence."}],
        )
        print(f"    {''.join(b.text for b in resp.content if b.type == 'text')}")
    except Exception as e:  # noqa: BLE001
        print(f"    Skipped ({type(e).__name__}): {e}")


if __name__ == "__main__":
    main()
