"""
Tear down the Use Case 2 (Entra) AgentCore resources.

Deletes (in reverse-dependency order):
  - Every gateway target on the gateway (not just the one this example
    creates — orphaned targets from earlier runs are cleaned up too).
    Then waits for async deletion to complete before touching the gateway.
  - The Gateway.
  - The Gateway-actor credential provider.
  - The Agent-actor credential provider.
  - The agent's workload identity.
  - The Gateway service IAM role (only if it matches our auto-create name or
    is set in .env — we never delete a role we can't attribute to this
    example).

Then VERIFIES each resource is gone and reports any survivors. Optionally
clears deploy-populated values from `.env` so the next fresh run starts clean.

What it does NOT touch:
  - Entra app registrations (FrontendApp, AgentApp, GatewayApp). They have no
    AWS-side cost and are reusable. See `deploy/00_delete_entra_apps.py`.
  - The agent's CDK stack. That gets removed with `agentcore remove agent` +
    `agentcore deploy -y -v`.
  - Local files (`.env`, `agentcore/`, `app/`) — unless --clean-env is passed.

Run from the project root:
    python deploy/teardown.py                  # tear down + verify
    python deploy/teardown.py --verify-only    # just check what's still there
    python deploy/teardown.py --clean-env      # also clear deploy-populated .env values
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


# ── Find helpers (used by both delete and verify) ───────────────────────────
def find_gateway_id(client, name: str) -> str | None:
    paginator = client.get_paginator("list_gateways")
    for page in paginator.paginate():
        for gw in page.get("items", []):
            if gw.get("name") == name:
                return gw["gatewayId"]
    return None


def list_all_targets(client, gateway_id: str) -> list[dict]:
    """Return every target on the gateway (paginated)."""
    targets: list[dict] = []
    paginator = client.get_paginator("list_gateway_targets")
    for page in paginator.paginate(gatewayIdentifier=gateway_id):
        targets.extend(page.get("items", []))
    return targets


def role_exists(iam, role_name: str) -> bool:
    try:
        iam.get_role(RoleName=role_name)
        return True
    except ClientError as e:
        if e.response["Error"].get("Code") in {"NoSuchEntity", "NoSuchEntityException"}:
            return False
        raise


def workload_exists(ac_control, name: str) -> bool:
    try:
        # get_workload_identity is the canonical read; fall back to list if the
        # SDK doesn't expose it in this version.
        try:
            ac_control.get_workload_identity(name=name)
            return True
        except AttributeError:
            for page in ac_control.get_paginator("list_workload_identities").paginate():
                for wi in page.get("items", []) or page.get("workloadIdentities", []):
                    if wi.get("name") == name:
                        return True
            return False
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if code in {"ResourceNotFoundException", "NotFoundException"}:
            return False
        raise


def provider_exists(ac_control, name: str) -> bool:
    try:
        ac_control.get_oauth2_credential_provider(name=name)
        return True
    except ClientError as e:
        if e.response["Error"].get("Code") in {
            "ResourceNotFoundException",
            "NotFoundException",
        }:
            return False
        raise


# ── Delete helpers ──────────────────────────────────────────────────────────
def delete_all_targets(client, gateway_id: str) -> None:
    """Delete every target on the gateway (not just microsoft-graph-obo).

    Older test runs may have left orphaned targets under different names;
    the Gateway can't be deleted until every last one is gone.
    """
    targets = list_all_targets(client, gateway_id)
    if not targets:
        print(f"• No gateway targets to delete on {gateway_id}")
        return

    print(f"• Found {len(targets)} target(s) on {gateway_id}. Deleting all…")
    for t in targets:
        name = t.get("name") or t.get("targetId")
        try:
            client.delete_gateway_target(
                gatewayIdentifier=gateway_id,
                targetId=t["targetId"],
            )
            print(f"  ✓ Deleted target: {name} ({t['targetId']})")
        except ClientError as e:
            code = e.response["Error"].get("Code", "")
            if code in {"ResourceNotFoundException", "NotFoundException"}:
                print(f"  • Target already gone: {name}")
            else:
                raise

    # Target deletion is async. Poll until list_gateway_targets is empty
    # (or ~90s elapsed), otherwise delete_gateway will refuse with:
    #   "has targets associated with it"
    import time

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        remaining = list_all_targets(client, gateway_id)
        if not remaining:
            return
        print(f"  ⏳ Waiting for {len(remaining)} target(s) to finish deleting…")
        time.sleep(3)
    remaining = list_all_targets(client, gateway_id)
    if remaining:
        names = ", ".join(t.get("name", t["targetId"]) for t in remaining)
        raise RuntimeError(
            f"Gateway {gateway_id} still has targets after 90s: {names}. Re-run teardown once they finish deleting."
        )


def delete_gateway_if_exists(client, gateway_id: str, name: str) -> None:
    try:
        client.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"✓ Deleted gateway: {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] in {
            "ResourceNotFoundException",
            "NotFoundException",
        }:
            print(f"• Gateway already gone: {name}")
        else:
            raise


def delete_provider_if_exists(client, name: str) -> None:
    try:
        client.delete_oauth2_credential_provider(name=name)
        print(f"✓ Deleted credential provider: {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] in {
            "ResourceNotFoundException",
            "NotFoundException",
        }:
            print(f"• Credential provider already gone: {name}")
        else:
            raise


def delete_workload_if_exists(client, name: str) -> None:
    try:
        client.delete_workload_identity(name=name)
        print(f"✓ Deleted workload identity: {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] in {
            "ResourceNotFoundException",
            "NotFoundException",
        }:
            print(f"• Workload identity already gone: {name}")
        else:
            raise


def delete_iam_role_if_exists(role_name: str) -> None:
    """Delete the Gateway service role. Detaches inline policies first."""
    iam = boto3.client("iam")
    try:
        # Detach inline policies (there's typically only one from us).
        for policy_name in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        # Detach managed policies (we don't attach any, but be defensive).
        for p in iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
        iam.delete_role(RoleName=role_name)
        print(f"✓ Deleted IAM role: {role_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] in {"NoSuchEntity", "NoSuchEntityException"}:
            print(f"• IAM role already gone: {role_name}")
        else:
            raise


# ── Verification ────────────────────────────────────────────────────────────
def verify_all_gone(
    ac_control,
    *,
    gateway_name: str,
    workload_name: str,
    agent_provider: str,
    gateway_provider: str,
    role_name: str,
) -> list[str]:
    """Check every resource. Return a list of survivor descriptions (empty if clean)."""
    survivors: list[str] = []
    iam = boto3.client("iam")

    # 1) Workload identity.
    try:
        if workload_exists(ac_control, workload_name):
            survivors.append(f"Workload identity still exists: {workload_name}")
            print(f"  ✗ workload identity     : {workload_name} — STILL PRESENT")
        else:
            print(f"  ✓ workload identity     : {workload_name} — gone")
    except Exception as e:
        print(f"  ? workload identity     : check errored ({type(e).__name__}: {e})")

    # 2) Credential providers.
    for label, name in [
        ("agent-actor provider  ", agent_provider),
        ("gateway-actor provider", gateway_provider),
    ]:
        try:
            if provider_exists(ac_control, name):
                survivors.append(f"Credential provider still exists: {name}")
                print(f"  ✗ {label}: {name} — STILL PRESENT")
            else:
                print(f"  ✓ {label}: {name} — gone")
        except Exception as e:
            print(f"  ? {label}: check errored ({type(e).__name__}: {e})")

    # 3) Gateway (must also be sure no targets orphaned it).
    try:
        gateway_id = find_gateway_id(ac_control, gateway_name)
        if gateway_id:
            targets = list_all_targets(ac_control, gateway_id)
            detail = f"{gateway_name} (id={gateway_id})"
            if targets:
                detail += f" with {len(targets)} target(s): " + ", ".join(t.get("name", t["targetId"]) for t in targets)
            survivors.append(f"Gateway still exists: {detail}")
            print(f"  ✗ gateway               : {detail} — STILL PRESENT")
        else:
            print(f"  ✓ gateway               : {gateway_name} — gone")
    except Exception as e:
        print(f"  ? gateway               : check errored ({type(e).__name__}: {e})")

    # 4) IAM role for the Gateway.
    try:
        if role_exists(iam, role_name):
            survivors.append(f"IAM role still exists: {role_name}")
            print(f"  ✗ IAM role              : {role_name} — STILL PRESENT")
        else:
            print(f"  ✓ IAM role              : {role_name} — gone")
    except Exception as e:
        print(f"  ? IAM role              : check errored ({type(e).__name__}: {e})")

    return survivors


# ── .env cleanup ────────────────────────────────────────────────────────────
DEPLOY_POPULATED_KEYS = [
    "GATEWAY_MCP_URL",
    "GATEWAY_SERVICE_ROLE_ARN",
    "AGENT_RUNTIME_INVOKE_URL",
]


def clean_env_values(env_path: Path) -> None:
    """Clear the values of deploy-populated keys, leaving the keys themselves in place.

    Only touches keys in DEPLOY_POPULATED_KEYS. Does NOT touch client IDs /
    secrets / scopes / .env structure.
    """
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines()
    changed = False
    for i, line in enumerate(lines):
        for key in DEPLOY_POPULATED_KEYS:
            if line.startswith(f"{key}=") and "=" in line and line.split("=", 1)[1]:
                lines[i] = f"{key}="
                changed = True
                print(f"  ✓ Cleared .env value: {key}")
                break
    if changed:
        env_path.write_text("\n".join(lines) + "\n")


# ── Main ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Skip deletions; just report what's still present.",
    )
    parser.add_argument(
        "--clean-env",
        action="store_true",
        help="After teardown, clear deploy-populated values (GATEWAY_MCP_URL, "
        "GATEWAY_SERVICE_ROLE_ARN, AGENT_RUNTIME_INVOKE_URL) from .env.",
    )
    args = parser.parse_args()

    real_world_root = Path(__file__).resolve().parent.parent
    load_dotenv(real_world_root / ".env")
    region = os.environ.get("AWS_REGION", "us-west-2")

    gateway_name = must_env("GATEWAY_NAME")
    agent_obo_provider_name = must_env("AGENT_OBO_PROVIDER_NAME")
    gateway_obo_provider_name = must_env("GATEWAY_OBO_PROVIDER_NAME")
    workload_name = must_env("AGENT_WORKLOAD_NAME")

    role_arn = os.environ.get("GATEWAY_SERVICE_ROLE_ARN", "").strip()
    role_name = role_arn.rsplit("/", 1)[-1] if role_arn else f"AmazonBedrockAgentCoreGatewayRole-{gateway_name}"

    ac_control = boto3.client("bedrock-agentcore-control", region_name=region)

    if not args.verify_only:
        print("[1/2] Deleting AWS resources…")
        gateway_id = find_gateway_id(ac_control, gateway_name)
        if gateway_id:
            delete_all_targets(ac_control, gateway_id)
            delete_gateway_if_exists(ac_control, gateway_id, gateway_name)
        else:
            print(f"• Gateway not found (already gone?): {gateway_name}")

        delete_provider_if_exists(ac_control, gateway_obo_provider_name)
        delete_provider_if_exists(ac_control, agent_obo_provider_name)
        delete_workload_if_exists(ac_control, workload_name)
        delete_iam_role_if_exists(role_name)
        print()

    header = "[2/2] Verifying" if not args.verify_only else "[verify-only]"
    print(f"{header} — checking AWS resources are gone…")
    survivors = verify_all_gone(
        ac_control,
        gateway_name=gateway_name,
        workload_name=workload_name,
        agent_provider=agent_obo_provider_name,
        gateway_provider=gateway_obo_provider_name,
        role_name=role_name,
    )

    if survivors:
        print()
        print(f"⚠ {len(survivors)} resource(s) still present:")
        for s in survivors:
            print(f"    - {s}")
        print()
        print("Some AgentCore deletes are async. Wait 30 seconds and re-run:")
        print("    python deploy/teardown.py")
        print()
        print("If a resource is stuck (e.g. Gateway target won't delete), inspect it:")
        print("    aws bedrock-agentcore-control list-gateways --region", region)
        print(
            "    aws bedrock-agentcore-control list-gateway-targets --gateway-identifier <id> --region",
            region,
        )
        sys.exit(1 if not args.verify_only else 0)
    else:
        print()
        print("✓ All AWS resources for UC2 Entra are cleaned up.")

    if args.clean_env:
        env_path = real_world_root / ".env"
        print()
        print("[clean-env] Clearing deploy-populated .env values…")
        clean_env_values(env_path)

    print()
    print("Still to clean up manually if you're done for good:")
    print("  - The agent runtime: from inside $AGENT_RUNTIME_NAME/")
    print('      agentcore remove agent --name "$AGENT_RUNTIME_NAME" -y')
    print("      agentcore deploy -y -v")
    print("  - The scaffolded CLI project folder ($AGENT_RUNTIME_NAME/) — rm -rf if you like.")
    print("  - The Entra app registrations — either leave them (cost nothing) or:")
    print("      python deploy/00_delete_entra_apps.py --yes")


if __name__ == "__main__":
    main()
