"""
Smoke test for the deployed Market Trends Agent.

Sends a broker profile and two market questions in one session and prints the replies.

Usage:
    uv run python test_agent.py                      # uses .agent_arn and your profile's region
    uv run python test_agent.py --region us-west-2
    uv run python test_agent.py --agent-arn <runtime ARN>
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

PROMPTS = [
    (
        "Name: Yuval Bing\n"
        "Company: HSBC\n"
        "Role: Investment Advisor\n"
        "Industry Interests: oil, emerging markets\n"
        "Investment Strategy: dividend\n"
        "Risk Tolerance: low"
    ),
    "What is the current price of AMZN?",
    "I am Yuval Bing from HSBC. Based on my profile, what should I watch in the energy sector this week?",
]


def resolve_agent_arn(cli_arn: str | None) -> str:
    """Return the runtime ARN from --agent-arn, AGENT_RUNTIME_ARN or .agent_arn."""
    if cli_arn:
        return cli_arn
    if os.environ.get("AGENT_RUNTIME_ARN"):
        return os.environ["AGENT_RUNTIME_ARN"]
    arn_file = Path(__file__).resolve().parent / ".agent_arn"
    if arn_file.exists():
        return arn_file.read_text().strip()
    sys.exit("No agent ARN found. Run 'uv run python deploy.py' first, or pass --agent-arn.")


def main() -> int:
    """Send the smoke-test prompts to the agent and print the replies."""
    parser = argparse.ArgumentParser(description="Smoke test the deployed Market Trends Agent")
    parser.add_argument("--agent-arn", help="Runtime ARN (default: AGENT_RUNTIME_ARN or .agent_arn)")
    parser.add_argument("--region", help="AWS region (default: the region in the agent ARN)")
    args = parser.parse_args()

    agent_arn = resolve_agent_arn(args.agent_arn)
    region = args.region or agent_arn.split(":")[3]
    client = boto3.client("bedrock-agentcore", region_name=region, config=Config(read_timeout=300))
    # Runtime session ids must be at least 33 characters; one session keeps the conversation together
    session_id = f"market-trends-test-{uuid.uuid4().hex}"

    print(f"Agent  : {agent_arn}")
    print(f"Session: {session_id}\n")
    for prompt in PROMPTS:
        print(f"You: {prompt}\n")
        started = time.time()
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            runtimeSessionId=session_id,
            payload=json.dumps({"prompt": prompt}).encode(),
        )
        body = resp["response"].read().decode("utf-8")
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass
        print(f"Agent ({time.time() - started:.0f}s): {body}\n")
    print("✅ Agent responded to all prompts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
