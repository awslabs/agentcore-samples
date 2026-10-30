# /// script
# requires-python = ">=3.10"
# dependencies = ["boto3"]
# ///
"""
Create the gateway IAM role, policy engine, gateway, MCP server target,
and base permits for the banking assistant temporal policies sample.

The script is idempotent — re-running it safely skips resources that
already exist. State is saved to setup_config.json so each step can
reference the IDs from previous steps.

Usage:
    uv run setup.py

Required environment variables:
    DISCOVERY_URL            - Cognito OIDC discovery URL (from Step 1)
    GATEWAY_CLIENT_ID        - Cognito gateway client ID (from Step 1)
    MCP_SERVER_URL           - MCP server invocation URL (from Step 2, agentcore status)
    MCP_CREDENTIAL_PROVIDER_ARN - OAuth credential provider ARN for the MCP server target
                                  (from agentcore status --json after deploy)

Optional:
    REGION             - AWS region (default: us-east-1)
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REGION = os.environ.get("REGION", "us-east-1")
GATEWAY_ROLE_NAME = "banking-gateway-role"
GATEWAY_NAME = "banking-gateway"
ENGINE_NAME = "banking_policy_engine"
TARGET_NAME = "banking-assistant-tools"
CONFIG_FILE = Path(__file__).parent / "setup_config.json"

# Base (non-temporal) permits. AgentCore Policy is deny-by-default, so every
# tool that should be callable needs a plain `permit`. These are created once
# by setup.py so the README steps only add the temporal policies on top.
#
# All tools live under the single target name `banking_assistant_tools`.
# The write tools gated by temporal policies are deliberately omitted:
#   transfer_funds, load_portfolio, rebalance_portfolio, execute_trade
#
# A tool without any permit is denied, and a denied action never records a
# ::request/::response, so its history would be invisible to later temporal
# conditions. See docs/predicates.md ("The Dependency Trap").
BASE_PERMITS = [
    ("banking-assistant-tools", "get_account_balance"),
    ("banking-assistant-tools", "get_transaction_history"),
    ("banking-assistant-tools", "freeze_account"),
    ("banking-assistant-tools", "unfreeze_account"),
    ("banking-assistant-tools", "approve_transfer"),
    ("banking-assistant-tools", "reject_transfer"),
    ("banking-assistant-tools", "get_client_profile"),
    ("banking-assistant-tools", "get_market_price"),
    ("banking-assistant-tools", "approve_trade"),
    ("banking-assistant-tools", "interact_advisor"),
]


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_config(data: dict) -> None:
    cfg = load_config()
    cfg.update(data)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ---------------------------------------------------------------------------
# IAM role
# ---------------------------------------------------------------------------


def ensure_gateway_role(iam, account_id: str) -> str:
    try:
        arn = iam.get_role(RoleName=GATEWAY_ROLE_NAME)["Role"]["Arn"]
        print(f"  Gateway role already exists: {arn}")
    except iam.exceptions.NoSuchEntityException:
        trust = json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                        "Condition": {
                            "StringEquals": {"aws:SourceAccount": account_id}
                        },
                    }
                ],
            }
        )
        arn = iam.create_role(
            RoleName=GATEWAY_ROLE_NAME,
            AssumeRolePolicyDocument=trust,
        )["Role"]["Arn"]
        print(f"  Created gateway role: {arn}")

    iam.put_role_policy(
        RoleName=GATEWAY_ROLE_NAME,
        PolicyName="GatewayExecutionPolicy",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "PolicyEngineConfiguration",
                        "Effect": "Allow",
                        "Action": "bedrock-agentcore:GetPolicyEngine",
                        "Resource": f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:policy-engine/*",
                    },
                    {
                        "Sid": "PolicyEngineAuthorization",
                        "Effect": "Allow",
                        "Action": [
                            "bedrock-agentcore:AuthorizeAction",
                            "bedrock-agentcore:PartiallyAuthorizeActions",
                        ],
                        "Resource": [
                            f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:policy-engine/*",
                            f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:gateway/*",
                        ],
                    },
                    {
                        "Sid": "GetWorkloadAccessToken",
                        "Effect": "Allow",
                        "Action": [
                            "bedrock-agentcore:GetWorkloadAccessToken",
                            "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                            "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                        ],
                        "Resource": [
                            f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:workload-identity-directory/default",
                            f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:workload-identity-directory/default/workload-identity/banking-gateway-*",
                        ],
                    },
                    {
                        "Sid": "CompleteResourceTokenAuth",
                        "Effect": "Allow",
                        "Action": "bedrock-agentcore:CompleteResourceTokenAuth",
                        "Resource": [
                            f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:token-vault/default",
                            f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:token-vault/default/oauth2credentialprovider/*",
                            f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:workload-identity-directory/default",
                            f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:workload-identity-directory/default/workload-identity/banking-gateway-*",
                        ],
                    },
                    {
                        "Sid": "GetResourceOauth2Token",
                        "Effect": "Allow",
                        "Action": "bedrock-agentcore:GetResourceOauth2Token",
                        "Resource": [
                            f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:token-vault/default",
                            f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:token-vault/default/oauth2credentialprovider/*",
                            f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:workload-identity-directory/default",
                            f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:workload-identity-directory/default/workload-identity/banking-gateway-*",
                        ],
                    },
                    {
                        "Sid": "GetSecretValue",
                        "Effect": "Allow",
                        "Action": "secretsmanager:GetSecretValue",
                        "Resource": f"arn:aws:secretsmanager:{REGION}:{account_id}:secret:bedrock-agentcore-identity!default/oauth2/*",
                    },
                ],
            }
        ),
    )
    print("  GatewayExecutionPolicy applied — waiting 15s for IAM propagation...")
    time.sleep(15)
    return arn


def update_role_trust_with_gateway(iam, account_id: str, gateway_arn: str) -> None:
    iam.update_assume_role_policy(
        RoleName=GATEWAY_ROLE_NAME,
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                        "Condition": {
                            "StringEquals": {"aws:SourceAccount": account_id},
                            "ArnLike": {"aws:SourceArn": gateway_arn},
                        },
                    }
                ],
            }
        ),
    )
    print(f"  Trust policy scoped to gateway ARN: {gateway_arn}")
    print("  Waiting 10s for IAM propagation...")
    time.sleep(10)


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------


def ensure_policy_engine(ctrl, cfg: dict) -> tuple[str, str]:
    if cfg.get("engine_id"):
        print(f"  Policy engine already exists: {cfg['engine_id']} (skipped)")
        return cfg["engine_id"], cfg["engine_arn"]

    print(f"  Creating policy engine '{ENGINE_NAME}'...")
    resp = ctrl.create_policy_engine(
        name=ENGINE_NAME,
        description="Temporal policies for the banking assistant",
        clientToken=str(uuid.uuid4()),
    )
    engine_id = resp["policyEngineId"]
    engine_arn = resp["policyEngineArn"]

    print("  Waiting for engine ACTIVE...", end="", flush=True)
    for _ in range(40):
        status = ctrl.get_policy_engine(policyEngineId=engine_id).get("status")
        if status == "ACTIVE":
            print(" active")
            break
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            print(f" FAILED ({status})")
            sys.exit(1)
        print(".", end="", flush=True)
        time.sleep(5)

    save_config({"engine_id": engine_id, "engine_arn": engine_arn})
    return engine_id, engine_arn


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


def gateway_params(
    role_arn: str, engine_arn: str, discovery_url: str, client_id: str
) -> dict:
    """
    The full gateway configuration, shared by create_gateway and
    update_gateway.

    update_gateway performs a full replacement, not a partial patch: any
    parameter left out is reset to its default (for example, omitting
    authorizerConfiguration or policyEngineConfiguration clears it). Both
    calls therefore send this identical, complete parameter set so a
    re-run never silently drops the authorizer or the attached engine.
    """
    return {
        "name": GATEWAY_NAME,
        "description": "Banking assistant MCP gateway",
        "roleArn": role_arn,
        "protocolType": "MCP",
        "protocolConfiguration": {
            "mcp": {
                "supportedVersions": ["2025-11-25", "2026-07-28"],
                "sessionConfiguration": {"sessionTimeoutInSeconds": 3600},
                "streamingConfiguration": {"enableResponseStreaming": True},
            }
        },
        "authorizerType": "CUSTOM_JWT",
        "authorizerConfiguration": {
            "customJWTAuthorizer": {
                "discoveryUrl": discovery_url,
                "allowedClients": [client_id],
            }
        },
        "policyEngineConfiguration": {"arn": engine_arn, "mode": "ENFORCE"},
        "exceptionLevel": "DEBUG",
    }


def wait_gateway_ready(ctrl, gateway_id: str) -> dict:
    print("  Waiting for gateway READY...", end="", flush=True)
    for _ in range(60):
        gw = ctrl.get_gateway(gatewayIdentifier=gateway_id)
        status = gw.get("status")
        if status == "READY":
            print(" ready")
            return gw
        if status in ("FAILED", "CREATE_FAILED", "UPDATE_FAILED"):
            print(f" FAILED ({status})")
            sys.exit(1)
        print(".", end="", flush=True)
        time.sleep(5)
    return ctrl.get_gateway(gatewayIdentifier=gateway_id)


def ensure_gateway(
    ctrl, role_arn: str, engine_arn: str, discovery_url: str, client_id: str, cfg: dict
) -> tuple[str, str]:
    params = gateway_params(role_arn, engine_arn, discovery_url, client_id)

    if cfg.get("gateway_id"):
        gateway_id = cfg["gateway_id"]
        print(f"  Gateway already exists: {gateway_id} — reconciling configuration...")
        ctrl.update_gateway(gatewayIdentifier=gateway_id, **params)
    else:
        print(f"  Creating gateway '{GATEWAY_NAME}'...")
        gateway_id = ctrl.create_gateway(**params)["gatewayId"]

    gw = wait_gateway_ready(ctrl, gateway_id)

    gateway_arn = gw.get(
        "gatewayArn",
        f"arn:aws:bedrock-agentcore:{REGION}:{boto3.client('sts').get_caller_identity()['Account']}:gateway/{gateway_id}",
    )
    gateway_url = gw.get("gatewayUrl", "")
    print(f"  Gateway ARN:  {gateway_arn}")
    print(f"  Gateway URL:  {gateway_url}")
    save_config(
        {
            "gateway_id": gateway_id,
            "gateway_arn": gateway_arn,
            "gateway_url": gateway_url,
        }
    )
    return gateway_id, gateway_arn


# ---------------------------------------------------------------------------
# MCP server target
# ---------------------------------------------------------------------------


def ensure_mcp_target(
    ctrl, gateway_id: str, mcp_server_url: str, credential_provider_arn: str, cfg: dict
) -> str:
    key = f"target_id_{TARGET_NAME}"
    if cfg.get(key):
        print(f"  Target '{TARGET_NAME}' already exists: {cfg[key]} (skipped)")
        return cfg[key]

    print(f"  Creating MCP server target '{TARGET_NAME}' -> {mcp_server_url}...")
    try:
        resp = ctrl.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=TARGET_NAME,
            description="Banking assistant MCP server (all tools)",
            credentialProviderConfigurations=[
                {
                    "credentialProviderType": "OAUTH",
                    "credentialProvider": {
                        "oauthCredentialProvider": {
                            "providerArn": credential_provider_arn,
                            "scopes": ["api/mcp"],
                        }
                    },
                }
            ],
            targetConfiguration={
                "mcp": {
                    "mcpServer": {
                        "endpoint": mcp_server_url,
                    }
                }
            },
        )
        target_id = resp["targetId"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConflictException":
            raise
        existing = ctrl.list_gateway_targets(
            gatewayIdentifier=gateway_id, maxResults=50
        )
        target_id = next(
            t["targetId"]
            for t in existing.get("items", [])
            if t.get("name") == TARGET_NAME
        )
        print(f"  Target already exists: {target_id}")
        save_config({key: target_id})
        return target_id

    print("  Waiting for target READY...", end="", flush=True)
    for _ in range(30):
        status = ctrl.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        ).get("status")
        if status == "READY":
            print(" ready")
            break
        if status in ("FAILED", "CREATE_FAILED"):
            print(f" FAILED ({status})")
            sys.exit(1)
        print(".", end="", flush=True)
        time.sleep(5)

    save_config({key: target_id})
    return target_id


# ---------------------------------------------------------------------------
# Base (non-temporal) permits
# ---------------------------------------------------------------------------


def ensure_base_permits(ctrl, engine_id: str, gateway_arn: str, cfg: dict) -> None:
    created = set(cfg.get("base_permits", []))
    for target, tool in BASE_PERMITS:
        # Policy names are limited to 48 chars. Use a short prefix.
        name = f"permit_{tool}"
        if name in created:
            print(f"  Base permit '{name}' already recorded (skipped)")
            continue

        action = f"{target}___{tool}"
        statement = (
            f'permit(principal, action == AgentCore::Action::"{action}", '
            f'resource == AgentCore::Gateway::"{gateway_arn}");'
        )
        try:
            ctrl.create_policy(
                policyEngineId=engine_id,
                name=name,
                description=f"Base permit for {action}",
                validationMode="IGNORE_ALL_FINDINGS",
                definition={"policy": {"statement": statement}},
            )
            print(f"  Created base permit: {name}")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConflictException":
                raise
            print(f"  Base permit already exists: {name}")

        created.add(name)
        save_config({"base_permits": sorted(created)})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    discovery_url = os.environ.get("DISCOVERY_URL", "")
    gateway_client_id = os.environ.get("GATEWAY_CLIENT_ID", "")
    mcp_server_url = os.environ.get("MCP_SERVER_URL", "")
    credential_provider_arn = os.environ.get("MCP_CREDENTIAL_PROVIDER_ARN", "")

    missing = [
        name
        for name, val in [
            ("DISCOVERY_URL", discovery_url),
            ("GATEWAY_CLIENT_ID", gateway_client_id),
            ("MCP_SERVER_URL", mcp_server_url),
            ("MCP_CREDENTIAL_PROVIDER_ARN", credential_provider_arn),
        ]
        if not val
    ]
    if missing:
        print(
            f"Error: missing required environment variables: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)

    session = boto3.Session(region_name=REGION)
    account_id = session.client("sts").get_caller_identity()["Account"]
    iam = session.client("iam")
    ctrl = session.client("bedrock-agentcore-control", region_name=REGION)

    cfg = load_config()

    print("=== Banking Assistant — Setup ===\n")

    print("Step 1: Gateway IAM role")
    role_arn = ensure_gateway_role(iam, account_id)
    cfg = load_config()

    print("\nStep 2: Policy engine")
    engine_id, engine_arn = ensure_policy_engine(ctrl, cfg)
    cfg = load_config()

    print("\nStep 3: Gateway")
    gateway_id, gateway_arn = ensure_gateway(
        ctrl, role_arn, engine_arn, discovery_url, gateway_client_id, cfg
    )
    cfg = load_config()

    print("\nStep 4: Scope role trust to gateway ARN")
    if not cfg.get("trust_scoped"):
        update_role_trust_with_gateway(iam, account_id, gateway_arn)
        save_config({"trust_scoped": True})
    else:
        print("  Already scoped (skipped)")

    print("\nStep 5: MCP server target")
    cfg = load_config()
    ensure_mcp_target(ctrl, gateway_id, mcp_server_url, credential_provider_arn, cfg)

    print("\nStep 6: Base (non-temporal) permits")
    cfg = load_config()
    ensure_base_permits(ctrl, engine_id, gateway_arn, cfg)

    cfg = load_config()
    print("\n=== Done ===")
    print(f"  Engine ID:   {cfg.get('engine_id')}")
    print(f"  Gateway ID:  {cfg.get('gateway_id')}")
    print(f"  Gateway URL: {cfg.get('gateway_url')}")
    print(f"\nexport ENGINE_ID={cfg.get('engine_id')}")
    print(f"export GATEWAY_ID={cfg.get('gateway_id')}")
    print(f"export GATEWAY_ARN={cfg.get('gateway_arn')}")
    print(f"export ENGINE_ARN={engine_arn}")


if __name__ == "__main__":
    main()
