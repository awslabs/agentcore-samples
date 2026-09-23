"""
Strands Agent with AgentCore Browser Tool.

Demonstrates intelligent web automation using a Strands agent and the official
strands_tools.browser.AgentCoreBrowser integration:
  1. Create a Strands agent with a browser tool (shared from utils/)
  2. Send natural language prompts for website analysis
  3. The agent autonomously browses and returns structured insights

The shared agent is defined in utils/browser_agent.py.

Prerequisites:
    pip install -r ../requirements.txt

IAM permissions required:
    bedrock-agentcore:StartBrowserSession
    bedrock-agentcore:StopBrowserSession
    bedrock-agentcore:ConnectBrowserAutomationStream
    bedrock:InvokeModel

Usage:
    python demo.py
    python demo.py --prompt "Analyze Apple stock at https://www.marketwatch.com/investing/stock/aapl"
    python demo.py --url "https://www.marketwatch.com/investing/stock/tsla" \\
                   --question "What is the current stock price and market cap?"
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.browser_agent import create_agent


# ── Default prompts ────────────────────────────────────────────────────────────

DEFAULT_PROMPTS = [
    "Analyze the Tesla stock page at https://www.marketwatch.com/investing/stock/tsla "
    "and provide key financial insights: current price, market cap, P/E ratio, and today's change.",
]


# ── Helpers ────────────────────────────────────────────────────────────────────


def run_prompt(agent, prompt: str) -> str:
    print(f"\n{'=' * 60}")
    print(f"Prompt: {prompt}")
    print("=" * 60)

    start = time.time()
    response = agent(prompt)
    elapsed = time.time() - start

    print(f"\n[Agent Response] ({elapsed:.1f}s)")
    print("-" * 60)
    if hasattr(response, "message"):
        for block in response.message.get("content", []):
            if block.get("text"):
                print(block["text"])
    else:
        print(str(response))
    return str(response)


# ── Main ───────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="Strands browser agent demo")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--prompt", default=None, help="Custom analysis prompt")
    group.add_argument("--url", default=None, help="URL to analyse (use with --question)")
    parser.add_argument(
        "--question",
        default="Please provide a comprehensive analysis of this website.",
        help="Question to answer about --url",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("AgentCore Browser Tool — Strands Agent Demo")
    print("=" * 60)

    agent = create_agent()

    if args.url:
        prompt = f"Visit {args.url} and answer: {args.question}"
        run_prompt(agent, prompt)
    elif args.prompt:
        run_prompt(agent, args.prompt)
    else:
        for p in DEFAULT_PROMPTS:
            run_prompt(agent, p)

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
