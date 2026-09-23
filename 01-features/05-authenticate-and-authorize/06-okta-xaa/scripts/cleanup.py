"""Tear down the resources this sample creates.

SAFE BY DEFAULT: prints what it would delete (dry-run). Pass --yes to actually
delete. Deletions are scoped to this sample's named resources.

What it removes:
  AWS (always, with --yes):
    - Lambda function            (RESOURCE_LAMBDA_NAME, default obo-todo-resource)
    - Secrets Manager secret     (XAA_KEY_SECRET_ID, default agentcore/xaa_private_key)
    - Lambda execution role      (LAMBDA_ROLE_NAME, default obo-todo-lambda-role)
  AWS runtime (only with --include-runtime):
    - CloudFormation stack       (AgentCore-<AGENT_RUNTIME_NAME>-default) — this
      is the AgentCore runtime + its exec role + ECR created by `agentcore deploy`.
  Okta (only with --include-okta, needs OKTA_API_TOKEN):
    - Login app                  (OKTA_LOGIN_CLIENT_ID)
    - Custom Authorization Server (derived from RESOURCE_AS_ISSUER)
    NOTE: the AI Agent (workload principal) is an EA feature without a stable
    delete API — remove it manually in the Admin Console (Directory > AI Agents).

Usage:
    python3 cleanup.py                          # dry-run (shows the plan)
    python3 cleanup.py --yes                     # delete the AWS resource app bits
    python3 cleanup.py --yes --include-runtime   # also delete the AgentCore stack
    python3 cleanup.py --yes --include-okta       # also delete the Okta login app + AS
"""

from __future__ import annotations

import argparse
import os

import boto3
import httpx
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
LAMBDA_NAME = os.environ.get("RESOURCE_LAMBDA_NAME", "obo-todo-resource")
LAMBDA_ROLE = os.environ.get("LAMBDA_ROLE_NAME", "obo-todo-lambda-role")
SECRET_ID = os.environ.get("XAA_KEY_SECRET_ID", "agentcore/xaa_private_key")
RUNTIME_NAME = os.environ.get("AGENT_RUNTIME_NAME", "xaatodoagent")
STACK_NAME = f"AgentCore-{RUNTIME_NAME}-default"


def _do(dry: bool, desc: str, fn) -> None:
    if dry:
        print(f"  [dry-run] would {desc}")
        return
    try:
        fn()
        print(f"  ✓ {desc}")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("ResourceNotFoundException", "NoSuchEntity", "404", "ValidationError"):
            print(f"  - skip ({desc}): not found")
        else:
            print(f"  ! {desc}: {e}")


def cleanup_aws(dry: bool) -> None:
    print("AWS resource app:")
    lam = boto3.client("lambda", region_name=REGION)
    _do(dry, f"delete Lambda function {LAMBDA_NAME}", lambda: lam.delete_function(FunctionName=LAMBDA_NAME))

    sm = boto3.client("secretsmanager", region_name=REGION)
    # Note: the secret name is intentionally kept out of the log message.
    _do(
        dry,
        "delete Secrets Manager secret",
        lambda: sm.delete_secret(SecretId=SECRET_ID, ForceDeleteWithoutRecovery=True),
    )

    iam = boto3.client("iam")

    def _del_role():
        for p in iam.list_attached_role_policies(RoleName=LAMBDA_ROLE).get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=LAMBDA_ROLE, PolicyArn=p["PolicyArn"])
        for name in iam.list_role_policies(RoleName=LAMBDA_ROLE).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=LAMBDA_ROLE, PolicyName=name)
        iam.delete_role(RoleName=LAMBDA_ROLE)

    _do(dry, f"delete IAM role {LAMBDA_ROLE}", _del_role)


def cleanup_runtime(dry: bool) -> None:
    print("AgentCore runtime (CloudFormation):")
    cfn = boto3.client("cloudformation", region_name=REGION)
    _do(dry, f"delete stack {STACK_NAME}", lambda: cfn.delete_stack(StackName=STACK_NAME))
    if not dry:
        print(f"  (stack deletion is async; check: aws cloudformation describe-stacks --stack-name {STACK_NAME})")


def cleanup_okta(dry: bool) -> None:
    print("Okta:")
    base = (os.environ.get("OKTA_ORG_URL") or os.environ.get("OKTA_ISSUER", "")).rstrip("/")
    token = os.environ.get("OKTA_API_TOKEN", "")
    if not base or not token:
        print("  - skip: set OKTA_ORG_URL + OKTA_API_TOKEN to clean up Okta resources")
        return
    c = httpx.Client(
        base_url=base,
        headers={"Authorization": f"SSWS {token}", "Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
    )

    def _delete(desc: str, deactivate_path: str, delete_path: str) -> None:
        if dry:
            print(f"  [dry-run] would deactivate+delete {desc}")
            return
        c.post(deactivate_path)  # 204 if ok / already inactive
        r = c.delete(delete_path)
        if r.status_code in (200, 202, 204, 404):
            print(f"  ✓ {desc}")
        elif r.status_code == 409:
            print(
                f"  ! {desc}: in use (409) — remove the AI Agent first (it holds a "
                f"delegation/resource connection), then re-run."
            )
        else:
            print(f"  ! {desc}: HTTP {r.status_code}")

    login_id = os.environ.get("OKTA_LOGIN_CLIENT_ID", "")
    if login_id and not login_id.startswith("your-"):
        _delete(f"login app {login_id}", f"/api/v1/apps/{login_id}/lifecycle/deactivate", f"/api/v1/apps/{login_id}")

    as_issuer = os.environ.get("RESOURCE_AS_ISSUER", "")
    as_id = as_issuer.rstrip("/").split("/oauth2/", 1)[-1] if "/oauth2/" in as_issuer else ""
    if as_id:
        _delete(
            f"custom AS {as_id}",
            f"/api/v1/authorizationServers/{as_id}/lifecycle/deactivate",
            f"/api/v1/authorizationServers/{as_id}",
        )

    print("  NOTE: delete the AI Agent manually (Admin Console > Directory > AI Agents);")
    print("        it must be removed before the custom AS can be deleted.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="actually delete (default is dry-run)")
    ap.add_argument("--include-runtime", action="store_true", help="also delete the AgentCore CFN stack")
    ap.add_argument("--include-okta", action="store_true", help="also delete the Okta login app + custom AS")
    args = ap.parse_args()
    dry = not args.yes

    print(f"Region: {REGION}   (dry-run={dry})\n")
    cleanup_aws(dry)
    if args.include_runtime:
        cleanup_runtime(dry)
    if args.include_okta:
        cleanup_okta(dry)
    if dry:
        print("\nDry-run only. Re-run with --yes to delete.")


if __name__ == "__main__":
    main()
