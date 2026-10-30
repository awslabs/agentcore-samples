"""Tear down the AWS resources this sample creates, in reverse-dependency order.

Order matters in two places:

  * Gateway targets must be gone before the gateway (DeleteGateway refuses
    while any target remains), and target deletion is async, so this waits.

  * The consent portal must finish DELETING before its IdP credential provider
    and execution role are removed. The portal holds references to both, and
    deleting them from under a still-DELETING portal is how you get a stuck
    delete.

Deletes: gateway targets -> GitHub credential provider -> consent portal (and
waits) -> primary IdP credential provider -> portal execution role -> gateway
-> gateway service role. Then verifies each is gone and reports survivors.

Does NOT touch:
  * The agent runtime / its CDK stack — remove that first with, from inside
    the scaffolded project folder:
        agentcore remove agent --name "$AGENT_RUNTIME_NAME" -y && agentcore deploy -y -v
  * Your IdP app registrations — `00_delete_entra_apps.py` /
    `00_delete_okta_apps.py`, or leave them (they cost nothing).
  * Your GitHub OAuth App — delete it yourself at
    https://github.com/settings/developers if you are done with it.

Run from the sample root:
    python deploy/teardown.py --entra
    python deploy/teardown.py --entra --verify-only
    python deploy/teardown.py --entra --clean-env
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    ENV_PATH,
    check_boto_version,
    control_client,
    find_gateway_by_name,
    find_portal_by_name,
    gateway_name,
    gateway_role_name,
    github_provider_name,
    idp_provider_name,
    interceptor_lambda_name,
    load_env,
    portal_name,
    portal_role_name,
    select_idp_with_args,
)

NOT_FOUND = {"ResourceNotFoundException", "NotFoundException"}
NO_SUCH_ENTITY = {"NoSuchEntity", "NoSuchEntityException"}


def interceptor_present(control, gateway_id: str, idp: dict, region: str) -> bool:
    """True if step 07's interceptor is attached, or its Lambda still exists."""
    try:
        gw = control.get_gateway(gatewayIdentifier=gateway_id)
        if gw.get("interceptorConfigurations"):
            return True
    except ClientError as e:
        if e.response["Error"]["Code"] not in NOT_FOUND:
            raise
    # The Lambda outliving the attachment is the case that orphans resources, so
    # it counts on its own.
    try:
        boto3.client("lambda", region_name=region).get_function(FunctionName=interceptor_lambda_name(idp))
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in NOT_FOUND:
            return False
        raise


DEPLOY_POPULATED_KEYS = [
    "GATEWAY_ID",
    "GATEWAY_URL",
    "GATEWAY_MCP_URL",
    "GATEWAY_SERVICE_ROLE_ARN",
    "PORTAL_ID",
    "PORTAL_ARN",
    "PORTAL_URL",
    "PORTAL_CALLBACK_URL",
    "PORTAL_CONNECT_RETURN_URL",
    "IDP_PROVIDER_ARN",
    "PORTAL_EXECUTION_ROLE_ARN",
    "GITHUB_PROVIDER_ARN",
    "GITHUB_PROVIDER_CALLBACK_URL",
    "GITHUB_TARGET_ID",
    "AGENT_RUNTIME_INVOKE_URL",
]


def list_all_targets(control, gateway_id: str) -> list[dict]:
    targets: list[dict] = []
    for page in control.get_paginator("list_gateway_targets").paginate(gatewayIdentifier=gateway_id):
        targets.extend(page.get("items", []))
    return targets


def delete_all_targets(control, gateway_id: str) -> None:
    """Delete every target on the gateway, then wait for the async deletes."""
    targets = list_all_targets(control, gateway_id)
    if not targets:
        print(f"• No gateway targets on {gateway_id}")
        return
    print(f"• Deleting {len(targets)} target(s) on {gateway_id}…")
    for t in targets:
        label = t.get("name") or t["targetId"]
        try:
            control.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=t["targetId"])
            print(f"  ✓ Deleted target: {label}")
        except ClientError as e:
            if e.response["Error"].get("Code") in NOT_FOUND:
                print(f"  • Target already gone: {label}")
            else:
                raise
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        remaining = list_all_targets(control, gateway_id)
        if not remaining:
            return
        print(f"  ⏳ Waiting for {len(remaining)} target(s) to finish deleting…")
        time.sleep(3)
    remaining = list_all_targets(control, gateway_id)
    if remaining:
        names = ", ".join(t.get("name", t["targetId"]) for t in remaining)
        print(
            f"ERROR: gateway {gateway_id} still has targets after 90s: {names}.\n"
            f"  Re-run teardown once they finish deleting.",
            file=sys.stderr,
        )
        sys.exit(1)


def delete_portal(control, portal_id: str | None) -> None:
    print("• Consent portal…")
    if not portal_id:
        print("  • None found, skipping")
        return
    try:
        control.delete_consent_portal(consentPortalIdentifier=portal_id)
        print(f"  ✓ Deleting: {portal_id}")
    except ClientError as e:
        if e.response["Error"].get("Code") not in NOT_FOUND:
            raise
        print(f"  • Already gone: {portal_id}")
        return
    # Wait it out — see the module docstring.
    for _ in range(30):
        try:
            status = control.get_consent_portal(consentPortalIdentifier=portal_id)["status"]
        except ClientError as e:
            if e.response["Error"].get("Code") in NOT_FOUND:
                print("  ✓ Deleted")
                return
            raise
        print(f"    status: {status}")
        time.sleep(10)
    print(
        "ERROR: portal still present after waiting; not touching its execution\n"
        "  role or IdP provider while they may still be in use. Re-run teardown\n"
        "  once the delete finishes.",
        file=sys.stderr,
    )
    sys.exit(1)


def delete_provider(control, name: str) -> None:
    try:
        control.delete_oauth2_credential_provider(name=name)
        print(f"  ✓ Deleted credential provider: {name}")
    except ClientError as e:
        if e.response["Error"].get("Code") in NOT_FOUND:
            print(f"  • Credential provider already gone: {name}")
        else:
            raise


def delete_gateway(control, gateway_id: str, name: str) -> None:
    try:
        control.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"  ✓ Deleted gateway: {name}")
    except ClientError as e:
        if e.response["Error"].get("Code") in NOT_FOUND:
            print(f"  • Gateway already gone: {name}")
        else:
            raise


def delete_role(iam, role_name: str) -> None:
    try:
        for policy_name in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        for p in iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
        iam.delete_role(RoleName=role_name)
        print(f"  ✓ Deleted IAM role: {role_name}")
    except ClientError as e:
        if e.response["Error"].get("Code") in NO_SUCH_ENTITY:
            print(f"  • IAM role already gone: {role_name}")
        else:
            raise


# --- verification ----------------------------------------------------------------


def provider_exists(control, name: str) -> bool:
    try:
        control.get_oauth2_credential_provider(name=name)
        return True
    except ClientError as e:
        if e.response["Error"].get("Code") in NOT_FOUND:
            return False
        raise


def role_exists(iam, role_name: str) -> bool:
    try:
        iam.get_role(RoleName=role_name)
        return True
    except ClientError as e:
        if e.response["Error"].get("Code") in NO_SUCH_ENTITY:
            return False
        raise


def verify_all_gone(control, iam, idp: dict) -> list[str]:
    survivors: list[str] = []

    def check(label: str, present: bool, detail: str) -> None:
        if present:
            survivors.append(f"{label} still exists: {detail}")
            print(f"  ✗ {label:<26}: {detail} — STILL PRESENT")
        else:
            print(f"  ✓ {label:<26}: {detail} — gone")

    try:
        portal = find_portal_by_name(control, portal_name(idp))
        check("consent portal", portal is not None, portal_name(idp))
    except Exception as e:  # noqa: BLE001
        print(f"  ? consent portal            : check errored ({type(e).__name__}: {e})")

    for label, name in (
        ("GitHub cred provider", github_provider_name(idp)),
        ("IdP cred provider", idp_provider_name(idp)),
    ):
        try:
            check(label, provider_exists(control, name), name)
        except Exception as e:  # noqa: BLE001
            print(f"  ? {label:<26}: check errored ({type(e).__name__}: {e})")

    try:
        gw = find_gateway_by_name(control, gateway_name(idp))
        detail = gateway_name(idp)
        if gw:
            try:
                targets = list_all_targets(control, gw["gatewayId"])
            except ClientError as e:
                # ListGateways is eventually consistent and can still return a
                # gateway that DeleteGateway has already removed. A 404 from the
                # target listing settles it: the gateway is gone, not surviving.
                if e.response["Error"]["Code"] in NOT_FOUND:
                    gw, targets = None, []
                else:
                    raise
            if targets:
                detail += f" with {len(targets)} target(s)"
        check("gateway", gw is not None, detail)
    except Exception as e:  # noqa: BLE001
        print(f"  ? gateway                   : check errored ({type(e).__name__}: {e})")

    for label, role in (
        ("portal execution role", portal_role_name(idp)),
        ("gateway service role", gateway_role_name(idp)),
    ):
        try:
            check(label, role_exists(iam, role), role)
        except Exception as e:  # noqa: BLE001
            print(f"  ? {label:<26}: check errored ({type(e).__name__}: {e})")

    return survivors


def clean_env_values() -> None:
    """Blank the values of deploy-populated keys, leaving IdP config intact."""
    if not ENV_PATH.exists():
        return
    lines = ENV_PATH.read_text().splitlines()
    changed = False
    for i, line in enumerate(lines):
        for key in DEPLOY_POPULATED_KEYS:
            if line.startswith(f"{key}=") and line.split("=", 1)[1]:
                lines[i] = f"{key}="
                changed = True
                print(f"  ✓ Cleared .env value: {key}")
                break
    if changed:
        ENV_PATH.write_text("\n".join(lines) + "\n")


def main() -> None:
    idp, args = select_idp_with_args(
        __doc__,
        [
            (["--verify-only"], {"action": "store_true", "help": "Skip deletions; just report survivors."}),
            (["--clean-env"], {"action": "store_true", "help": "Also blank deploy-populated .env values."}),
        ],
    )
    check_boto_version()
    load_env()

    control, region = control_client()
    iam = boto3.client("iam", region_name=region)

    if not args.verify_only:
        print("[1/2] Deleting AWS resources…")
        gw = find_gateway_by_name(control, gateway_name(idp))

        # The optional interceptor (step 07) must be detached before the gateway
        # goes, or its Lambda and role are orphaned with nothing referencing them.
        #
        # Detect it from GetGateway, never from the ListGateways summary that
        # find_gateway_by_name returns — the summary carries no
        # interceptorConfigurations, so testing it there always looks empty and
        # silently skips this step. Fall back to the Lambda's own existence, which
        # also covers a re-run after a partial teardown.
        if gw and interceptor_present(control, gw["gatewayId"], idp, region):
            print("• Optional interceptor detected — removing it first…")
            import subprocess

            r = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("07_deploy_interceptor.py")),
                    f"--{idp['name']}",
                    "--remove",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            print("  " + (r.stdout.strip().splitlines() or ["(no output)"])[-1])
            if r.returncode != 0:
                print(f"  ⚠ interceptor removal reported an error:\n{r.stderr.strip()[:400]}", file=sys.stderr)
            gw = find_gateway_by_name(control, gateway_name(idp))

        if gw:
            delete_all_targets(control, gw["gatewayId"])
        else:
            print(f"• Gateway not found (already gone?): {gateway_name(idp)}")

        print("• Credential provider (GitHub outbound)…")
        delete_provider(control, github_provider_name(idp))

        portal = find_portal_by_name(control, portal_name(idp))
        delete_portal(control, (portal or {}).get("consentPortalId") or os.environ.get("PORTAL_ID"))

        print("• Credential provider (primary IdP)…")
        delete_provider(control, idp_provider_name(idp))

        print("• Portal execution role…")
        delete_role(iam, portal_role_name(idp))

        if gw:
            print("• Gateway…")
            delete_gateway(control, gw["gatewayId"], gateway_name(idp))

        print("• Gateway service role…")
        delete_role(iam, gateway_role_name(idp))
        print()

    print("[2/2] Verifying…" if not args.verify_only else "[verify-only] Checking…")
    survivors = verify_all_gone(control, iam, idp)

    if survivors:
        print()
        print(f"⚠ {len(survivors)} resource(s) still present:")
        for s in survivors:
            print(f"    - {s}")
        print()
        print("Some AgentCore deletes are async. Wait 30 seconds and re-run:")
        print(f"    python deploy/teardown.py --{idp['name']}")
        sys.exit(1 if not args.verify_only else 0)

    print()
    print(f"✓ All AWS resources for the {idp['displayName']} run are cleaned up.")

    if args.clean_env:
        print()
        print("[clean-env] Blanking deploy-populated .env values…")
        clean_env_values()

    print()
    print("Still yours to clean up, if you are done for good:")
    print("  - The agent runtime, from inside the scaffolded project folder:")
    print('      agentcore remove agent --name "$AGENT_RUNTIME_NAME" -y && agentcore deploy -y -v')
    print("  - The scaffolded project folder itself (rm -rf $AGENT_RUNTIME_NAME/).")
    print(f"  - Your IdP apps: python deploy/00_delete_{idp['name']}_apps.py --yes")
    print("  - Your GitHub OAuth App: https://github.com/settings/developers")


if __name__ == "__main__":
    main()
