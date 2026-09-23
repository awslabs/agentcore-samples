"""Demo: streaming responses through the gateway.

Same four inference APIs as invoke.py, but with ``stream=True`` so tokens print
as they arrive instead of waiting for the full response. Each API exposes a
different streaming shape, handled by the helpers in inference_demo.py:

  PART 1  GET  /inference/v1/models            Discover the aggregated catalog.
  PART 2  POST /inference/v1/chat/completions  Stream chat deltas.
  PART 3  POST /inference/v1/responses         Stream Responses API text events.
  PART 4  POST /inference/v1/messages          Stream Anthropic Messages text.

Inbound auth is the Cognito gateway JWT. See invoke.py for the non-streaming
version and a fuller explanation of the APIs.

Requires GATEWAY_URL and COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/unified-multi-provider/invoke_streaming.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from env_utils import get_required_env, load_env
from inference_demo import (
    build_inference_clients,
    gateway_token,
    inference_base_url,
    stream_chat_completion,
    stream_message,
    stream_response,
)

PROMPT = "Count from 1 to 5, one number per line."


def section(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def main():
    load_env(os.path.join(os.path.dirname(__file__), ".env"))

    gateway_url = get_required_env("GATEWAY_URL")
    cognito_stack = get_required_env("COGNITO_STACK_NAME")

    token = gateway_token(cognito_stack)
    openai_client, anthropic_client = build_inference_clients(gateway_url, token)

    print(f"Inference endpoint: {inference_base_url(gateway_url, '/inference')}")

    # PART 1: Models (discovery; not a streaming API)
    section("PART 1: GET /inference/v1/models  (aggregated catalog)")
    try:
        for model in openai_client.models.list().data:
            print(f"  {model.id}")
    except Exception as e:  # noqa: BLE001
        print(f"  Failed: {type(e).__name__}: {e}")

    # PART 2: Chat Completions streaming (chunk.choices[0].delta.content)
    section("PART 2: POST /inference/v1/chat/completions  (streaming)")
    model = "bedrock-mantle/openai.gpt-oss-120b"
    print(f"\n  Bedrock (OpenAI OSS) -> model={model!r}")
    try:
        stream_chat_completion(openai_client, model, PROMPT)
    except Exception as e:  # noqa: BLE001
        print(f"    Skipped ({type(e).__name__}): {e}")

    # PART 3: Responses streaming (response.output_text.delta events)
    section("PART 3: POST /inference/v1/responses  (streaming)")
    print(f"\n  Bedrock (OpenAI OSS) -> model={model!r}")
    try:
        stream_response(openai_client, model, PROMPT)
    except Exception as e:  # noqa: BLE001
        print(f"    Skipped ({type(e).__name__}): {e}")

    # PART 4: Messages streaming (Anthropic stream.text_stream)
    section("PART 4: POST /inference/v1/messages  (streaming)")
    claude = "bedrock-mantle/anthropic.claude-haiku-4-5"
    print(f"\n  Bedrock (Claude) -> model={claude!r}")
    try:
        stream_message(anthropic_client, claude, PROMPT)
    except Exception as e:  # noqa: BLE001
        print(f"    Skipped ({type(e).__name__}): {e}")


if __name__ == "__main__":
    main()
