#!/usr/bin/env python3
"""
Market Trends Agent — Custom LLM-as-a-Judge Evaluators
========================================================

Creates three domain-specific custom evaluators tailored to the Market Trends
Agent's use case. These are LLM-as-a-judge (LLMaaJ) evaluators: each uses a
Bedrock model to score the agent's response against a custom rubric.

  1. MarketDataCitationAccuracy (TRACE)
     Did the agent cite specific, tool-sourced market data (prices, symbols,
     sector figures) rather than making vague or fabricated claims?

  2. BrokerPersonalization (TRACE)
     Did the agent tailor its response to the broker's stated investment focus,
     risk profile, and sector preferences?

  3. FinancialProfessionalism (TRACE)
     Is the response institutional-grade — professionally structured, clearly
     hedged, and appropriate for an investment broker audience?

After creation, evaluator IDs are saved to optimization/custom_evaluator_ids.json
so that user_simulated_dataset.py and optimize_agent.py can pick them up automatically.

Usage
-----
    export AWS_REGION=us-west-2
    uv run python evaluators/custom_evaluators.py

    # List already-created evaluators (no create, just show)
    uv run python evaluators/custom_evaluators.py --list

    # Delete evaluators saved in the ID file
    uv run python evaluators/custom_evaluators.py --delete

Public documentation
--------------------
  Custom evaluators:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/custom-evaluators.html

"""

import argparse
import json
import logging
import os
import uuid
from pathlib import Path

import boto3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_REGION", "us-west-2")
IDS_FILE = Path(__file__).parent.parent / "optimization" / "custom_evaluator_ids.json"

# Evaluator model — Claude Sonnet is recommended for nuanced financial rubrics.
# Must be a globally available inference profile.
EVALUATOR_MODEL_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"

# ---------------------------------------------------------------------------
# Evaluator definitions
# Each dict has: name, level, description, config (llmAsAJudge format)
# ---------------------------------------------------------------------------

EVALUATORS = [
    # -----------------------------------------------------------------
    # 1. MarketDataCitationAccuracy
    # Assesses whether the agent supports its claims with specific, real-
    # looking data retrieved from tools, as opposed to generic statements.
    # -----------------------------------------------------------------
    {
        "name": "mt_market_data_accuracy",
        "level": "TRACE",
        "description": (
            "Measures whether the Market Trends Agent cites specific, "
            "tool-sourced market data (prices, percentages, symbols, news "
            "headlines) rather than making vague or fabricated claims."
        ),
        "config": {
            "llmAsAJudge": {
                "modelConfig": {
                    "bedrockEvaluatorModelConfig": {
                        "modelId": EVALUATOR_MODEL_ID,
                        "inferenceConfig": {
                            "maxTokens": 512,
                            "temperature": 0.0,
                        },
                    }
                },
                "instructions": (
                    "You are evaluating a financial market intelligence agent. "
                    "Your task is to assess whether the agent's response is "
                    "grounded in specific, concrete market data.\n\n"
                    "A HIGH-QUALITY response:\n"
                    "- Cites specific stock prices (e.g. 'NVDA is trading at $875.30, up 2.4%')\n"
                    "- References specific news headlines or earnings figures\n"
                    "- Uses sector-level data with percentages or index values\n"
                    "- Attributes data to a tool call or data source\n\n"
                    "A LOW-QUALITY response:\n"
                    "- Makes vague statements like 'tech stocks are doing well'\n"
                    "- Fabricates or approximates prices without tool retrieval\n"
                    "- Gives generic commentary with no specific data points\n"
                    "- Refuses to give data when the tools were available\n\n"
                    "Context (user question and conversation): {context}\n"
                    "Agent response to evaluate: {assistant_turn}"
                ),
                "ratingScale": {
                    "numerical": [
                        {
                            "value": 1.0,
                            "label": "Fully Cited",
                            "definition": (
                                "Every claim is backed by specific data retrieved via tools: "
                                "prices, percentage changes, news headlines, or sector figures "
                                "with concrete numbers."
                            ),
                        },
                        {
                            "value": 0.75,
                            "label": "Mostly Cited",
                            "definition": (
                                "Most claims include specific data, but one or two statements "
                                "are somewhat general or lack exact figures."
                            ),
                        },
                        {
                            "value": 0.50,
                            "label": "Partially Cited",
                            "definition": (
                                "Some specific data points are present but the response "
                                "mixes concrete figures with vague generalisations."
                            ),
                        },
                        {
                            "value": 0.25,
                            "label": "Mostly Vague",
                            "definition": (
                                "The response is largely generic commentary with minimal "
                                "specific data. Numbers are absent or seem fabricated."
                            ),
                        },
                        {
                            "value": 0.0,
                            "label": "No Data",
                            "definition": (
                                "No specific market data is cited. The response consists of "
                                "pure opinion, refusal, or completely fabricated figures."
                            ),
                        },
                    ]
                },
            }
        },
    },
    # -----------------------------------------------------------------
    # 2. BrokerPersonalization
    # Measures how well the agent tailors its response to the broker's
    # stored profile: sector focus, risk tolerance, investment style.
    # -----------------------------------------------------------------
    {
        "name": "mt_broker_personalization",
        "level": "TRACE",
        "description": (
            "Measures whether the Market Trends Agent personalizes its response "
            "to the broker's stated investment focus, risk profile, and sector "
            "preferences."
        ),
        "config": {
            "llmAsAJudge": {
                "modelConfig": {
                    "bedrockEvaluatorModelConfig": {
                        "modelId": EVALUATOR_MODEL_ID,
                        "inferenceConfig": {
                            "maxTokens": 512,
                            "temperature": 0.0,
                        },
                    }
                },
                "instructions": (
                    "You are evaluating a financial market intelligence agent that "
                    "serves investment brokers. Your task is to assess how well the "
                    "agent's response is personalized to the broker's specific profile.\n\n"
                    "A HIGHLY PERSONALIZED response:\n"
                    "- Explicitly references the broker's stated sector focus "
                    "(e.g. 'Given your ESG focus, you may want to note that…')\n"
                    "- Connects market data to the broker's risk tolerance or "
                    "investment style\n"
                    "- Prioritizes information relevant to the broker's client "
                    "demographics or geographic focus\n"
                    "- Recalls previously stored preferences from the broker's profile\n\n"
                    "A GENERIC (not personalized) response:\n"
                    "- Provides the same market briefing anyone would receive\n"
                    "- Ignores stated sector preferences or risk tolerance\n"
                    "- Does not connect analysis to the broker's investment strategy\n\n"
                    "If the conversation contains no broker profile information (first "
                    "message, anonymous query), score 0.50 as neutral — personalization "
                    "was not possible.\n\n"
                    "Context (user question and conversation): {context}\n"
                    "Agent response to evaluate: {assistant_turn}"
                ),
                "ratingScale": {
                    "numerical": [
                        {
                            "value": 1.0,
                            "label": "Highly Personalized",
                            "definition": (
                                "The response explicitly connects market data to the broker's "
                                "stated sector focus, risk profile, or investment style. "
                                "Stored preferences are clearly applied."
                            ),
                        },
                        {
                            "value": 0.75,
                            "label": "Somewhat Personalized",
                            "definition": (
                                "The response shows some adaptation to the broker's profile "
                                "but could be more tailored. At least one preference is "
                                "acknowledged."
                            ),
                        },
                        {
                            "value": 0.50,
                            "label": "Neutral",
                            "definition": (
                                "The response is reasonable but generic — no profile "
                                "information was available or was not applied."
                            ),
                        },
                        {
                            "value": 0.25,
                            "label": "Missed Personalization",
                            "definition": (
                                "The broker's profile was available in context but the agent "
                                "largely ignored it and gave a one-size-fits-all response."
                            ),
                        },
                        {
                            "value": 0.0,
                            "label": "Completely Generic",
                            "definition": (
                                "The response is entirely generic, contradicts the broker's "
                                "stated preferences, or seems unaware of any profile data "
                                "that was clearly present in the conversation."
                            ),
                        },
                    ]
                },
            }
        },
    },
    # -----------------------------------------------------------------
    # 3. FinancialProfessionalism
    # Assesses the tone, structure, and quality of the response from
    # an institutional financial services perspective.
    # -----------------------------------------------------------------
    {
        "name": "mt_financial_professionalism",
        "level": "TRACE",
        "description": "Measures whether responses are institutional-grade: clear structure, appropriate hedging, and actionable insight for investment brokers.",
        "config": {
            "llmAsAJudge": {
                "modelConfig": {
                    "bedrockEvaluatorModelConfig": {
                        "modelId": EVALUATOR_MODEL_ID,
                        "inferenceConfig": {
                            "maxTokens": 512,
                            "temperature": 0.0,
                        },
                    }
                },
                "instructions": (
                    "You are evaluating a financial market intelligence agent that "
                    "serves professional investment brokers at institutional firms. "
                    "Assess the professionalism, clarity, and quality of the response.\n\n"
                    "A HIGHLY PROFESSIONAL response:\n"
                    "- Is clearly structured (e.g. stock data first, then context, "
                    "then recommendation)\n"
                    "- Uses appropriate financial hedging language "
                    "('as of market close', 'subject to earnings risk', etc.)\n"
                    "- Provides actionable insight, not just raw data\n"
                    "- Matches the formality and depth expected of a Bloomberg "
                    "terminal or sell-side research note\n"
                    "- Concise: doesn't pad with unnecessary caveats or filler\n\n"
                    "A LOW QUALITY response:\n"
                    "- Is disorganized or hard to parse quickly\n"
                    "- Makes definitive investment calls without hedging "
                    "('you should definitely buy this stock')\n"
                    "- Provides raw data dumps with no synthesis or insight\n"
                    "- Uses overly casual language inappropriate for institutional use\n"
                    "- Is excessively long or filled with irrelevant caveats\n\n"
                    "Context (user question and conversation): {context}\n"
                    "Agent response to evaluate: {assistant_turn}"
                ),
                "ratingScale": {
                    "numerical": [
                        {
                            "value": 1.0,
                            "label": "Institutional Grade",
                            "definition": (
                                "Response reads like professional sell-side research: "
                                "well-structured, appropriately hedged, actionable, and "
                                "concise. Perfectly suited for an investment committee briefing."
                            ),
                        },
                        {
                            "value": 0.75,
                            "label": "Professional",
                            "definition": (
                                "Response is professional and clear with minor gaps — "
                                "perhaps slightly verbose or lacking one level of synthesis."
                            ),
                        },
                        {
                            "value": 0.50,
                            "label": "Adequate",
                            "definition": (
                                "Response conveys the necessary information but lacks "
                                "polish, structure, or appropriate hedging expected at "
                                "an institutional level."
                            ),
                        },
                        {
                            "value": 0.25,
                            "label": "Below Standard",
                            "definition": (
                                "Response is disorganized, uses inappropriate tone, makes "
                                "unhedged investment calls, or dumps raw data without "
                                "synthesis."
                            ),
                        },
                        {
                            "value": 0.0,
                            "label": "Unprofessional",
                            "definition": (
                                "Response is entirely unsuitable for institutional use: "
                                "casual, incorrect, misleading, incoherent, or fails to "
                                "address the broker's question."
                            ),
                        },
                    ]
                },
            }
        },
    },
]

# ---------------------------------------------------------------------------
# Create / list / delete helpers
# ---------------------------------------------------------------------------

ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)


def _find_existing(name: str) -> str:
    """Scan list_evaluators to find an evaluator matching the given name."""
    try:
        paginator = ctrl.get_paginator("list_evaluators")
        for page in paginator.paginate():
            for ev in page.get("evaluators", []):
                if ev.get("evaluatorName") == name or ev.get("name") == name:
                    return ev.get("evaluatorId", "")
    except Exception as exc:
        logger.warning("list_evaluators failed: %s", exc)
    return ""


def create_all() -> dict:
    """Create all three custom evaluators and return {name: evaluator_id}."""
    ids: dict = {}
    for ev in EVALUATORS:
        print(f"\nCreating evaluator: {ev['name']} (level={ev['level']})...")
        try:
            resp = ctrl.create_evaluator(
                evaluatorName=ev["name"],
                level=ev["level"],
                description=ev["description"],
                evaluatorConfig=ev["config"],
                clientToken=str(uuid.uuid4()),
            )
            eid = resp["evaluatorId"]
            ids[ev["name"]] = eid
            print(f"  Created: {eid}")
        except Exception as exc:
            exc_name = type(exc).__name__
            if "ConflictException" in exc_name or "already exists" in str(exc).lower():
                print("  Already exists. Looking up existing ID...")
                eid = _find_existing(ev["name"])
                if eid:
                    ids[ev["name"]] = eid
                    print(f"  Found: {eid}")
                else:
                    print(f"  Could not find existing evaluator for {ev['name']}")
            else:
                print(f"  Failed: {exc}")

    IDS_FILE.write_text(json.dumps(ids, indent=2))
    print(f"\nEvaluator IDs saved to: {IDS_FILE}")
    return ids


def list_all() -> None:
    """Print all evaluators visible to this account (built-in + custom)."""
    print(f"\n{'ID':<50} {'Level':<10} {'Name'}")
    print("-" * 90)
    try:
        paginator = ctrl.get_paginator("list_evaluators")
        for page in paginator.paginate():
            for ev in page.get("evaluators", []):
                eid = ev.get("evaluatorId", "")
                level = ev.get("level", "")
                name = ev.get("name", ev.get("evaluatorId", ""))
                print(f"{eid:<50} {level:<10} {name}")
    except Exception as exc:
        print(f"list_evaluators failed: {exc}")


def delete_all() -> None:
    """Delete the evaluators whose IDs are stored in the IDs file."""
    if not IDS_FILE.exists():
        print(f"No IDs file found at {IDS_FILE}. Nothing to delete.")
        return
    ids = json.loads(IDS_FILE.read_text())
    for name, eid in ids.items():
        print(f"Deleting {name}: {eid} ...")
        try:
            ctrl.delete_evaluator(evaluatorId=eid)
            print("  Deleted.")
        except Exception as exc:
            print(f"  Skipped: {exc}")
    IDS_FILE.unlink(missing_ok=True)
    print("Done.")


def load_ids() -> dict:
    """Load saved evaluator IDs, returning {} if file doesn't exist."""
    if IDS_FILE.exists():
        return json.loads(IDS_FILE.read_text())
    return {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Create custom LLM-as-a-judge evaluators for the Market Trends Agent.")
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all evaluators visible to this account and exit.",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete evaluators recorded in custom_evaluator_ids.json and exit.",
    )
    args = parser.parse_args()

    if args.list:
        list_all()
        return

    if args.delete:
        delete_all()
        return

    print("Creating custom LLM-as-a-judge evaluators for Market Trends Agent...")
    print(f"Region         : {REGION}")
    print(f"Evaluator model: {EVALUATOR_MODEL_ID}")
    print(f"Evaluators     : {len(EVALUATORS)}")

    ids = create_all()

    print("\nSummary:")
    print(f"{'Name':<40} {'Evaluator ID'}")
    print("-" * 80)
    for name, eid in ids.items():
        print(f"{name:<40} {eid}")

    print(
        "\nNext steps:\n"
        "  Run user_simulated_dataset.py — it will automatically include these custom\n"
        "  evaluators alongside the built-in evaluators.\n\n"
        "  To delete:\n"
        "    uv run python evaluators/custom_evaluators.py --delete"
    )


if __name__ == "__main__":
    main()
