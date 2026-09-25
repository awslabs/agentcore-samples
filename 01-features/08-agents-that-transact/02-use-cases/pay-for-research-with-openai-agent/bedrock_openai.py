"""Configure the OpenAI Agents SDK for OpenAI models on Amazon Bedrock."""

from __future__ import annotations

import os
from dataclasses import dataclass

from agents import set_default_openai_client, set_tracing_disabled
from aws_bedrock_token_generator import provide_token
from openai import AsyncOpenAI


@dataclass(frozen=True)
class BedrockOpenAIRuntime:
    model: str
    region: str
    include_web_search: bool


def configure_bedrock_openai() -> BedrockOpenAIRuntime:
    """Connect the Agents SDK to the Bedrock Responses API."""
    region = os.getenv("AWS_REGION", "us-east-1")
    token = provide_token(region=region)
    set_default_openai_client(
        AsyncOpenAI(
            api_key=token,
            base_url=f"https://bedrock-mantle.{region}.api.aws/openai/v1",
        ),
        use_for_tracing=False,
    )
    set_tracing_disabled(True)
    return BedrockOpenAIRuntime(
        model=os.getenv("BEDROCK_OPENAI_MODEL", "openai.gpt-5.5"),
        region=region,
        include_web_search=os.getenv("BEDROCK_OPENAI_WEB_SEARCH_ENABLED", "").lower() in {"1", "true", "yes", "on"},
    )
