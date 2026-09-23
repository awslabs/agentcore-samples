"""Tear down the gateway, KB target, and gateway IAM role created by setup_gateway.py.

Reads ../.env.fmkb-gateway. Does NOT delete the KB.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

import boto3
from botocore.exceptions import ClientError

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import gateway as gw  # noqa: E402


def load_env() -> None:
    env = ROOT / ".env.fmkb-gateway"
    if not env.exists():
        sys.exit(f"{env} not found; nothing to clean up")
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()


def main() -> int:
    load_env()
    region = os.environ["REGION"]
    gateway_id = os.environ["GATEWAY_ID"]
    target_id = os.environ.get("TARGET_ID")
    role_name = os.environ.get("GATEWAY_ROLE_NAME")

    iam = boto3.client("iam")
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    if target_id:
        try:
            gw.delete_target(gateway_id, target_id, region)
            print(f"deleted target {target_id}")
        except ClientError as e:
            print(f"delete_gateway_target: {e}")

    # Gateway deletion is rejected while any target is still being torn down,
    # so wait for the target list to drain before retrying.
    for _ in range(12):
        try:
            remaining = control.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
        except ClientError:
            remaining = []
        if not remaining:
            break
        time.sleep(5)
    try:
        gw.delete_gateway(gateway_id, region)
        print(f"deleted gateway {gateway_id}")
    except ClientError as e:
        print(f"delete_gateway: {e}")

    if role_name:
        try:
            for p in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
                iam.delete_role_policy(RoleName=role_name, PolicyName=p)
            iam.delete_role(RoleName=role_name)
            print(f"deleted role {role_name}")
        except ClientError as e:
            print(f"delete_role: {e}")

    env = ROOT / ".env.fmkb-gateway"
    env.unlink(missing_ok=True)
    print(f"removed {env}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
