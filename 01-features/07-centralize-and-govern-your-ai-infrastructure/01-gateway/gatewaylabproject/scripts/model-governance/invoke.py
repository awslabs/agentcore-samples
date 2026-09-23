"""Demo: model-based routing and governance across provider targets.

With the bedrock / openai / gemini provider targets from deploy.py, the gateway
routes each request by the `model` field:

- Unqualified (`gpt-oss-120b`): matched across all targets; exact beats glob.
- Qualified (`bedrock/openai.gpt-oss-120b`): forced to the named target.
- Collision: if several targets match, the gateway prefers the bedrock target;
  otherwise it returns 409 asking you to qualify the model id.

Governance: each target's `operations` allow-list governs what unqualified
routing can match. An unqualified model that no target advertises is not routed
("not found on any target"). Qualified requests (`target/model`) address a target
directly.

The "succeeds" calls use OpenAI-compatible models on /v1/chat/completions, which
run on the current stage. (Claude uses /v1/messages; see invoke_native_sdk.py in
the unified tutorial.)

Requires GATEWAY_URL and COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/model-governance/invoke.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from env_utils import get_required_env, load_env
from inference_demo import build_inference_clients, gateway_token, inference_base_url


def list_models(openai_client):
    print("\n" + "=" * 60)
    print("Models: aggregated catalog across all targets (GET /v1/models)")
    print("=" * 60)
    try:
        models = openai_client.models.list().data
        print(f"  {len(models)} models discovered:")
        for m in models:
            print(f"    {m.id}")
    except Exception as e:  # noqa: BLE001
        print(f"  Failed ({type(e).__name__}): {e}")


def try_call(openai_client, label, model):
    print("\n" + "=" * 60)
    print(f"{label}: model={model!r}")
    print("=" * 60)
    try:
        resp = openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with one short sentence."}],
        )
        print(f"  OK: {resp.choices[0].message.content}")
    except Exception as e:  # noqa: BLE001 - a rejection is an expected demo outcome
        print(f"  Rejected ({type(e).__name__}): {e}")


def main():
    load_env(os.path.join(os.path.dirname(__file__), ".env"))

    gateway_url = get_required_env("GATEWAY_URL")
    cognito_stack = get_required_env("COGNITO_STACK_NAME")

    token = gateway_token(cognito_stack)
    openai_client, _ = build_inference_clients(gateway_url, token)

    print(f"Inference endpoint: {inference_base_url(gateway_url, '/inference')}")

    # 0. List the aggregated model catalog across every READY target. A FAILED
    #    target contributes no models, so this also shows which targets are live.
    list_models(openai_client)

    # 1. Unqualified routing, model IN the allow-list: no "/", matched across
    #    targets. The bedrock target advertises openai.gpt-oss-*, and modelMapping
    #    lets the caller drop the prefix, so this routes to bedrock and succeeds.
    try_call(openai_client, "Unqualified, in allow-list", "gpt-oss-120b")

    # 2. Unqualified routing, model NOT in any allow-list: the operations
    #    allow-list governs what unqualified routing can match. deepseek.v3.2 is a
    #    real Bedrock model but no target advertises it, so routing finds nothing
    #    and the gateway returns "not found on any target".
    try_call(openai_client, "Unqualified, not in allow-list", "deepseek.v3.2")

    # 3. Qualified routing: the "bedrock/" prefix selects the bedrock target
    #    explicitly. Qualified requests bypass cross-target matching.
    try_call(
        openai_client, "Qualified routing (bedrock)", "bedrock/openai.gpt-oss-120b"
    )

    # 4. Qualified routing to the OpenAI target (if deployed). Returns
    #    "not found on any target" when the openai target was not created
    #    (no OPENAI_API_KEY at deploy time).
    try_call(openai_client, "Qualified routing (openai)", "openai/gpt-4o-mini")

    # 5. Qualified routing to the Gemini target (if deployed). Gemini has no
    #    built-in connector, so it is attached as a provider target advertising
    #    gemini-* on /v1/chat/completions. Returns "not found on any target" when
    #    the gemini target was not created (no GEMINI_API_KEY at deploy time).
    try_call(openai_client, "Qualified routing (gemini)", "google/gemini-2.5-flash")


if __name__ == "__main__":
    main()
