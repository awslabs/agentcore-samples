#!/usr/bin/env python3
"""Invoke the Claims Agent runtime with SigV4 auth and display clean streamed output.

Usage:
    python3 scripts/test_invoke.py --region us-west-2
    python3 scripts/test_invoke.py --region us-west-2 --prompt 'Your claim text here'
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session as BotocoreSession


def get_runtime_arn(region: str) -> str:
    """Get the Runtime ARN from CloudFormation outputs."""
    cf = boto3.client("cloudformation", region_name=region)
    outputs = cf.describe_stacks(StackName="AgentCore-ClaimsAgent-dev")["Stacks"][0]["Outputs"]
    output_map = {o["OutputKey"]: o["OutputValue"] for o in outputs}

    for key, val in output_map.items():
        if key.startswith("RuntimeArn") or key == "RuntimeArn":
            return val
        if "RuntimeArn" in key:
            return val

    raise RuntimeError("RuntimeArn not found in stack outputs")


def invoke_and_stream(runtime_arn: str, region: str, prompt: str):
    """Invoke the agent with SigV4 auth and stream formatted output."""
    escaped_arn = urllib.parse.quote(runtime_arn, safe="")
    url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_arn}/invocations"

    payload = json.dumps({"prompt": prompt}).encode()

    # Sign the request with SigV4
    session = BotocoreSession()
    credentials = session.get_credentials().get_frozen_credentials()

    aws_request = AWSRequest(
        method="POST",
        url=url,
        data=payload,
        headers={
            "Content-Type": "application/json",
        },
    )
    SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(aws_request)

    req = urllib.request.Request(
        url,
        data=payload,
        headers=dict(aws_request.headers),
    )

    print("\033[90m━━━ Agent Response ━━━\033[0m\n")

    try:
        if not url.startswith("https://"):
            raise ValueError(f"Only HTTPS URLs are permitted: {url}")
        with urllib.request.urlopen(req, timeout=180) as resp:  # nosec B310
            for line in resp:
                decoded = line.decode("utf-8").strip()
                if not decoded:
                    continue

                if decoded.startswith("data: "):
                    chunk = decoded[6:]
                    # JSON-unescape quoted strings
                    if chunk.startswith('"') and chunk.endswith('"'):
                        try:
                            chunk = json.loads(chunk)
                        except json.JSONDecodeError:
                            chunk = chunk[1:-1]
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                elif decoded.startswith("{") and "error" in decoded:
                    try:
                        err = json.loads(decoded)
                        print(f"\n\033[91m❌ Error: {err.get('error', decoded)}\033[0m")
                    except json.JSONDecodeError:
                        print(f"\n\033[91m❌ {decoded}\033[0m")

        print("\n\n\033[90m━━━━━━━━━━━━━━━━━━━━━\033[0m")

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"\n\033[91m❌ HTTP {e.code}: {body}\033[0m")


def main():
    parser = argparse.ArgumentParser(description="Invoke Claims Agent with SigV4")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument(
        "--prompt",
        default="I need to file a claim. My policy is POL-12345. Fender bender yesterday, $2000 damage.",
    )
    args = parser.parse_args()

    print("\033[90m🔑 Authenticating (SigV4)...\033[0m")
    runtime_arn = get_runtime_arn(args.region)
    print(f"\033[90m✅ Connected to {runtime_arn.split('/')[-1]}\033[0m")
    print(f"\033[90m📝 {args.prompt}\033[0m\n")

    invoke_and_stream(runtime_arn, args.region, args.prompt)


if __name__ == "__main__":
    main()
