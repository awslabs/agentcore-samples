"""Read v1 `agentcore status --json` from stdin; never calls AWS or reads secrets."""

import argparse
import json
import os
import sys
from urllib.parse import quote


def access(status: dict, field: str, credential_name: str = "banking_assistant_tools-oauth") -> str:
    kind, name = {
        "url": ("runtime", "banking_assistant_tools"),
        "credential": ("credential", credential_name),
    }[field]
    matches = [r for r in status["resources"] if r["resourceType"] == kind and r["name"] == name]
    if len(matches) != 1 or matches[0].get("deploymentState") != "deployed":
        raise ValueError(f"{kind} {name} is not deployed on target {status.get('target')}")
    arn = matches[0]["arn"]
    if field == "credential":
        return arn
    partition, service, region = arn.split(":")[1:4]
    if service != "bedrock-agentcore" or ":runtime/" not in arn:
        raise ValueError("Expected an AgentCore Runtime ARN")
    suffix = "amazonaws.com.cn" if partition == "aws-cn" else "amazonaws.com"
    return f"https://bedrock-agentcore.{region}.{suffix}/runtimes/{quote(arn, safe='')}/invocations?qualifier=DEFAULT"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("field", choices=["url", "credential"])
    parser.add_argument(
        "--credential-name", default=os.environ.get("MCP_CREDENTIAL_NAME", "banking_assistant_tools-oauth")
    )
    args = parser.parse_args()
    print(access(json.load(sys.stdin), args.field, args.credential_name))
