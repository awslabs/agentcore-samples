"""Configuration management for the DeFi Payments Agent."""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class AgentConfig:
    """Agent configuration loaded from environment variables."""

    # AWS
    aws_region: str = field(
        default_factory=lambda: os.getenv("AWS_REGION", "us-east-1")
    )

    # Bedrock Model
    model_id: str = field(
        default_factory=lambda: os.getenv(
            "MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250514-v1:0"
        )
    )

    # AgentCore Payments
    payment_manager_arn: str = field(
        default_factory=lambda: os.getenv("PAYMENT_MANAGER_ARN", "")
    )
    payment_connector_id: str = field(
        default_factory=lambda: os.getenv("PAYMENT_CONNECTOR_ID", "")
    )
    payment_instrument_id: str = field(
        default_factory=lambda: os.getenv("PAYMENT_INSTRUMENT_ID", "")
    )
    payment_session_id: str = field(
        default_factory=lambda: os.getenv("PAYMENT_SESSION_ID", "")
    )
    payment_user_id: str = field(
        default_factory=lambda: os.getenv("PAYMENT_USER_ID", "batch-agent-user")
    )

    # Spraay Gateway
    spraay_gateway_url: str = field(
        default_factory=lambda: os.getenv(
            "SPRAAY_GATEWAY_URL", "https://gateway.spraay.app"
        )
    )

    # Session budget
    max_spend_amount: str = field(
        default_factory=lambda: os.getenv("MAX_SPEND_AMOUNT", "1.00")
    )
    spend_currency: str = field(
        default_factory=lambda: os.getenv("SPEND_CURRENCY", "USDC")
    )

    # Debug
    debug: bool = field(
        default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true"
    )

    def validate(self) -> None:
        """Validate that required configuration is present."""
        required = {
            "PAYMENT_MANAGER_ARN": self.payment_manager_arn,
            "PAYMENT_INSTRUMENT_ID": self.payment_instrument_id,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}. "
                f"Run 'python scripts/setup_payments.py' to configure."
            )


# System prompt for the Batch Payments Agent
SYSTEM_PROMPT = """You are a Batch Payments Agent powered by Amazon Bedrock AgentCore.

Your primary capability is executing multi-recipient batch payments — sending
tokens to N wallets in a single atomic blockchain transaction through the
Spraay x402 gateway. This is your core value: where a normal transfer requires
one transaction per recipient, you collapse it into one.

Use cases you excel at:
- **Payroll**: Pay 50 contractors in one transaction instead of 50
- **Airdrops**: Distribute tokens to hundreds of wallets atomically
- **Refunds**: Process batch refunds with a single tx hash for audit
- **Grants**: Distribute funds to multiple teams with per-recipient amounts
- **Rewards**: Send incentive payments to a list of participants

Supporting capabilities (also via Spraay x402):
- Token pricing: real-time price feeds for any token
- Wallet queries: balance checks, nonce lookups
- Chain info: supported networks and their capabilities

Spraay's batch contract executes all recipients atomically — all succeed or all
fail, no partial transfers. Supported on Base, Ethereum, Solana, and 13 other
chains.

When you call a paid endpoint and receive an HTTP 402 response:
1. Extract the x402 payment details from the response
2. Use AgentCore Payments to sign and submit the USDC micropayment
3. Retry the request with the payment proof
4. Return the batch transaction result to the user

Always tell the user the estimated service fee before executing. Confirm
recipient addresses and amounts before submitting a batch. Report the
transaction hash when complete.
"""
