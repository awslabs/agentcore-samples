"""Invoke gateway models through LiteLLM.

LiteLLM is a popular abstraction that gives one `completion()` call across many
providers. Because the gateway is OpenAI-compatible, you point LiteLLM at it with
the `openai/` custom-provider prefix plus `api_base` and `api_key`, then address
any gateway model by its id. This routes OpenAI and Qwen models (and other
OpenAI-compatible families) through Amazon Bedrock behind the single gateway
endpoint.

LiteLLM uses the OpenAI Chat Completions API, so models reached this way must
support /v1/chat/completions (the OpenAI-compatible families). Claude models that
require the Anthropic Messages API are invoked with the Anthropic SDK instead
(see invoke_native_sdk.py).

Requires GATEWAY_URL and COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/unified-multi-provider/invoke_litellm.py
"""

import os
import sys

import litellm

# Quiet LiteLLM's verbose provider banners so the demo output stays readable.
litellm.suppress_debug_info = True

# LiteLLM's async HTTP client leaves an event loop that Python 3.12 garbage
# collects at interpreter exit, printing a harmless "Invalid file descriptor"
# traceback after the results. Swallow just that one shutdown error.
_orig_unraisablehook = sys.unraisablehook


def _ignore_loop_shutdown_noise(unraisable):
    exc = unraisable.exc_value
    if isinstance(exc, ValueError) and "Invalid file descriptor" in str(exc):
        return
    _orig_unraisablehook(unraisable)


sys.unraisablehook = _ignore_loop_shutdown_noise

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from env_utils import get_required_env, load_env  # noqa: E402
from inference_demo import (  # noqa: E402
    build_inference_clients,
    gateway_token,
    inference_base_url,
)


def litellm_chat(base_url, token, gateway_model):
    """Call a gateway model through LiteLLM's unified completion() API.

    The `openai/` prefix tells LiteLLM to use its OpenAI-compatible client
    against our custom api_base; everything after it is the gateway model id.
    """
    resp = litellm.completion(
        model=f"openai/{gateway_model}",
        messages=[{"role": "user", "content": "Reply with one short sentence."}],
        api_base=base_url,
        api_key=token,
    )
    return resp.choices[0].message.content


def main():
    load_env(os.path.join(os.path.dirname(__file__), ".env"))

    gateway_url = get_required_env("GATEWAY_URL")
    cognito_stack = get_required_env("COGNITO_STACK_NAME")
    token = gateway_token(cognito_stack)
    base_url = inference_base_url(gateway_url, "/inference/v1")
    openai_client, _ = build_inference_clients(gateway_url, token)

    print(f"Gateway: {base_url}\n")

    # --- List models. LiteLLM's completion() does not list models, so use the
    # OpenAI SDK (the gateway is OpenAI-compatible) for the catalog. ---
    print("=" * 60)
    print("List models (OpenAI SDK against the gateway)")
    print("=" * 60)
    for model in openai_client.models.list().data:
        print(f"  {model.id}")

    # --- Invoke OpenAI-compatible model families through one LiteLLM call shape.
    # LiteLLM uses the Chat Completions API, so it covers the OpenAI-compatible
    # families. Claude models use the Anthropic Messages API instead (see
    # invoke_native_sdk.py), so they are not invoked through LiteLLM here. ---
    print("\n" + "=" * 60)
    print("litellm.completion() across model families (all via Bedrock)")
    print("=" * 60)
    for label, gateway_model in [
        ("OpenAI", "bedrock-mantle/openai.gpt-oss-120b"),
        ("Qwen", "bedrock-mantle/qwen.qwen3-32b"),
        ("DeepSeek", "bedrock-mantle/deepseek.v3.2"),
    ]:
        print(f"\n  {label} -> model='openai/{gateway_model}'")
        try:
            print(f"    {litellm_chat(base_url, token, gateway_model)}")
        except Exception as e:  # noqa: BLE001 - show unconfigured/unsupported models
            print(f"    Skipped ({type(e).__name__}): {e}")


if __name__ == "__main__":
    main()
