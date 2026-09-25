"""
Clean up all resources created by setup.py and deploy.py.

    python cleanup.py
    python cleanup.py --discover   # find gateway resources by name prefix, for a
                                   # lost .provision-state.json

Deletes, in order:
- AgentCore Runtime, its CloudWatch log group, execution role, Secrets Manager
  secret, and ECR repository (from .runtime-state.json)
- Gateway target, gateway, gateway IAM role, Cognito domain, app client, and user
  pool (from .provision-state.json)

Cleanup is best-effort: a resource that is already gone must not stop the remaining
steps. Anything that genuinely fails to delete is kept in its state file so the run
can be retried.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

HERE = Path(__file__).parent
RUNTIME_STATE = HERE / ".runtime-state.json"
GATEWAY_STATE = HERE / ".provision-state.json"
GONE_CODES = (
    "ResourceNotFoundException",
    "NoSuchEntity",
    "NoSuchEntityException",
    "RepositoryNotFoundException",
)


def load(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text())
    return {"resources": []}


# ── Runtime stack ─────────────────────────────────────────────────────────────


def cleanup_runtime(region: str) -> bool:
    # Returns True when everything recorded in .runtime-state.json is gone.
    state = load(RUNTIME_STATE)
    resources = list(reversed(state["resources"]))
    if not resources:
        print("runtime stack: nothing recorded")
        return True

    session = boto3.Session(region_name=region)
    agc = session.client("bedrock-agentcore-control")
    iam = session.client("iam")
    ecr = session.client("ecr")
    sm = session.client("secretsmanager")
    cwl = session.client("logs")
    failed = []
    print(f"runtime stack: deleting {len(resources)} resources")
    for r in resources:
        kind = r["kind"]
        try:
            if kind == "agent_runtime":
                agc.delete_agent_runtime(agentRuntimeId=r["agent_runtime_id"])
                # AgentCore creates this log group itself and it holds prompt text,
                # so it must not outlive the runtime.
                group = f"/aws/bedrock-agentcore/runtimes/{r['agent_runtime_id']}-DEFAULT"
                try:
                    cwl.delete_log_group(logGroupName=group)
                    print("  deleted log group", group)
                except ClientError as log_exc:
                    if log_exc.response["Error"]["Code"] != "ResourceNotFoundException":
                        raise
            elif kind == "iam_role":
                for p in iam.list_role_policies(RoleName=r["role_name"])["PolicyNames"]:
                    iam.delete_role_policy(RoleName=r["role_name"], PolicyName=p)
                iam.delete_role(RoleName=r["role_name"])
            elif kind == "ecr_repo":
                ecr.delete_repository(repositoryName=r["repository_name"], force=True)
            elif kind == "secret":
                # No recovery window: the credential should not linger, and the next
                # deploy recreates it from gateway state anyway.
                sm.delete_secret(SecretId=r["secret_name"], ForceDeleteWithoutRecovery=True)
            else:
                print("  skip unknown kind:", kind)
                continue
            print("  deleted", kind)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in GONE_CODES:
                print("  already gone:", kind)
            else:
                print(f"  FAILED {kind}: {exc}")
                failed.append(r)
    if failed:
        state["resources"] = failed
        RUNTIME_STATE.write_text(json.dumps(state, indent=2))
        print(f"  {len(failed)} failed; {RUNTIME_STATE.name} kept for retry")
        return False
    RUNTIME_STATE.unlink(missing_ok=True)
    return True


# ── Gateway stack ─────────────────────────────────────────────────────────────


def cleanup_gateway(region: str, discover: bool) -> bool:
    # Returns True when everything recorded in .provision-state.json is gone.
    state = load(GATEWAY_STATE)
    session = boto3.Session(region_name=region)
    resources = list(reversed(state["resources"]))
    if discover:
        resources = _discover(session, state.get("prefix") or "claude-code-gw")
    if not resources:
        print("gateway stack: nothing recorded")
        return True

    idp = session.client("cognito-idp")
    iam = session.client("iam")
    agc = session.client("bedrock-agentcore-control")
    failed = []
    print(f"gateway stack: deleting {len(resources)} resources")
    for r in resources:
        kind = r["kind"]
        try:
            if kind == "gateway_target":
                agc.delete_gateway_target(
                    gatewayIdentifier=r["gateway_id"], targetId=r["target_id"]
                )
            elif kind == "gateway":
                # Targets must be fully gone first; sweep any we did not record. A
                # target deleted moments earlier can still report DELETING, and
                # DeleteGatewayTarget on it raises a ValidationException, so skip
                # those instead of failing the whole gateway deletion.
                for t in agc.list_gateway_targets(gatewayIdentifier=r["gateway_id"]).get(
                    "items", []
                ):
                    if t.get("status") == "DELETING":
                        continue
                    agc.delete_gateway_target(
                        gatewayIdentifier=r["gateway_id"], targetId=t["targetId"]
                    )
                # DeleteGateway rejects a gateway that still has targets, including
                # ones mid-deletion, so wait for the list to drain.
                for _ in range(30):
                    if not agc.list_gateway_targets(
                        gatewayIdentifier=r["gateway_id"]
                    ).get("items"):
                        break
                    time.sleep(3)
                agc.delete_gateway(gatewayIdentifier=r["gateway_id"])
            elif kind == "iam_role":
                for p in iam.list_role_policies(RoleName=r["role_name"])["PolicyNames"]:
                    iam.delete_role_policy(RoleName=r["role_name"], PolicyName=p)
                iam.delete_role(RoleName=r["role_name"])
            elif kind == "user_pool_domain":
                idp.delete_user_pool_domain(Domain=r["domain"], UserPoolId=r["user_pool_id"])
            elif kind == "user_pool_client":
                idp.delete_user_pool_client(
                    UserPoolId=r["user_pool_id"], ClientId=r["client_id"]
                )
            elif kind == "user_pool":
                idp.delete_user_pool(UserPoolId=r["user_pool_id"])
            else:
                print("  skip unknown kind:", kind)
                continue
            print("  deleted", kind, r)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in GONE_CODES:
                print("  already gone:", kind)
            else:
                print(f"  FAILED {kind}: {exc}")
                failed.append(r)

    if failed:
        state["resources"] = failed
        GATEWAY_STATE.write_text(json.dumps(state, indent=2))
        print(f"  {len(failed)} failed; {GATEWAY_STATE.name} kept for retry")
        return False
    GATEWAY_STATE.unlink(missing_ok=True)
    return True


def _discover(session: Any, prefix: str) -> list[dict[str, Any]]:
    # Find gateway-stack resources by name prefix, for recovery when state is lost.
    found: list[dict[str, Any]] = []
    agc = session.client("bedrock-agentcore-control")
    try:
        for gw in agc.list_gateways().get("items", []):
            if gw.get("name", "").startswith(prefix):
                gid = gw["gatewayId"]
                for t in agc.list_gateway_targets(gatewayIdentifier=gid).get("items", []):
                    found.append(
                        {
                            "kind": "gateway_target",
                            "gateway_id": gid,
                            "target_id": t["targetId"],
                            "name": t.get("name"),
                        }
                    )
                found.append({"kind": "gateway", "gateway_id": gid})
    except ClientError as exc:
        print("gateway discovery failed:", exc)

    iam = session.client("iam")
    for page in iam.get_paginator("list_roles").paginate():
        for role in page["Roles"]:
            if role["RoleName"].startswith(prefix):
                found.append({"kind": "iam_role", "role_name": role["RoleName"]})

    idp = session.client("cognito-idp")
    for page in idp.get_paginator("list_user_pools").paginate(MaxResults=60):
        for pool in page["UserPools"]:
            if pool["Name"].startswith(prefix):
                pid = pool["Id"]
                desc = idp.describe_user_pool(UserPoolId=pid)["UserPool"]
                if desc.get("Domain"):
                    found.append(
                        {
                            "kind": "user_pool_domain",
                            "domain": desc["Domain"],
                            "user_pool_id": pid,
                        }
                    )
                found.append({"kind": "user_pool", "user_pool_id": pid})
    return found


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--region", default=None, help="defaults to the region in the state files")
    ap.add_argument(
        "--discover", action="store_true", help="find gateway resources by name prefix"
    )
    args = ap.parse_args()

    region = (
        args.region
        or load(RUNTIME_STATE).get("region")
        or load(GATEWAY_STATE).get("region")
        or "us-east-1"
    )

    # Runtime first: it reads gateway state, and its secret is recreated from gateway
    # state on the next deploy.
    runtime_ok = cleanup_runtime(region)
    gateway_ok = cleanup_gateway(region, args.discover)

    if runtime_ok and gateway_ok:
        print("\nCleanup complete.")
        return 0
    print("\nSome resources were not deleted; re-run `python cleanup.py` to retry.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
