"""Inspect configuration and the agent team without AWS, model, or merchant calls."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import NoReturn

from dotenv import load_dotenv
from pay_for_research import build_agent_team, build_prompt

PAYMENT_SETTINGS = (
    "PAYMENT_MANAGER_ARN",
    "PAYMENT_INSTRUMENT_ID",
    "PAYMENT_SESSION_ID",
    "PAYMENT_USER_ID",
    "PAID_RESEARCH_ALLOWED_HOSTS",
)


class DisabledPaymentClient:
    """Make accidental payment access fail during local inspection."""

    def fetch(self, _url: str) -> NoReturn:
        raise AssertionError("Offline inspection cannot make a payment")

    def session_status(self) -> NoReturn:
        raise AssertionError("Offline inspection cannot query AWS")


def inspect_sample(query: str, *, public_only: bool = False) -> dict:
    paid_url = None if public_only else os.getenv("PAID_RESEARCH_URL")
    model = os.getenv("BEDROCK_OPENAI_MODEL", "openai.gpt-5.5")
    web_search_enabled = os.getenv("BEDROCK_OPENAI_WEB_SEARCH_ENABLED", "").lower() in {"1", "true", "yes", "on"}
    team = build_agent_team(
        DisabledPaymentClient() if paid_url else None,
        approved_paid_url=paid_url,
        model=model,
        include_web_search=web_search_enabled,
    )
    return {
        "mode": "offline",
        "model": model,
        "model_region": os.getenv("AWS_REGION", "us-east-1"),
        "web_search_enabled": web_search_enabled,
        "payment_configuration_present": {name: bool(os.getenv(name, "").strip()) for name in PAYMENT_SETTINGS},
        "versions": {
            package: version(package)
            for package in ("bedrock-agentcore", "boto3", "botocore", "openai", "openai-agents")
        },
        "team": {
            "lead_tools": [tool.name for tool in team.lead.tools],
            "public_tools": [tool.name for tool in team.public_evidence.tools],
            "premium_tools": [tool.name for tool in team.premium_evidence.tools] if team.premium_evidence else [],
        },
        "prompt": build_prompt(query, paid_url),
    }


def main(argv: Sequence[str] | None = None) -> None:
    load_dotenv(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="Assess the material near-term drivers and risks for AMZN.")
    parser.add_argument("--public-only", action="store_true", help="Inspect a team without a premium specialist")
    args = parser.parse_args(argv)
    print(json.dumps(inspect_sample(args.query, public_only=args.public_only), indent=2))


if __name__ == "__main__":
    main()
