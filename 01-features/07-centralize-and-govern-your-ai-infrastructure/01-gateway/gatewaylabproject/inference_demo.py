"""Shared helpers for inference-target demo scripts.

Inference targets are invoked over the gateway's ``/inference`` path using the
OpenAI or Anthropic SDK (not MCP). The gateway inbound authorizer is Cognito
CUSTOM_JWT, so the gateway JWT is passed as the SDK ``api_key``. These helpers
fetch that token from Cognito and derive the inference base URL from the
gateway URL.

Usage from scripts:

    from inference_demo import gateway_token, build_inference_clients
    token = gateway_token(cognito_stack_name)
    openai_client, anthropic_client = build_inference_clients(gateway_url, token)
"""

from __future__ import annotations

from urllib.parse import urlparse

import anthropic
import boto3
import requests
from openai import OpenAI


def inference_base_url(gateway_url: str, suffix: str = "/inference/v1") -> str:
    """Derive the inference base URL from the gateway URL's scheme + host.

    Use ``suffix="/inference/v1"`` for the OpenAI SDK and ``"/inference"`` for
    the Anthropic SDK.
    """
    parsed = urlparse(gateway_url)
    return f"{parsed.scheme}://{parsed.netloc}{suffix}"


def build_inference_clients(gateway_url: str, token: str):
    """Return ``(openai_client, anthropic_client)`` pointed at the gateway.

    Both SDKs send the gateway JWT as ``Authorization: Bearer`` for inbound
    auth, but they take it differently:

    - OpenAI SDK uses the ``/inference/v1`` base and ``api_key`` (sent as
      ``Authorization: Bearer``).
    - Anthropic SDK uses the ``/inference`` base and ``auth_token`` (its
      ``api_key`` would be sent as ``x-api-key``, which the gateway does not
      accept for inbound auth).
    """
    openai_client = OpenAI(
        base_url=inference_base_url(gateway_url, "/inference/v1"), api_key=token
    )
    anthropic_client = anthropic.Anthropic(
        base_url=inference_base_url(gateway_url, "/inference"), auth_token=token
    )
    return openai_client, anthropic_client


# --- Streaming print helpers --------------------------------------------- #
# Each demo streams responses (stream=True) and prints tokens as they arrive.
# The consumption shape differs per API, so one helper per API keeps the demo
# scripts simple and consistent.


def stream_chat_completion(openai_client, model, prompt, indent="    "):
    """Stream an OpenAI Chat Completions response, printing tokens live."""
    stream = openai_client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )
    print(indent, end="")
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            print(delta, end="", flush=True)
    print()


def stream_response(openai_client, model, prompt, indent="    "):
    """Stream an OpenAI Responses API response, printing tokens live."""
    stream = openai_client.responses.create(model=model, input=prompt, stream=True)
    print(indent, end="")
    for event in stream:
        if event.type == "response.output_text.delta":
            print(event.delta, end="", flush=True)
    print()


def stream_message(anthropic_client, model, prompt, max_tokens=256, indent="    "):
    """Stream an Anthropic Messages API response, printing tokens live."""
    print(indent, end="")
    with anthropic_client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
    print()


def gateway_token(cognito_stack_name: str, region: str | None = None) -> str:
    """Fetch a Cognito client-credentials token for the GatewayClient.

    The returned JWT is used as the inbound gateway credential. Pass it as the
    OpenAI/Anthropic SDK ``api_key``.
    """
    region = region or boto3.Session().region_name
    cfn = boto3.client("cloudformation", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)

    outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=cognito_stack_name)["Stacks"][0][
            "Outputs"
        ]
    }
    client_id = outputs["GatewayClientId"]
    scope = outputs["GatewayScope"]
    client_secret = cognito.describe_user_pool_client(
        UserPoolId=outputs["UserPoolId"], ClientId=client_id
    )["UserPoolClient"]["ClientSecret"]
    token_endpoint = outputs["TokenEndpoint"]

    response = requests.post(
        token_endpoint,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]
