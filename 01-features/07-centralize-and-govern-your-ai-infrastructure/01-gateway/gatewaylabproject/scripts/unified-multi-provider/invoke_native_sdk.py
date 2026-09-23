"""Invoke models through the gateway using each provider's NATIVE SDK.

Shows that you keep using the OpenAI SDK and the Anthropic SDK you already know,
just pointed at the gateway. The gateway routes by the model string, so the same
OpenAI client reaches an OpenAI model on Bedrock (`bedrock-mantle/openai.*`) or a
GPT model directly on OpenAI (`openai/*`); the Anthropic client reaches Claude.

Inbound auth is the Cognito gateway JWT. The OpenAI SDK takes it as `api_key`
(sent as Authorization: Bearer); the Anthropic SDK takes it as `auth_token`
(its `api_key` would be sent as x-api-key, which the gateway does not accept).

Requires GATEWAY_URL and COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/unified-multi-provider/invoke_native_sdk.py
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

    # OpenAI SDK -> an OpenAI model hosted on Bedrock (bedrock-mantle target).
    print("OpenAI SDK -> bedrock-mantle/openai.gpt-oss-120b")
    resp = openai_client.chat.completions.create(
        model="bedrock-mantle/openai.gpt-oss-120b",
        messages=[{"role": "user", "content": "Reply with one short sentence."}],
    )
    print(f"  {resp.choices[0].message.content}\n")

    # Anthropic SDK -> a Claude model hosted on Bedrock (Messages API).
    print("Anthropic SDK -> bedrock-mantle/anthropic.claude-haiku-4-5")
    try:
        msg = anthropic_client.messages.create(
            model="bedrock-mantle/anthropic.claude-haiku-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": "Reply with one short sentence."}],
        )
        print(f"  {''.join(b.text for b in msg.content if b.type == 'text')}")
    except Exception as e:  # noqa: BLE001
        print(f"  Skipped ({type(e).__name__}): {e}")


if __name__ == "__main__":
    main()
