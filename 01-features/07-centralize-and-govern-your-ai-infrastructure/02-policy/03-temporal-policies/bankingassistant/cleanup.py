# /// script
# requires-python = ">=3.10"
# dependencies = ["boto3"]
# ///
"""
Tear down everything setup.py created for the banking assistant temporal
policies sample: all policies (base permits + temporal), the policy engine,
the MCP server target, the gateway, and the gateway IAM role.

Resource IDs are read from setup_config.json. The script is idempotent;
resources that are already gone are skipped. Async deletions are polled
to completion before moving to the next step.

Usage:
    uv run cleanup.py              # delete the sample's own resources
    uv run cleanup.py --cognito    # also delete the shared Cognito stack

Optional environment variables:
    REGION               - AWS region (default: us-east-1)
    COGNITO_STACK_NAME   - Cognito stack to delete with --cognito
                           (default: agentcore-gateway-lab)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REGION = os.environ.get("REGION", "us-east-1")
GATEWAY_ROLE_NAME = "banking-gateway-role"
GATEWAY_ROLE_POLICY = "GatewayExecutionPolicy"
CONFIG_FILE = Path(__file__).parent / "setup_config.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


def delete_all_policies(ctrl, engine_id: str) -> None:
    """Delete every policy on the engine (base permits and temporal alike)."""
    try:
        paginator_input = {"policyEngineId": engine_id, "maxResults": 50}
        names: list[tuple[str, str]] = []
        while True:
            resp = ctrl.list_policies(**paginator_input)
            for p in resp.get("policies", []):
                names.append((p["policyId"], p.get("name", p["policyId"])))
            token = resp.get("nextToken")
            if not token:
                break
            paginator_input["nextToken"] = token
    except ClientError as e:
        print(f"  Could not list policies: {e}")
        return

    if not names:
        print("  No policies to delete.")
        return

    for policy_id, name in names:
        try:
            ctrl.delete_policy(policyEngineId=engine_id, policyId=policy_id)
            print(f"  Deleted policy: {name}")
        except ClientError as e:
            if e.response["Error"]["Code"] in ("ResourceNotFoundException", "NotFound"):
                print(f"  Policy already gone: {name}")
            else:
                print(f"  FAILED to delete {name}: {e}")

    # Wait for all policy deletions to complete before proceeding.
    print("  Waiting for policy deletions to complete...", end="", flush=True)
    for _ in range(30):
        resp = ctrl.list_policies(policyEngineId=engine_id, maxResults=50)
        remaining = [
            p for p in resp.get("policies", []) if p.get("status") != "DELETING"
        ]
        deleting = [
            p for p in resp.get("policies", []) if p.get("status") == "DELETING"
        ]
        if not deleting and not remaining:
            print(" done")
            return
        if not deleting and remaining:
            print(f" {len(remaining)} still active (may need another pass)")
            return
        print(".", end="", flush=True)
        time.sleep(3)
    print(" timed out (some policies may still be deleting)")


def delete_engine(ctrl, engine_id: str) -> None:
    try:
        ctrl.delete_policy_engine(policyEngineId=engine_id)
        print(f"  Delete initiated for policy engine: {engine_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("ResourceNotFoundException", "NotFound"):
            print(f"  Policy engine already gone: {engine_id}")
            return
        else:
            print(f"  FAILED to delete policy engine: {e}")
            return

    # Wait for engine deletion.
    print("  Waiting for engine deletion...", end="", flush=True)
    for _ in range(30):
        try:
            status = ctrl.get_policy_engine(policyEngineId=engine_id).get("status")
            if status == "DELETING":
                print(".", end="", flush=True)
                time.sleep(5)
            else:
                print(f" status: {status}")
                return
        except ClientError:
            print(" done")
            return
    print(" timed out")


# ---------------------------------------------------------------------------
# Gateway and targets
# ---------------------------------------------------------------------------


def delete_targets(ctrl, gateway_id: str, cfg: dict) -> None:
    target_ids = [cfg[k] for k in cfg if k.startswith("target_id_") and cfg[k]]
    if not target_ids:
        try:
            resp = ctrl.list_gateway_targets(
                gatewayIdentifier=gateway_id, maxResults=50
            )
            target_ids = [t["targetId"] for t in resp.get("items", [])]
        except ClientError as e:
            print(f"  Could not list targets: {e}")
            return

    if not target_ids:
        print("  No targets to delete.")
        return

    for target_id in target_ids:
        try:
            ctrl.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
            print(f"  Deleted gateway target: {target_id}")
        except ClientError as e:
            if e.response["Error"]["Code"] in (
                "ResourceNotFoundException",
                "NotFound",
            ):
                print(f"  Target already gone: {target_id}")
            else:
                print(f"  FAILED to delete target {target_id}: {e}")

    # Wait for targets to finish deleting.
    print("  Waiting for target deletions...", end="", flush=True)
    for _ in range(20):
        try:
            resp = ctrl.list_gateway_targets(
                gatewayIdentifier=gateway_id, maxResults=50
            )
            if not resp.get("items"):
                print(" done")
                return
        except ClientError:
            print(" done")
            return
        print(".", end="", flush=True)
        time.sleep(3)
    print(" timed out")


def delete_gateway(ctrl, gateway_id: str) -> None:
    try:
        ctrl.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"  Delete initiated for gateway: {gateway_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("ResourceNotFoundException", "NotFound"):
            print(f"  Gateway already gone: {gateway_id}")
            return
        else:
            print(f"  FAILED to delete gateway: {e}")
            return

    # Wait for gateway deletion.
    print("  Waiting for gateway deletion...", end="", flush=True)
    for _ in range(30):
        try:
            status = ctrl.get_gateway(gatewayIdentifier=gateway_id).get("status")
            if status == "DELETING":
                print(".", end="", flush=True)
                time.sleep(5)
            else:
                print(f" status: {status}")
                return
        except ClientError:
            print(" done")
            return
    print(" timed out")


# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------


def delete_gateway_role(iam) -> None:
    try:
        iam.delete_role_policy(
            RoleName=GATEWAY_ROLE_NAME, PolicyName=GATEWAY_ROLE_POLICY
        )
        print(f"  Deleted inline policy: {GATEWAY_ROLE_POLICY}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            print(f"  FAILED to delete inline policy: {e}")

    try:
        iam.delete_role(RoleName=GATEWAY_ROLE_NAME)
        print(f"  Deleted gateway role: {GATEWAY_ROLE_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            print(f"  Gateway role already gone: {GATEWAY_ROLE_NAME}")
        else:
            print(f"  FAILED to delete gateway role: {e}")


def delete_cognito_stack(cfn, stack_name: str) -> None:
    try:
        cfn.delete_stack(StackName=stack_name)
        print(f"  Delete initiated for Cognito stack: {stack_name}")
    except ClientError as e:
        print(f"  FAILED to delete Cognito stack: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tear down the banking assistant sample"
    )
    parser.add_argument(
        "--cognito",
        action="store_true",
        help="Also delete the shared Cognito stack (only if no other lab needs it)",
    )
    args = parser.parse_args()

    cfg = load_config()
    if not cfg:
        print(
            "No setup_config.json found; nothing to clean up. "
            "Run setup.py first, or delete resources manually.",
            file=sys.stderr,
        )
        sys.exit(0)

    session = boto3.Session(region_name=REGION)
    ctrl = session.client("bedrock-agentcore-control", region_name=REGION)
    iam = session.client("iam")

    print("=== Banking Assistant — Cleanup ===\n")

    engine_id = cfg.get("engine_id")
    gateway_id = cfg.get("gateway_id")

    print("Step 1: Policies")
    if engine_id:
        delete_all_policies(ctrl, engine_id)
    else:
        print("  No engine_id in config (skipped)")

    print("\nStep 2: Gateway targets")
    if gateway_id:
        delete_targets(ctrl, gateway_id, cfg)
    else:
        print("  No gateway_id in config (skipped)")

    print("\nStep 3: Policy engine")
    if engine_id:
        delete_engine(ctrl, engine_id)
    else:
        print("  No engine_id in config (skipped)")

    print("\nStep 4: Gateway")
    if gateway_id:
        delete_gateway(ctrl, gateway_id)
    else:
        print("  No gateway_id in config (skipped)")

    print("\nStep 5: Gateway IAM role")
    delete_gateway_role(iam)

    if args.cognito:
        stack_name = os.environ.get("COGNITO_STACK_NAME", "agentcore-gateway-lab")
        print(f"\nStep 6: Cognito stack ({stack_name})")
        cfn = session.client("cloudformation", region_name=REGION)
        delete_cognito_stack(cfn, stack_name)

    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()
        print(f"\nRemoved {CONFIG_FILE.name}")

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
