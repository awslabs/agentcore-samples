"""Batch Payments Agent — main entry point.

A Strands agent that uses Amazon Bedrock AgentCore Payments to autonomously
execute multi-recipient batch payments through the Spraay x402 gateway.
Pay N wallets in one atomic transaction instead of N separate transactions.

Usage:
    # Local interactive mode
    python -m agent.main

    # Single prompt
    python -m agent.main --prompt "Send 0.001 ETH to 3 wallets on Base"

    # Deploy to AgentCore Runtime
    agentcore deploy
"""

import argparse
import logging
import sys

from strands import Agent
from strands.models import BedrockModel

from agent.config import AgentConfig, SYSTEM_PROMPT
from agent.tools import (
    # Primary — batch payments
    batch_transfer,
    batch_transfer_with_payment,
    estimate_batch_cost,
    # Supporting — discovery, pricing, chains
    discover_spraay_services,
    request_spraay_endpoint,
    request_spraay_endpoint_with_payment,
    get_supported_chains,
    estimate_spraay_cost,
)

logger = logging.getLogger(__name__)


def create_agent(config: AgentConfig | None = None) -> Agent:
    """Create and configure the DeFi Payments Agent.

    Args:
        config: Agent configuration. If None, loads from environment.

    Returns:
        Configured Strands Agent with AgentCore Payments plugin.
    """
    if config is None:
        config = AgentConfig()
    config.validate()

    # Import AgentCore Payments plugin
    from bedrock_agentcore.payments.integrations.strands.plugin import (
        AgentCorePaymentsPlugin,
    )
    from bedrock_agentcore.payments.integrations.config import (
        AgentCorePaymentsPluginConfig,
    )

    # Configure the payments plugin
    payments_config = AgentCorePaymentsPluginConfig(
        payment_manager_arn=config.payment_manager_arn,
        user_id=config.payment_user_id,
        payment_instrument_id=config.payment_instrument_id,
        payment_session_id=config.payment_session_id,
        region=config.aws_region,
    )
    payments_plugin = AgentCorePaymentsPlugin(payments_config)

    # Configure the Bedrock model
    model = BedrockModel(
        model_id=config.model_id,
        region=config.aws_region,
    )

    # Create the agent with batch payment tools + supporting tools
    agent = Agent(
        model=model,
        tools=[
            # Primary — batch payments
            batch_transfer,
            batch_transfer_with_payment,
            estimate_batch_cost,
            # Supporting — discovery, pricing, chains
            discover_spraay_services,
            request_spraay_endpoint,
            request_spraay_endpoint_with_payment,
            get_supported_chains,
            estimate_spraay_cost,
        ],
        plugins=[payments_plugin],
        system_prompt=SYSTEM_PROMPT,
    )

    logger.info(
        "Batch Payments Agent created — model=%s, region=%s, gateway=%s",
        config.model_id,
        config.aws_region,
        config.spraay_gateway_url,
    )

    return agent


def run_interactive(agent: Agent) -> None:
    """Run the agent in interactive mode."""
    print("\n💧 Batch Payments Agent (Spraay x402 + AgentCore Payments)")
    print("=" * 60)
    print("Pay N recipients in one transaction. Type 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                print("\nGoodbye!")
                break

            response = agent(user_input)
            print(f"\nAgent: {response}\n")

        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!")
            break
        except Exception as e:
            logger.error("Agent error: %s", e, exc_info=True)
            print(f"\nError: {e}\n")


def main() -> None:
    """Entry point for the DeFi Payments Agent."""
    parser = argparse.ArgumentParser(
        description="Batch Payments Agent — AgentCore Payments + Spraay x402"
    )
    parser.add_argument(
        "--prompt",
        type=str,
        help="Single prompt to run (non-interactive mode)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Create the agent
    config = AgentConfig()
    if args.debug:
        config.debug = True

    agent = create_agent(config)

    # Run in single-prompt or interactive mode
    if args.prompt:
        response = agent(args.prompt)
        print(response)
    else:
        run_interactive(agent)


# AgentCore Runtime entry point
def handler(event: dict, context: dict) -> dict:
    """AWS Lambda / AgentCore Runtime handler.

    This is the entry point when deployed to AgentCore Runtime.
    """
    config = AgentConfig()
    agent = create_agent(config)

    prompt = event.get("prompt", event.get("input", ""))
    if not prompt:
        return {"error": "No prompt provided"}

    response = agent(prompt)
    return {"response": str(response)}


if __name__ == "__main__":
    main()
