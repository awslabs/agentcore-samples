"""Delete the AWS resources this sample creates, in dependency order, and verify.

Dry-run by default: it lists what it would remove and exits. Pass --yes to delete.

Order matters, and two operations are asynchronous in ways that bite:

  * A gateway cannot be deleted while it has targets.
  * DeletePolicy is asynchronous, and DeletePolicyEngine refuses while any policy
    remains ("still contains 1 policy and cannot be deleted"), so it waits.

Not deleted unless asked: the AgentCore Runtime (its own CloudFormation stack, via
--include-runtime) and anything in Okta (use deploy/00_delete_okta_apps.py; the AI Agent
itself has no delete API and must go via the Admin Console).

    python deploy/teardown.py                      # preview
    python deploy/teardown.py --yes
    python deploy/teardown.py --yes --include-runtime
    python deploy/teardown.py --yes --keep-secret   # leave the AI Agent key in place
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    SAMPLE_ROOT,
    clients,
    env,
    gateway_name,
    gateway_role_name,
    interceptor_name,
    interceptor_role_name,
    load_env,
    obo_provider_name,
    policy_engine_name,
    region,
    resource_lambda_name,
    resource_role_name,
)

AGENT_KEY_SM_ID = "agentcore/xaa-ai-agent-key"


def plan(aws) -> list[tuple[str, str]]:
    """Everything that exists right now, in the order it must be removed."""
    items: list[tuple[str, str]] = []

    for gw in aws["acc"].list_gateways().get("items", []):
        if gw["name"] != gateway_name():
            continue
        for tgt in aws["acc"].list_gateway_targets(gatewayIdentifier=gw["gatewayId"]).get("items", []):
            items.append(("gateway target", f"{gw['gatewayId']}/{tgt['targetId']}"))
        items.append(("gateway", gw["gatewayId"]))

    for pe in aws["acc"].list_policy_engines().get("policyEngines", []):
        if pe["name"] != policy_engine_name():
            continue
        for pol in aws["acc"].list_policies(policyEngineId=pe["policyEngineId"]).get("policies", []):
            items.append(("cedar policy", f"{pe['policyEngineId']}/{pol['name']}"))
        items.append(("policy engine", pe["policyEngineId"]))

    try:
        aws["acc"].get_oauth2_credential_provider(name=obo_provider_name())
        items.append(("obo credential provider", obo_provider_name()))
    except aws["acc"].exceptions.ResourceNotFoundException:
        pass

    for api in aws["apigw"].get_apis().get("Items", []):
        if api["Name"] == f"{resource_lambda_name()}-api":
            items.append(("http api", api["ApiId"]))

    for fn in (resource_lambda_name(), interceptor_name()):
        try:
            aws["lam"].get_function(FunctionName=fn)
            items.append(("lambda", fn))
        except aws["lam"].exceptions.ResourceNotFoundException:
            pass

    for role in (resource_role_name(), interceptor_role_name(), gateway_role_name()):
        try:
            aws["iam"].get_role(RoleName=role)
            items.append(("iam role", role))
        except aws["iam"].exceptions.NoSuchEntityException:
            pass

    return items


def delete(aws, args) -> None:
    acc = aws["acc"]

    for gw in acc.list_gateways().get("items", []):
        if gw["name"] != gateway_name():
            continue
        gid = gw["gatewayId"]
        for tgt in acc.list_gateway_targets(gatewayIdentifier=gid).get("items", []):
            acc.delete_gateway_target(gatewayIdentifier=gid, targetId=tgt["targetId"])
            print(f"  deleted target {tgt['targetId']}")
        # A gateway with targets still attached cannot be deleted.
        for _ in range(24):
            if not acc.list_gateway_targets(gatewayIdentifier=gid).get("items", []):
                break
            time.sleep(5)
        acc.delete_gateway(gatewayIdentifier=gid)
        print(f"  deleted gateway {gid}")

    for pe in acc.list_policy_engines().get("policyEngines", []):
        if pe["name"] != policy_engine_name():
            continue
        pe_id = pe["policyEngineId"]
        for pol in acc.list_policies(policyEngineId=pe_id).get("policies", []):
            acc.delete_policy(policyEngineId=pe_id, policyId=pol["policyId"])
            print(f"  deleting policy {pol['name']}")
        # DeletePolicy is async and the engine refuses while any policy remains.
        for _ in range(24):
            if not acc.list_policies(policyEngineId=pe_id).get("policies", []):
                break
            time.sleep(5)
        acc.delete_policy_engine(policyEngineId=pe_id)
        print(f"  deleted policy engine {pe_id}")

    try:
        acc.delete_oauth2_credential_provider(name=obo_provider_name())
        print(f"  deleted credential provider {obo_provider_name()}")
    except acc.exceptions.ResourceNotFoundException:
        pass

    for api in aws["apigw"].get_apis().get("Items", []):
        if api["Name"] == f"{resource_lambda_name()}-api":
            aws["apigw"].delete_api(ApiId=api["ApiId"])
            print(f"  deleted http api {api['ApiId']}")

    for fn in (resource_lambda_name(), interceptor_name()):
        try:
            aws["lam"].delete_function(FunctionName=fn)
            print(f"  deleted lambda {fn}")
        except aws["lam"].exceptions.ResourceNotFoundException:
            pass

    for role in (resource_role_name(), interceptor_role_name(), gateway_role_name()):
        try:
            for name in aws["iam"].list_role_policies(RoleName=role).get("PolicyNames", []):
                aws["iam"].delete_role_policy(RoleName=role, PolicyName=name)
            for att in aws["iam"].list_attached_role_policies(RoleName=role).get("AttachedPolicies", []):
                aws["iam"].detach_role_policy(RoleName=role, PolicyArn=att["PolicyArn"])
            aws["iam"].delete_role(RoleName=role)
            print(f"  deleted role {role}")
        except aws["iam"].exceptions.NoSuchEntityException:
            pass

    if not args.keep_secret:
        try:
            aws["sm"].delete_secret(SecretId=AGENT_KEY_SM_ID, ForceDeleteWithoutRecovery=True)
            print(f"  deleted secret {AGENT_KEY_SM_ID}")
        except aws["sm"].exceptions.ResourceNotFoundException:
            pass

    if args.include_runtime:
        project = SAMPLE_ROOT / env("AGENT_RUNTIME_NAME", "xaatodoagent")
        if project.exists():
            print(f"  removing the AgentCore runtime stack in {project.name}")
            subprocess.run(["agentcore", "destroy", "-y"], cwd=project, check=False)
        else:
            print(f"  • {project.name} not found; skipping the runtime stack")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true", help="Actually delete.")
    ap.add_argument("--include-runtime", action="store_true", help="Also destroy the AgentCore stack.")
    ap.add_argument("--keep-secret", action="store_true", help="Leave the AI Agent key in Secrets Manager.")
    args = ap.parse_args()
    load_env()
    aws = clients()

    items = plan(aws)
    print(f"region {region()}\n")
    if not items:
        print("  Nothing of this sample's remains in AWS.")
    else:
        print(f"  {len(items)} resource(s) would be deleted, in this order:")
        for kind, name in items:
            print(f"    {kind:26} {name}")
        if not args.keep_secret:
            print(f"    {'secret':26} {AGENT_KEY_SM_ID}")
        if args.include_runtime:
            print(f"    {'agentcore stack':26} {env('AGENT_RUNTIME_NAME', 'xaatodoagent')}")

    if not args.yes:
        print("\n  Dry run. Re-run with --yes to delete.")
        return

    print("\n--- deleting ---")
    delete(aws, args)

    print("\n--- verifying ---")
    left = plan(aws)
    if left:
        print(f"  ⚠ {len(left)} resource(s) still present:")
        for kind, name in left:
            print(f"    {kind:26} {name}")
        print("  Some deletions are asynchronous; re-run in a minute.")
    else:
        print("  ✓ none of this sample's AWS resources remain")

    print(
        "\n  Not covered here:\n"
        "    Okta apps and authorization servers  python deploy/00_delete_okta_apps.py --yes\n"
        "    the AI Agent itself                  Admin Console -> Directory -> AI Agents\n"
        "    the AgentCore runtime                --include-runtime, or `agentcore destroy`"
    )


if __name__ == "__main__":
    main()
