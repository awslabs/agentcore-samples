"""Create a short-lived AgentCore payment session for one research run."""

from __future__ import annotations

import argparse
import os
import uuid
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path

from bedrock_agentcore.payments import PaymentManager
from dotenv import load_dotenv
from payment import create_payment_manager


def parse_budget(raw: str) -> str:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("budget must be a decimal amount") from exc
    if not value.is_finite() or value <= 0:
        raise argparse.ArgumentTypeError("budget must be finite and greater than zero")
    try:
        if value != value.quantize(Decimal("0.000001")):
            raise argparse.ArgumentTypeError("budget must have at most six decimal places")
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("budget is too large") from exc
    # Preserve sub-cent limits: rounding 0.001 to cents would silently create zero.
    return format(value, "f")


def parse_expiry_minutes(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expiry must be a whole number of minutes") from exc
    if not 15 <= value <= 480:
        raise argparse.ArgumentTypeError("expiry must be between 15 and 480 minutes")
    return value


def create_session(manager: PaymentManager, user_id: str, budget: str, expiry_minutes: int) -> dict:
    """Create a capped session through the public AgentCore SDK."""
    return manager.create_payment_session(
        user_id=user_id,
        limits={"maxSpendAmount": {"value": budget, "currency": "USD"}},
        expiry_time_in_minutes=expiry_minutes,
        client_token=str(uuid.uuid4()),
    )


def main(argv: Sequence[str] | None = None) -> None:
    load_dotenv(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=parse_budget, default="0.25")
    parser.add_argument("--expiry-minutes", type=parse_expiry_minutes, default=60)
    args = parser.parse_args(argv)

    manager_arn = os.getenv("PAYMENT_MANAGER_ARN", "").strip()
    user_id = os.getenv("PAYMENT_USER_ID", "").strip()
    if not manager_arn or not user_id:
        parser.error("Set PAYMENT_MANAGER_ARN and PAYMENT_USER_ID in .env before creating a session")
    manager = create_payment_manager(manager_arn)
    session = create_session(manager, user_id, args.budget, args.expiry_minutes)

    session_id = session["paymentSessionId"]
    print(f"Created session with a ${args.budget} cap for {args.expiry_minutes} minutes.")
    print(f"export PAYMENT_SESSION_ID={session_id}")


if __name__ == "__main__":
    main()
