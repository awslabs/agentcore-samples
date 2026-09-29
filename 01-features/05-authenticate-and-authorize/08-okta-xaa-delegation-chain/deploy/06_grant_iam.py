"""Grant the Runtime's execution role the permissions the OBO exchange needs.

The AgentCore CLI creates the execution role but knows nothing about AgentCore
Identity, so the two token operations the agent calls must be added afterwards.

Scoped deliberately:

  * `GetWorkloadAccessTokenForJWT` and friends are limited to this account's
    workload-identity directory, not "*".
  * `GetResourceOauth2Token` is limited to the single OBO provider ARN, so a
    compromised agent cannot mint tokens through any other provider in the vault.
  * The Secrets Manager read is limited to the identity service's own OAuth secret
    path.

Those ARNs come from the AWS service reference: these actions DO support
resource-level permissions, so there is no reason to use a wildcard.

    python deploy/06_grant_iam.py                 # discover the role from agentcore status
    python deploy/06_grant_iam.py --role-name X   # or name it explicitly
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import SAMPLE_ROOT, account_id, clients, env, load_env, must_env, region, save_env

POLICY_NAME = "XaaAgentOboAccess"


def discover_role(project_dir: str) -> str | None:
    """Find the Runtime execution role.

    Prefer the CloudFormation stack outputs: the CLI creates the stack and records the
    role ARN there, so this is deterministic. Parsing `agentcore status` was tried first
    and is brittle -- the flag set and output shape vary by CLI version.
    """
    import boto3
    from botocore.exceptions import ClientError

    cfn = boto3.client("cloudformation", region_name=region())
    for stack in (f"AgentCore-{project_dir}-default", f"AgentCore-{project_dir}"):
        try:
            outputs = cfn.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs") or []
        except ClientError:
            continue
        for out in outputs:
            value = out.get("OutputValue") or ""
            if ":role/" in value and "Role" in (out.get("OutputKey") or ""):
                print(f"  found the execution role in stack {stack}")
                return value
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--role-name", help="Execution role name, if discovery fails.")
    args = ap.parse_args()
    load_env()
    aws = clients()
    acct, reg = account_id(), region()

    role = args.role_name
    if not role:
        arn = discover_role(env("AGENT_RUNTIME_NAME", "xaatodoagent"))
        role = arn.split("/")[-1] if arn else None
    if not role:
        print(
            "ERROR: could not determine the Runtime execution role.\n"
            "Deploy the agent first, then either re-run here or pass --role-name.\n"
            "  cd <project> && agentcore status     # look for the execution role",
            file=sys.stderr,
        )
        sys.exit(1)

    provider_arn = must_env("AGENT_OBO_PROVIDER_ARN", "Run deploy/04_create_obo_provider.py first.")
    directory = f"arn:aws:bedrock-agentcore:{reg}:{acct}:workload-identity-directory/default"
    token_vault = f"arn:aws:bedrock-agentcore:{reg}:{acct}:token-vault/default"
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "WorkloadIdentity",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                ],
                # A wildcard on the directory id covers the workload identities beneath
                # it: an IAM wildcard spans "/".
                "Resource": [directory, f"{directory}/*"],
            },
            {
                "Sid": "OboExchange",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:GetResourceOauth2Token",
                # IAM authorizes this action against SEVERAL resources, and every one
                # of them must be listed or the call fails. Naming only the credential
                # provider yields:
                #
                #   not authorized to perform: bedrock-agentcore:GetResourceOauth2Token
                #   on resource: .../token-vault/default
                #
                # which names the resource it actually wanted. The token-vault ARN is
                # the one most easily missed, because the provider ARN already contains
                # the vault as a path prefix and reads like it should be sufficient.
                "Resource": [
                    directory,
                    f"{directory}/*",
                    token_vault,
                    provider_arn,
                ],
            },
            {
                "Sid": "ReadIdentityOauthSecrets",
                "Effect": "Allow",
                "Action": "secretsmanager:GetSecretValue",
                "Resource": f"arn:aws:secretsmanager:{reg}:{acct}:secret:bedrock-agentcore-identity!default/oauth2/*",
            },
        ],
    }

    aws["iam"].put_role_policy(RoleName=role, PolicyName=POLICY_NAME, PolicyDocument=json.dumps(policy))
    print(f"  ✓ attached {POLICY_NAME} to {role}")
    for stmt in policy["Statement"]:
        print(f"    {stmt['Sid']}")
        for res in [stmt["Resource"]] if isinstance(stmt["Resource"], str) else stmt["Resource"]:
            print(f"      {res}")
    save_env(AGENT_EXECUTION_ROLE=role)
    print("\n  Next: python deploy/07_enable_observability.py")


if __name__ == "__main__":
    main()
