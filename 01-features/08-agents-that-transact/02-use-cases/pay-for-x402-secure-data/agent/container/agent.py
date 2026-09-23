#!/usr/bin/env python3
"""Strands agent definition: x402-secure trust tools and AgentCore payments plugin wiring.

The ``main()`` one-shot CLI and interactive REPL at the bottom of this module
are **development/testing helpers only** — meant for exercising the agent with
your own synthetic prompts. They print the raw agent result to the console,
which can include prompt text and model output, so they must not be used with
production or third-party user data. Production traffic runs through the
AgentCore Runtime container (``main.py`` / ``http_app.py``), which returns
structured responses rather than printing to stdout.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv

if TYPE_CHECKING:
    from bedrock_agentcore.payments.integrations.strands import AgentCorePaymentsPlugin
    from strands import Agent

from payments import resolve_payment_plugin_config_values, resolve_region
from strands import tool
from x402_services import (
    DEFAULT_SERVICE_ID,
    TrustedX402ServiceGateway,
    supported_service_ids,
    supported_service_operations,
    use_request_trust_state,
)

load_dotenv()


def configure_aws_environment() -> dict[str, str | None]:
    aws_region = resolve_region()
    os.environ["AWS_REGION"] = aws_region
    os.environ["AWS_DEFAULT_REGION"] = aws_region

    aws_profile = os.environ.get("AWS_PROFILE") or None
    return {
        "aws_region": aws_region,
        "aws_profile": aws_profile,
    }


AWS_CONTEXT = configure_aws_environment()


def resolve_model_id() -> str:
    return os.environ.get("BEDROCK_MODEL_ID") or os.environ.get("MODEL_ID") or "us.anthropic.claude-sonnet-4-6"


DEFAULT_MODEL = resolve_model_id()
_X402_SERVICE_GATEWAY = None

SYSTEM_PROMPT = """You are an agent that uses registered paid x402 service endpoints. Every target x402 service call is protected by t54 x402-secure trust verification.

Available capabilities:
- Registered paid x402 service endpoints, including Heurist YahooFinanceAgent for market data
- t54 x402-secure endpoint scoring before any target x402 payment
- AgentCorePaymentsPlugin proof generation and x402 retry through per-invocation payment context

Guidelines:
- Before calling any target x402 service endpoint, call check_x402_endpoint_trust for that exact registered service_id or endpoint URL.
- Only use call_trusted_x402_service after trust passes. The tool enforces this guardrail in code and blocks missing, expired, low-score, scam, or URL-mismatched trust state before target payment.
- Never attempt to call a paid x402 endpoint directly. Use only registered service IDs and operations exposed by call_trusted_x402_service.
- Use service_id "heurist_yahoo_finance" for live symbol resolution, quotes, price history, technicals, fundamentals, analyst views, ETF data, news, or equity screens.
- Supported Heurist operations: resolve_symbol, quote_snapshot, price_history, technical_snapshot, options_expirations, options_chain, futures_snapshot, news_search, market_overview, company_fundamentals, analyst_snapshot, fund_snapshot, equity_screen.
- When trust is blocked, explain the blocked result and do not retry the target service call until trust passes.
- Summarize paid service responses for the user after a successful call_trusted_x402_service result.
"""


def get_x402_service_gateway() -> TrustedX402ServiceGateway:
    global _X402_SERVICE_GATEWAY
    if _X402_SERVICE_GATEWAY is None:
        _X402_SERVICE_GATEWAY = TrustedX402ServiceGateway()
    return _X402_SERVICE_GATEWAY


def create_agentcore_payments_plugin() -> AgentCorePaymentsPlugin | None:
    """Build the AgentCore payments plugin from the resolved payment config.

    Returns:
        A configured plugin, or None when payment config is unavailable (the agent
        then runs without paid x402 retry support).

    Raises:
        RuntimeError: If the bedrock-agentcore strands integration is not installed.
    """
    config_values = resolve_payment_plugin_config_values()
    if config_values is None:
        return None

    try:
        from bedrock_agentcore.payments.integrations.strands import (
            AgentCorePaymentsPlugin,
            AgentCorePaymentsPluginConfig,
        )
    except ImportError as exc:  # pragma: no cover - exercised when SDK deps are missing
        raise RuntimeError(
            "bedrock-agentcore[strands-agents] is required for plugin-native x402 payments. "
            "Install the pinned dependencies with: pip install -r agent/container/requirements.txt "
            "(or: pip install 'bedrock-agentcore[strands-agents]')."
        ) from exc

    return AgentCorePaymentsPlugin(
        config=AgentCorePaymentsPluginConfig(
            payment_manager_arn=config_values.payment_manager_arn,
            user_id=config_values.user_id,
            payment_instrument_id=config_values.payment_instrument_id,
            payment_session_id=config_values.payment_session_id,
            payment_connector_id=config_values.payment_connector_id,
            region=config_values.region,
            agent_name=config_values.agent_name,
        )
    )


@tool
def check_x402_endpoint_trust(
    service_id: str | None = DEFAULT_SERVICE_ID,
    url: str | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Check a registered or explicit x402 endpoint through t54 x402-secure.

    AgentCorePaymentsPlugin may retry this tool with x402 payment headers.
    """
    return get_x402_service_gateway().check_x402_endpoint_trust(
        service_id,
        url,
        headers=headers,
    )


@tool
def call_trusted_x402_service(
    service_id: str,
    operation: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Call a registered paid x402 service operation after x402-secure approval.

    AgentCorePaymentsPlugin may retry this tool with x402 payment headers.

    Supported service IDs:
    - heurist_yahoo_finance

    Supported heurist_yahoo_finance operations:
    - resolve_symbol
    - quote_snapshot
    - price_history
    - technical_snapshot
    - options_expirations
    - options_chain
    - futures_snapshot
    - news_search
    - market_overview
    - company_fundamentals
    - analyst_snapshot
    - fund_snapshot
    - equity_screen
    """
    return get_x402_service_gateway().call_trusted_x402_service(
        service_id,
        operation,
        payload or {},
        headers=headers,
    )


def create_agent() -> Agent:
    """Create the Strands agent with x402 tools and (when configured) the payments plugin.

    Returns:
        A Strands Agent wired with the trust-check and trusted-service-call tools.

    Raises:
        RuntimeError: If strands-agents is not installed.
    """
    try:
        from strands import Agent
    except ImportError as exc:  # pragma: no cover - exercised in the real runtime, not unit tests
        raise RuntimeError(
            "strands-agents must be installed before running this agent. "
            "Install the pinned dependencies with: pip install -r agent/container/requirements.txt "
            "(or: pip install strands-agents)."
        ) from exc

    plugins = []
    payments_plugin = create_agentcore_payments_plugin()
    if payments_plugin is not None:
        plugins.append(payments_plugin)

    return Agent(
        system_prompt=SYSTEM_PROMPT,
        plugins=plugins,
        tools=[
            check_x402_endpoint_trust,
            call_trusted_x402_service,
        ],
        model=DEFAULT_MODEL,
    )


def main() -> None:
    """Run the agent as an interactive CLI, or one-shot when a prompt is passed via argv.

    Development/testing helper only. This entry point prints raw agent output
    (prompt text + model responses) to stdout, so it is gated behind an
    explicit opt-in: set ``X402_ALLOW_DEV_CLI=1`` to run it. It refuses to run
    otherwise, which prevents it from ever executing in a deployed / production
    context (the AgentCore Runtime container runs ``main.py``, not this
    function, and never sets that variable).
    """
    if os.environ.get("X402_ALLOW_DEV_CLI") != "1":
        sys.exit(
            "Refusing to start the development CLI/REPL.\n"
            "This helper prints raw agent output (prompt + model text) to stdout and is\n"
            "intended for local development with SYNTHETIC prompts only — never\n"
            "production or third-party user data.\n"
            "To run it explicitly, set X402_ALLOW_DEV_CLI=1, e.g.:\n"
            '  X402_ALLOW_DEV_CLI=1 PYTHONPATH="$PWD/agent/container" \\\n'
            '      python agent/container/agent.py "<your synthetic test prompt>"'
        )

    print("=" * 60)
    print("  Pay for Secure Data (x402) Agent")
    print("=" * 60)
    print(f"  AWS region: {AWS_CONTEXT['aws_region']}")
    print(f"  AWS profile: {AWS_CONTEXT['aws_profile'] or 'default credential chain'}")
    print(f"  Model     : {DEFAULT_MODEL}")
    print(f"  x402 services: {', '.join(supported_service_ids())}")
    print(f"  {DEFAULT_SERVICE_ID} operations: {', '.join(supported_service_operations())}")
    print("=" * 60)
    print()

    agent = create_agent()

    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        print(f"Prompt: {prompt}\n")
        with use_request_trust_state():
            result = agent(prompt)
        print(f"\n{'=' * 60}")
        print("Agent response:")
        # Dev/testing CLI only — prints the raw result for synthetic prompts.
        # Do not use with production user data (see module docstring).
        print(result)
        return

    print("Enter prompts (type 'quit' to exit):\n")
    while True:
        try:
            prompt = input("You: ").strip()
        except EOFError:
            print()
            break
        if prompt.lower() in {"quit", "exit"}:
            break
        if not prompt:
            continue
        with use_request_trust_state():
            result = agent(prompt)
        # Dev/testing REPL only — prints the raw result for synthetic prompts.
        # Do not use with production user data (see module docstring).
        print(f"\nAgent: {result}\n")


if __name__ == "__main__":
    main()
