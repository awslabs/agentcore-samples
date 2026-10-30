#!/usr/bin/env python3
from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import boto3
from dotenv import load_dotenv

SAMPLE_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(SAMPLE_ROOT / ".env")


def require_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise SystemExit(f"Missing {key}")
    return value


def boto3_session() -> boto3.Session:
    kwargs = {"region_name": os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"}
    profile = os.environ.get("AWS_PROFILE", "").strip()
    if profile:
        kwargs["profile_name"] = profile
    return boto3.Session(**kwargs)


def require_confirmed_account() -> None:
    expected_account = require_env("CONFIRM_AWS_ACCOUNT_ID")
    identity = boto3_session().client("sts").get_caller_identity()
    if identity["Account"] != expected_account:
        raise SystemExit(
            "CONFIRM_AWS_ACCOUNT_ID does not match the current AWS caller. "
            "Update .env before running live integration checks."
        )


def main() -> None:
    if os.environ.get("RUN_AWS_X402_E2E") != "1":
        print("AWS/x402 Python integration: SKIPPED - RUN_AWS_X402_E2E is not 1")
        return

    require_confirmed_account()
    require_env("MANAGER_ARN")
    require_env("PAYMENT_CONNECTOR_ID")
    require_env("PAYMENT_SESSION_ID")
    require_env("PAYMENT_INSTRUMENT_ID")
    require_env("PROCESS_PAYMENT_ROLE_ARN")
    require_env("PAY_TO")

    payment_amount = Decimal(require_env("PAYMENT_AMOUNT"))
    max_payment_amount = Decimal(os.environ.get("MAX_PAYMENT_AMOUNT_USDC", "0.25"))
    if payment_amount <= 0:
        raise SystemExit("PAYMENT_AMOUNT must be positive")
    if payment_amount > max_payment_amount:
        raise SystemExit("PAYMENT_AMOUNT exceeds MAX_PAYMENT_AMOUNT_USDC")

    print("AWS/x402 Python integration prerequisite gate passed.")
    print("No live paid request is executed by this prerequisite gate.")


if __name__ == "__main__":
    main()
