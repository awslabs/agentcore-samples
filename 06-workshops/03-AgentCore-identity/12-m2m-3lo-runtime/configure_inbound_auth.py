"""
Post-deploy script: Attaches IAM permissions for outbound credential retrieval,
KMS access for the token vault, and registers OAuth2 callback URLs for 3LO flows.

JWT inbound auth is now handled natively by the CLI via agentcore.json.

Run this once after 'agentcore deploy -y'.

Usage:
    python configure_inbound_auth.py
"""

import boto3
import json
import os
import sys


def find_project_dir() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    for entry in os.listdir(base):
        candidate = os.path.join(base, entry)
        if os.path.isdir(candidate) and os.path.isdir(os.path.join(candidate, "agentcore")):
            return candidate
    raise FileNotFoundError("No agentcore project directory found. Run 'agentcore create' first.")


def _find_in_json(obj, key):
    """Recursively search for a key in nested JSON."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            result = _find_in_json(v, key)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_in_json(item, key)
            if result:
                return result
    return None


def get_runtime_id() -> str:
    """Read the deployed runtime ID from deployed-state.json.

    Searches for runtimeId recursively to work across CLI versions.
    """
    project_dir = find_project_dir()
    state_file = os.path.join(project_dir, "agentcore", ".cli", "deployed-state.json")
    if not os.path.exists(state_file):
        raise FileNotFoundError("No deployed-state.json found. Run 'agentcore deploy -y' first.")
    with open(state_file) as f:
        state = json.load(f)
    rid = _find_in_json(state, "runtimeId")
    if rid:
        return rid
    raise ValueError("No deployed agent found. Run 'agentcore deploy -y' first.")


def main():
    try:
        with open("cognito_config.json") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("ERROR: cognito_config.json not found. Run 'python setup_cognito.py' first.")
        sys.exit(1)

    runtime_id = get_runtime_id()
    print(f"Configuring post-deploy permissions on runtime: {runtime_id}")

    ctrl = boto3.client("bedrock-agentcore-control", region_name=config["region"])

    # Fetch current runtime config to extract role ARN
    current = ctrl.get_agent_runtime(agentRuntimeId=runtime_id)

    # Attach IAM policy for AgentCore Identity outbound credential retrieval
    region = config["region"]
    account = boto3.client("sts").get_caller_identity()["Account"]
    role_name = current["roleArn"].split("/")[-1]
    iam = boto3.client("iam")
    print(f"\nAttaching IAM policy to role: {role_name}")
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="AgentCoreIdentityOutbound",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "bedrock-agentcore:GetResourceApiKey",
                            "bedrock-agentcore:GetResourceOauth2Token",
                        ],
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["secretsmanager:GetSecretValue"],
                        "Resource": f"arn:aws:secretsmanager:{region}:{account}:secret:bedrock-agentcore*",
                    },
                ],
            }
        ),
    )
    print("IAM policy attached.")

    # Attach KMS policy so the runtime can use the token vault CMK for USER_FEDERATION flows
    tv = boto3.client("bedrock-agentcore-control", region_name=region).get_token_vault(tokenVaultId="default")
    kms_key_arn = tv.get("kmsConfiguration", {}).get("kmsKeyArn", "")
    if kms_key_arn:
        print(f"Attaching KMS policy for token vault key: {kms_key_arn}")
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="AgentCoreKMSAccess",
            PolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "kms:Decrypt",
                                "kms:GenerateDataKey",
                                "kms:DescribeKey",
                            ],
                            "Resource": kms_key_arn,
                        }
                    ],
                }
            ),
        )
        print("KMS policy attached.")

    # Register allowed callback URLs in the workload identity
    # Required for USER_FEDERATION (3LO) flows
    callback_url = os.environ.get("CALLBACK_URL", "http://localhost:9090/oauth2/callback")
    print(f"\nRegistering callback URL in workload identity: {callback_url}")
    ctrl.update_workload_identity(
        name=runtime_id,
        allowedResourceOauth2ReturnUrls=[callback_url],
    )
    print("Callback URL registered.")
    print("\nWait ~30s for changes to propagate, then run: python invoke.py")


if __name__ == "__main__":
    main()
