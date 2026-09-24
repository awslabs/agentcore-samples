# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Model provider selection — direct Bedrock vs Gateway inference target.

Two modes, controlled by INFERENCE_ROUTE:

  direct    (default) — Strands' BedrockModel calls Bedrock.InvokeModel over
                        the AWS SDK. This is the classic "InvokeModel per
                        agent" pattern and the baseline for comparison.

  gateway              — The agents talk to the AgentCore Gateway's
                        /inference endpoint instead. The Gateway holds one
                        shared credential to the model provider and is the
                        single point where guardrails, policy, and cost
                        attribution can be enforced for every team, so
                        multi-team workloads get one governed ingress rather
                        than each caller wiring those concerns separately.

The switch is a feature flag rather than a rewrite because the point of the
Gateway-as-LLM-gateway pattern is that clients do not have to change: the
same Strands Agent, the same tools, the same messages — just a different
model provider underneath. The demo can flip and prove that.

Two wire formats live behind the one endpoint, and which one a model speaks
is a property of the model, not a choice:

  /inference/v1/messages          Anthropic Messages API — Claude models.
                                  Requires an `anthropic_version` field;
                                  omitting it is a 400.
  /inference/v1/chat/completions  OpenAI Chat Completions — everything else
                                  (DeepSeek, Mistral, Llama, Nova, …).
                                  Claude models return 400 "does not support
                                  the '/v1/chat/completions' API" here.

So the provider is selected from the model id. Both surfaces stream over SSE
and both report token usage.

Auth is SigV4 in every case: the Gateway uses the AWS_IAM authorizer, so the
provider SDKs get a custom httpx client whose auth hook signs each request.
The SDKs still insist on an api_key being set, so a placeholder is passed and
the hook overwrites the Authorization header.
"""
from __future__ import annotations

import logging
import os

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from strands.models import BedrockModel

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# Version the Anthropic Messages API expects in the body. The Gateway forwards
# it upstream unchanged.
_ANTHROPIC_VERSION = "2023-06-01"

# Headers excluded from the SigV4 canonical request: hop-by-hop values, and
# any signature material from a previous attempt. Same set as the MCP client's
# signer in lib/gateway.py, kept in sync so both auth paths behave identically.
_UNSIGNED_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "authorization",
    "x-amz-date",
    "x-amz-security-token",
    "x-amz-content-sha256",
}


def _sign(request, session: boto3.Session, region: str) -> None:
    """SigV4-sign `request` in place, for either httpx client flavour."""
    creds = session.get_credentials()
    if creds is None:
        raise RuntimeError("No AWS credentials available for gateway inference")

    # The provider SDKs require *an* api_key and turn it into their own auth
    # header — x-api-key for Anthropic, Authorization for OpenAI. Ours is a
    # placeholder because the Gateway authorizes with SigV4, and sending both
    # is a hard 401 upstream: "request must not include both 'authorization'
    # and 'x-api-key' headers". Drop the SDK's header before signing, so the
    # only credential on the wire is the signature.
    request.headers.pop("x-api-key", None)

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _UNSIGNED_HEADERS
    }
    aws_request = AWSRequest(
        method=request.method,
        url=str(request.url),
        data=request.content,
        headers=headers,
    )
    SigV4Auth(
        creds.get_frozen_credentials(), "bedrock-agentcore", region
    ).add_auth(aws_request)

    for key, value in aws_request.headers.items():
        request.headers[key] = value


class _SigV4Auth(httpx.Auth):
    """Signs each request to the Gateway's /inference endpoint.

    Per-request rather than cached: the body differs on every call, and SigV4
    covers a hash of the payload.
    """

    requires_request_body = True

    def __init__(self, region: str) -> None:
        self._region = region
        # Hold the Session rather than frozen credentials so the container
        # role's short-lived credentials refresh on their own.
        self._session = boto3.Session()

    def auth_flow(self, request: httpx.Request):
        _sign(request, self._session, self._region)
        yield request


def inference_base_url(gateway_url: str, include_v1: bool) -> str:
    """Map a Gateway URL to the base each provider SDK expects.

    `gateway_url` ends in /mcp for the MCP transport; inference is a sibling
    path on the same host, so that suffix is trimmed rather than appended to.

    The `/v1` segment differs by SDK, and getting it wrong is a confusing
    *400 "Unsupported inference path: /v1/v1/messages"*:

      Anthropic  appends "/v1/messages" to base_url  -> base must end /inference
      OpenAI     appends "/chat/completions"          -> base must end /inference/v1

    Args:
        gateway_url: The Gateway's MCP URL, as Terraform reports it.
        include_v1: True for the OpenAI SDK, False for the Anthropic SDK.
    """
    base = gateway_url.rstrip("/")
    if base.endswith("/mcp"):
        base = base[: -len("/mcp")]
    return f"{base}/inference/v1" if include_v1 else f"{base}/inference"


def _is_anthropic(model_id: str) -> bool:
    """Whether `model_id` speaks the Anthropic Messages API.

    Matches on the vendor segment rather than the word "claude" anywhere, so a
    hypothetical third-party model with "claude" in its name is not misrouted.
    """
    tail = model_id.split("/")[-1]
    return tail.startswith("anthropic.") or tail.startswith("claude-")


def _build_gateway_model(model_id: str, region: str) -> object:
    """Construct a Strands model that routes through the Gateway.

    Provider SDKs are imported lazily so a `direct` deployment needs neither
    the openai nor the anthropic package at import time.
    """
    gateway_url = os.environ.get("GATEWAY_URL")
    if not gateway_url:
        raise ValueError(
            "GATEWAY_URL environment variable is required for gateway inference"
        )
    if _is_anthropic(model_id):
        from strands.models.anthropic import AnthropicModel  # noqa: PLC0415

        base_url = inference_base_url(gateway_url, include_v1=False)
        logger.info(
            "[INFERENCE] gateway → Anthropic Messages API at %s (model=%s)",
            base_url,
            model_id,
        )
        # AnthropicModel builds an AsyncAnthropic client, so the injected
        # transport must be async too.
        return AnthropicModel(
            client_args={
                "base_url": base_url,
                "api_key": "sigv4-signed-per-request",  # placeholder; see _SigV4Auth
                # The Gateway requires anthropic_version on Messages requests.
                # It has to travel as a header, not in `params`: Strands splats
                # params into messages.stream(), which rejects unknown kwargs
                # ("unexpected keyword argument 'anthropic_version'"). The SDK
                # merges default_headers into every request instead.
                "default_headers": {"anthropic-version": _ANTHROPIC_VERSION},
                "http_client": httpx.AsyncClient(
                    auth=_SigV4Auth(region), timeout=120.0
                ),
            },
            model_id=model_id,
            max_tokens=8192,
            # No temperature: the newest Claude models reject it outright
            # ("`temperature` is deprecated for this model"). The determinism
            # it bought on the direct route has to come from the prompts —
            # which already demand strict JSON — rather than from sampling
            # controls. The direct BedrockModel route still sets it, since
            # older inference-profile models accept it.
            params={},
        )

    import openai  # noqa: PLC0415
    from strands.models.openai import OpenAIModel  # noqa: PLC0415

    class _NoEmptyToolsModel(OpenAIModel):
        """OpenAIModel that omits `tools` from the request when it is empty.

        Strands' format_request always emits `"tools": [...]`, an empty list
        when the agent has no tools. DeepSeek's chat template treats the mere
        presence of the `tools` key — even `[]` — as license to call tools, and
        responds with its native tool-call tokens (`<｜tool▁calls▁begin｜>…`) as
        plain text instead of the answer. For a tool-less agent like the risk
        supervisor that means no verdict JSON at all, which then trips the
        ESCALATE fail-safe on every run. Verified by isolation: the identical
        request with the `tools` key removed returns clean JSON.

        Dropping only the *empty* list is safe — the specialists bind real
        tools, so their request keeps a populated `tools` array untouched.
        """

        def format_request(self, *args, **kwargs):  # type: ignore[override]
            request = super().format_request(*args, **kwargs)
            if not request.get("tools"):
                request.pop("tools", None)
            return request

    base_url = inference_base_url(gateway_url, include_v1=True)
    logger.info(
        "[INFERENCE] gateway → OpenAI Chat Completions at %s (model=%s)",
        base_url,
        model_id,
    )
    # Pass a pre-built client, NOT client_args. Given client_args, OpenAIModel
    # wraps each request in `async with openai.AsyncOpenAI(**client_args)`,
    # whose __aexit__ closes the injected http_client — so our SigV4 AsyncClient
    # dies after the first turn and the tool-use loop's second call fails with
    # "Cannot send a request, as the client has been closed." A client passed as
    # `client=` is reused across requests and never closed by the model (per its
    # own docstring), which is what a multi-turn agent needs. The api_key is a
    # placeholder; _SigV4Auth overwrites the Authorization header per request.
    client = openai.AsyncOpenAI(
        base_url=base_url,
        api_key="sigv4-signed-per-request",
        http_client=httpx.AsyncClient(auth=_SigV4Auth(region), timeout=120.0),
    )
    return _NoEmptyToolsModel(
        client=client,
        model_id=model_id,
        params={"temperature": 0.1},
    )


def build_model() -> object:
    """Return the model the orchestrator and specialists should use.

    Reads:
        INFERENCE_ROUTE — "direct" (Bedrock InvokeModel) or "gateway".
        MODEL_ID        — for "direct", a Bedrock model or inference-profile id.
        GATEWAY_MODEL_ID — for "gateway", the id as the Gateway advertises it
                          (e.g. "bedrock-mantle/anthropic.claude-sonnet-5").
                          The connector's catalog does not use Bedrock's
                          inference-profile ids, so the two routes need
                          different identifiers for the same intent.
        AWS_REGION      — for SigV4 when routing through the Gateway.

    Returns:
        A Strands Model. Callers do not care which — that is the point.
    """
    route = os.environ.get("INFERENCE_ROUTE", "direct").strip().lower()
    region = os.environ.get("AWS_REGION", "us-east-1")

    if route == "gateway":
        model_id = os.environ.get("GATEWAY_MODEL_ID", "").strip()
        if not model_id:
            raise ValueError(
                "GATEWAY_MODEL_ID is required when INFERENCE_ROUTE=gateway. "
                "List available ids with GET {gateway}/inference/v1/models."
            )
        return _build_gateway_model(model_id=model_id, region=region)

    if route != "direct":
        logger.warning(
            "Unknown INFERENCE_ROUTE=%r; falling back to direct Bedrock", route
        )
    model_id = os.environ.get("MODEL_ID", _DEFAULT_MODEL_ID)
    logger.info("[INFERENCE] calling Bedrock directly (model=%s)", model_id)
    return BedrockModel(model_id=model_id, temperature=0.1)


def current_route() -> str:
    """The route this runtime is configured for — surfaced to the console."""
    return os.environ.get("INFERENCE_ROUTE", "direct").strip().lower()


def current_model_id() -> str:
    """The model id actually in use, which differs per route."""
    if current_route() == "gateway":
        return os.environ.get("GATEWAY_MODEL_ID", "")
    return os.environ.get("MODEL_ID", _DEFAULT_MODEL_ID)
