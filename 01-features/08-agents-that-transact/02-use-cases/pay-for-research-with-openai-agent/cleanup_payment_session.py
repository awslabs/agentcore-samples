"""Delete one payment session created for this sample."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv
from payment import create_payment_manager


def main(argv: Sequence[str] | None = None) -> None:
    load_dotenv(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--session-id",
        default=os.getenv("PAYMENT_SESSION_ID"),
        help="Exact session to delete; defaults to PAYMENT_SESSION_ID",
    )
    args = parser.parse_args(argv)
    manager_arn = os.getenv("PAYMENT_MANAGER_ARN", "").strip()
    user_id = os.getenv("PAYMENT_USER_ID", "").strip()
    if not manager_arn or not user_id or not args.session_id:
        parser.error("Set PAYMENT_MANAGER_ARN, PAYMENT_USER_ID, and PAYMENT_SESSION_ID (or --session-id)")

    manager = create_payment_manager(manager_arn)
    manager.delete_payment_session(payment_session_id=args.session_id, user_id=user_id)
    print(f"Deleted payment session {args.session_id}. Unset PAYMENT_SESSION_ID before the next run.")


if __name__ == "__main__":
    main()
