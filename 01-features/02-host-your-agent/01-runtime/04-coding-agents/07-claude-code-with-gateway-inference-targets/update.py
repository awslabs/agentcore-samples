"""
Rebuild the image and roll the existing runtime onto it, keeping the same ARN.

    python update.py [--region us-east-1]

Creating a runtime pins the image it resolved at create time, so pushing a new
:latest is not enough -- UpdateAgentRuntime is needed to roll it forward without
destroying and recreating the runtime (which would change its ARN).

Reuses the build, secret and role logic from deploy.py so the two cannot drift.
"""

import argparse
import sys
import time

import boto3

from deploy import (
    build_and_push,
    ensure_role,
    ensure_secret,
    gateway_outputs,
    load_state,
    runtime_env,
)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()
    region = args.region

    state = load_state()
    arn = (state.get("outputs") or {}).get("agent_runtime_arn")
    rid = next(
        (r.get("agent_runtime_id") for r in state["resources"] if r["kind"] == "agent_runtime"),
        None,
    )
    if not rid:
        sys.exit("no runtime in state -- run `python deploy.py` first")

    uri = build_and_push(region, state)
    gw = gateway_outputs()
    secret_arn = ensure_secret(region, state, gw)
    # Re-applies the inline policy, so an existing role picks up the scoped
    # secretsmanager:GetSecretValue grant instead of silently lacking it.
    ensure_role(region, state, secret_arn)
    agc = boto3.client("bedrock-agentcore-control", region_name=region)
    current = agc.get_agent_runtime(agentRuntimeId=rid)
    print("  updating runtime to the new image")
    agc.update_agent_runtime(
        agentRuntimeId=rid,
        agentRuntimeArtifact={"containerConfiguration": {"containerUri": uri}},
        roleArn=current["roleArn"],
        networkConfiguration={"networkMode": "PUBLIC"},
        protocolConfiguration={"serverProtocol": "HTTP"},
        environmentVariables=runtime_env(gw, secret_arn),
    )
    for _ in range(60):
        status = agc.get_agent_runtime(agentRuntimeId=rid).get("status")
        if status == "READY":
            print("  runtime READY on the new image")
            return 0
        if status in ("UPDATE_FAILED", "FAILED"):
            print(f"  runtime {status}")
            return 1
        time.sleep(5)
    print(f"  still updating; arn={arn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
