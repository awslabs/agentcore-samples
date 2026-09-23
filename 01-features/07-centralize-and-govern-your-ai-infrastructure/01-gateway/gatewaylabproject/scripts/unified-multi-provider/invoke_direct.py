"""Invoke OpenAI and Anthropic DIRECTLY (not through Bedrock), via the gateway.

The gateway can route to a provider's own API instead of Bedrock. The `openai`
and `anthropic` connector targets send traffic straight to api.openai.com /
api.anthropic.com, using the provider API key the gateway holds. Compare this
with invoke_native_sdk.py, where the `bedrock-mantle` target serves the same
model families from Amazon Bedrock.

The client code is identical to the Bedrock case; only the model string's target
prefix changes (openai/... and anthropic/... instead of bedrock-mantle/...).

Requires GATEWAY_URL and COGNITO_STACK_NAME, plus the openai/anthropic targets
created by deploy.py (which need OPENAI_API_KEY / ANTHROPIC_API_KEY at deploy).

Usage:
    uv run python scripts/unified-multi-provider/invoke_direct.py
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


def main():
    load_env(os.path.join(os.path.dirname(__file__), ".env"))

    gateway_url = get_required_env("GATEWAY_URL")
    cognito_stack = get_required_env("COGNITO_STACK_NAME")
    token = gateway_token(cognito_stack)
    openai_client, anthropic_client = build_inference_clients(gateway_url, token)

    print(f"Gateway: {inference_base_url(gateway_url, '/inference')}\n")

    # OpenAI SDK -> OpenAI directly (the `openai` connector target).
    print("OpenAI SDK -> openai/gpt-4o-mini  (direct to OpenAI)")
    try:
        resp = openai_client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": "Reply with one short sentence."}],
        )
        print(f"  {resp.choices[0].message.content}\n")
    except Exception as e:  # noqa: BLE001
        print(f"  Skipped ({type(e).__name__}): {e}\n")

    # Anthropic SDK -> Anthropic directly (the `anthropic` connector target).
    print("Anthropic SDK -> anthropic/claude-haiku-4-5  (direct to Anthropic)")
    try:
        msg = anthropic_client.messages.create(
            model="anthropic/claude-haiku-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": "Reply with one short sentence."}],
        )
        print(f"  {''.join(b.text for b in msg.content if b.type == 'text')}")
    except Exception as e:  # noqa: BLE001
        print(f"  Skipped ({type(e).__name__}): {e}")


if __name__ == "__main__":
    main()
