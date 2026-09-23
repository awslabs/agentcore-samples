"""Demo: streaming through LiteLLM.

Same as invoke_litellm.py, but with ``stream=True`` so LiteLLM yields chunks as
they arrive. Iterate the stream and read ``chunk.choices[0].delta.content``.
Covers the OpenAI-compatible families (OpenAI, Qwen, DeepSeek, ...) served by
Bedrock behind the gateway. Claude uses the Anthropic Messages API instead (see
invoke_streaming.py).

Requires GATEWAY_URL and COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/unified-multi-provider/invoke_litellm_streaming.py
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
from inference_demo import gateway_token, inference_base_url  # noqa: E402


def litellm_stream(base_url, token, gateway_model, indent="    "):
    """Stream a gateway model through LiteLLM, printing tokens as they arrive.

    The `openai/` prefix tells LiteLLM to use its OpenAI-compatible client
    against our custom api_base; everything after it is the gateway model id.
    """
    stream = litellm.completion(
        model=f"openai/{gateway_model}",
        messages=[{"role": "user", "content": "Count from 1 to 5, one per line."}],
        api_base=base_url,
        api_key=token,
        stream=True,
    )
    print(indent, end="")
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            print(delta, end="", flush=True)
    print()


def main():
    load_env(os.path.join(os.path.dirname(__file__), ".env"))

    gateway_url = get_required_env("GATEWAY_URL")
    cognito_stack = get_required_env("COGNITO_STACK_NAME")
    token = gateway_token(cognito_stack)
    base_url = inference_base_url(gateway_url, "/inference/v1")

    print(f"Gateway: {base_url}\n")

    print("=" * 60)
    print("litellm.completion(stream=True) across model families (all via Bedrock)")
    print("=" * 60)
    for label, gateway_model in [
        ("OpenAI", "bedrock-mantle/openai.gpt-oss-120b"),
        ("Qwen", "bedrock-mantle/qwen.qwen3-32b"),
        ("DeepSeek", "bedrock-mantle/deepseek.v3.2"),
    ]:
        print(f"\n  {label} -> model='openai/{gateway_model}'")
        try:
            litellm_stream(base_url, token, gateway_model)
        except Exception as e:  # noqa: BLE001 - show unconfigured/unsupported models
            print(f"    Skipped ({type(e).__name__}): {e}")


if __name__ == "__main__":
    main()
