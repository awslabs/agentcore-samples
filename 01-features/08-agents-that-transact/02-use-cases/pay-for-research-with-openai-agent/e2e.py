"""Run live model, merchant-challenge, and optional payment smoke tests."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

import httpx
from agents import Runner, ToolCallItem
from bedrock_openai import configure_bedrock_openai
from dotenv import load_dotenv
from pay_for_research import build_agent
from payment import X402PaymentClient


class DisabledPaymentClient:
    def fetch(self, _url: str) -> NoReturn:
        raise AssertionError("The model smoke test must not call the paid tool")

    def session_status(self) -> NoReturn:
        raise AssertionError("The model smoke test must not query payment state")


async def model_smoke(url: str) -> dict[str, str | bool | None]:
    runtime = configure_bedrock_openai()
    agent = build_agent(
        DisabledPaymentClient(),
        approved_paid_url=url,
        model=runtime.model,
        include_web_search=runtime.include_web_search,
    )
    result = await Runner.run(
        agent,
        """Call research_public_evidence exactly once. Ask it to return the token
PUBLIC_SPECIALIST_OK and no other text. Do not call research_premium_evidence.
After the public specialist returns, reply with exactly PAID_RESEARCH_MODEL_OK.""",
    )
    output = str(result.final_output).strip()
    if output != "PAID_RESEARCH_MODEL_OK":
        raise RuntimeError(f"Unexpected model smoke-test output: {output!r}")
    delegated_tools = [
        item.tool_name for item in result.new_items if isinstance(item, ToolCallItem) and item.tool_name is not None
    ]
    if delegated_tools != ["research_public_evidence"]:
        raise RuntimeError(f"Unexpected specialist delegation: {delegated_tools!r}")
    return {
        "provider": "bedrock",
        "model": runtime.model,
        "region": runtime.region,
        "web_search_enabled": runtime.include_web_search,
        "architecture": "manager-with-two-specialists",
        "delegated_tools": ",".join(delegated_tools),
        "status": "passed",
    }


def merchant_challenge(url: str) -> dict[str, Any]:
    response = httpx.get(url, follow_redirects=False, timeout=30.0)
    if response.status_code != 402:
        raise RuntimeError(f"Expected HTTP 402 from test merchant, got {response.status_code}")

    try:
        body = response.json()
    except json.JSONDecodeError:
        body = {}
    challenge = body if isinstance(body, dict) else {}
    payment_required = response.headers.get("payment-required")
    if not challenge.get("accepts") and payment_required:
        try:
            padding = "=" * (-len(payment_required) % 4)
            decoded = base64.b64decode(payment_required + padding)
            header_challenge = json.loads(decoded)
            if isinstance(header_challenge, dict):
                challenge = header_challenge
        except (ValueError, json.JSONDecodeError):
            pass
    version = challenge.get("x402Version")
    accepts = challenge.get("accepts")
    if (
        version not in (1, 2)
        or not isinstance(accepts, list)
        or not accepts
        or any(not isinstance(offer, dict) or not offer.get("network") for offer in accepts)
    ):
        raise RuntimeError("Merchant returned HTTP 402 without a supported x402 challenge")
    return {
        "status": "passed",
        "status_code": response.status_code,
        "x402_version": version,
        "has_payment_required_header": payment_required is not None,
        "offers": [
            {
                "network": offer["network"],
                "scheme": offer.get("scheme"),
                "amount_base_units": offer.get("amount", offer.get("maxAmountRequired")),
                "asset": offer.get("asset"),
            }
            for offer in accepts
        ],
    }


def payment_smoke(url: str) -> dict[str, str | int | bool | None]:
    result = json.loads(X402PaymentClient.from_env().fetch(url))
    if not result.get("ok") or not result.get("payment_made"):
        raise RuntimeError(f"Live payment smoke test failed: {result}")
    return {
        "status": "passed",
        "status_code": result.get("status_code"),
        "payment_made": result.get("payment_made"),
        "payment_attempts": result.get("payment_attempts"),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run paid-research live smoke tests")
    result.add_argument(
        "--url",
        default=os.getenv(
            "PAID_RESEARCH_URL",
            "https://x402-test.genesisblock.ai/api/market-news",
        ),
    )
    mode = result.add_mutually_exclusive_group()
    mode.add_argument(
        "--payment",
        action="store_true",
        help="Execute a real testnet payment; requires configured payment resources",
    )
    mode.add_argument(
        "--merchant-only",
        action="store_true",
        help="Inspect the x402 challenge and price without AWS credentials, model calls, or payments",
    )
    return result


def main(argv: Sequence[str] | None = None) -> None:
    load_dotenv(Path(__file__).with_name(".env"))
    args = parser().parse_args(argv)
    report = {
        "model": (
            {"status": "skipped", "reason": "--merchant-only was supplied"}
            if args.merchant_only
            else asyncio.run(model_smoke(args.url))
        ),
        "merchant_challenge": merchant_challenge(args.url),
    }
    if args.payment:
        report["payment"] = payment_smoke(args.url)
    else:
        report["payment"] = {"status": "skipped", "reason": "--payment was not supplied"}
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
