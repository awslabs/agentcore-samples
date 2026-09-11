"""Build a single OpenAI Agents SDK agent that can pay an x402 endpoint."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agents import Agent, OpenAIResponsesModel, Runner, function_tool, set_tracing_disabled
from aws_bedrock_token_generator import provide_token
from bedrock_agentcore.payments import PaymentManager
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai_x402_tool import build_x402_fetch

SOURCE_DIR = Path(__file__).resolve().parent
SHARED_ENV = SOURCE_DIR.parent / ".env"
DEFAULT_MODEL_REGION = "us-east-1"
DEFAULT_MODEL_ID = "openai.gpt-5.5"

AGENT_INSTRUCTIONS = """You are a payment-enabled research assistant.

When the user asks to access a paid endpoint, use the x402_fetch tool directly.
Summarize the returned data and report whether payment succeeded.
If the tool returns an error or payment failure, report that clearly.
Do not claim a payment or retrieved data succeeded unless the tool result confirms it.
Do not attempt alternate trial, walletless, or workaround URLs from an endpoint response.
If payment_made is null, the payment outcome is unknown; report that and do not retry.
Do not retry a failed payment or content request automatically.
"""


@dataclass(frozen=True)
class TutorialConfig:
    """Configuration produced by Tutorial 00 plus local run settings."""

    payment_manager_arn: str
    instrument_id: str
    user_id: str
    region: str
    model_region: str
    model_id: str
    paid_url: str
    session_budget: str


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing {name}. Complete Tutorial 00 first.")
    return value


def payment_region(payment_manager_arn: str) -> str:
    """Return the AWS region encoded in a payment manager ARN."""
    arn_parts = payment_manager_arn.split(":")
    if len(arn_parts) < 6 or arn_parts[0] != "arn" or not arn_parts[3]:
        raise ValueError("PAYMENT_MANAGER_ARN is not a valid AWS ARN")
    return arn_parts[3]


def load_config() -> TutorialConfig:
    """Load the shared Tutorial 00 resource identifiers."""
    load_dotenv(SHARED_ENV)
    payment_manager_arn = _required_env("PAYMENT_MANAGER_ARN")
    return TutorialConfig(
        payment_manager_arn=payment_manager_arn,
        instrument_id=_required_env("INSTRUMENT_ID"),
        user_id=_required_env("USER_ID"),
        region=payment_region(payment_manager_arn),
        model_region=os.getenv("BEDROCK_OPENAI_MODEL_REGION", DEFAULT_MODEL_REGION),
        model_id=os.getenv("BEDROCK_OPENAI_MODEL_ID", DEFAULT_MODEL_ID),
        paid_url=os.getenv(
            "PAID_URL",
            "https://x402-test.genesisblock.ai/api/market-news",
        ),
        session_budget=os.getenv("PAYMENT_SESSION_BUDGET", "1.00"),
    )


def build_model(
    model_region: str = DEFAULT_MODEL_REGION,
    model_id: str = DEFAULT_MODEL_ID,
) -> OpenAIResponsesModel:
    """Configure the OpenAI Agents SDK for GPT-5.5 on Amazon Bedrock."""
    # Bedrock authentication does not configure the OpenAI trace exporter.
    set_tracing_disabled(True)
    client = AsyncOpenAI(
        base_url=f"https://bedrock-mantle.{model_region}.api.aws/openai/v1",
        api_key=provide_token(region=model_region),
    )
    return OpenAIResponsesModel(model=model_id, openai_client=client)


def create_payment_session(
    config: TutorialConfig,
) -> tuple[PaymentManager, str]:
    """Create one short-lived, budget-bounded payment session."""
    manager = PaymentManager(
        payment_manager_arn=config.payment_manager_arn,
        region_name=config.region,
    )
    session = manager.create_payment_session(
        user_id=config.user_id,
        limits={
            "maxSpendAmount": {
                "value": config.session_budget,
                "currency": "USD",
            }
        },
        expiry_time_in_minutes=60,
        client_token=str(uuid.uuid4()),
    )
    return manager, session["paymentSessionId"]


def load_x402_fetch(
    config: TutorialConfig,
    payment_session_id: str,
) -> Callable[..., str]:
    """Load the generic x402 tool after supplying its payment configuration."""
    return build_x402_fetch(
        payment_manager_arn=config.payment_manager_arn,
        payment_instrument_id=config.instrument_id,
        payment_session_id=payment_session_id,
        user_id=config.user_id,
        region=config.region,
    )


def build_agent(
    model: OpenAIResponsesModel,
    x402_fetch: Callable[..., str],
) -> Agent:
    """Build one payment-enabled OpenAI agent."""

    payment_tool = function_tool(x402_fetch)

    openai_agent = Agent(
        name="OpenAI Payment Agent",
        instructions=AGENT_INSTRUCTIONS,
        model=model,
        tools=[payment_tool],
    )
    print(f"Created OpenAI agent with name: {openai_agent.name}")
    return openai_agent


def run_agent(agent: Agent, prompt: str) -> str:
    """Run the agent and return its final output."""

    return Runner.run_sync(agent, prompt).final_output


def run_local() -> None:
    """Run one paid request directly on the local machine."""
    config = load_config()
    _manager, payment_session_id = create_payment_session(config)
    x402_fetch = load_x402_fetch(
        config,
        payment_session_id,
    )
    agent = build_agent(
        build_model(config.model_region, config.model_id),
        x402_fetch,
    )
    prompt = f"Access this paid endpoint and summarize the result: {config.paid_url}. Report whether payment succeeded."
    print(run_agent(agent, prompt))


def main() -> None:
    """Run one paid request directly on the local machine."""
    run_local()


if __name__ == "__main__":
    main()
