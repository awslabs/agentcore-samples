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
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import SAMPLE_ROOT, account_id, clients, env, load_env, must_env, region, save_env

POLICY_NAME = "XaaAgentOboAccess"


def discover_role(project_dir: str) -> str | None:
    """Read the execution role from `agentcore status`, which prints JSON."""
    project = SAMPLE_ROOT / project_dir
    if not project.exists():
        return None
    try:
        out = subprocess.run(
            ["agentcore", "status", "--output", "json"],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        ).stdout
        blob = json.loads(out[out.index("{") : out.rindex("}") + 1])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if "role" in key.lower() and isinstance(value, str) and ":role/" in value:
                    yield value
                else:
                    yield from walk(value)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item)

    return next(walk(blob), None)


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
                "Sid": "OboExchangeThisProviderOnly",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:GetResourceOauth2Token",
                "Resource": [provider_arn, directory, f"{directory}/*"],
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
