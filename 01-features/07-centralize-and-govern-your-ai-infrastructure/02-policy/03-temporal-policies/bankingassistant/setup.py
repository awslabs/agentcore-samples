# /// script
# requires-python = ">=3.10"
# dependencies = ["boto3==1.43.36", "botocore==1.43.36", "jmespath==1.0.1", "s3transfer==0.19.0", "python-dateutil==2.9.0.post0", "six==1.17.0", "urllib3==2.5.0"]
# ///
"""
Create the gateway IAM role, policy engine, gateway, MCP server target,
and base permits for the banking assistant temporal policies sample.

The script reconciles resources recorded in setup_config.json. It refuses to
adopt an unrecorded role; keep this file through partial failures and cleanup.

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
from oauth_provider import STATE_FILE as OAUTH_STATE_FILE, verify_provider
from policy_contract import (API_VERSION, require_policy_model, require_policy_client_model,
                             verified_policy_session, policy_statement)
from resource_state import (OWNER_TAG, absent, atomic_save, require_equal,
                            verify_resources)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REGION = os.environ.get("REGION", "us-east-1")
GATEWAY_ROLE_NAME = os.environ.get("GATEWAY_ROLE_NAME", "banking-gateway-role")
GATEWAY_NAME = os.environ.get("GATEWAY_NAME", "banking-gateway")
ENGINE_NAME = os.environ.get("ENGINE_NAME", "banking_policy_engine")
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
    atomic_save(CONFIG_FILE, cfg)


def ownership_id() -> str:
    owner = load_config().get("ownership_id")
    if not owner:
        owner = str(uuid.uuid4())
        save_config({"ownership_id": owner})
    return owner


def create_intent(key: str, params: dict) -> str:
    """Persist request and stable token BEFORE sending; never reuse for drift."""
    intents = load_config().get("create_intents", {})
    if key in intents:
        require_equal(intents[key]["params"], params, key + " create intent")
    else:
        intents[key] = {"clientToken": str(uuid.uuid4()), "params": params}
        save_config({"create_intents": intents})
    return intents[key]["clientToken"]


def check_environment(cfg: dict, account_id: str) -> None:
    expected = {"account_id": account_id, "region": REGION,
                "role_name": GATEWAY_ROLE_NAME, "gateway_name": GATEWAY_NAME,
                "engine_name": ENGINE_NAME}
    if cfg and any(cfg.get(key) != value for key, value in expected.items()):
        raise RuntimeError(
            "setup_config.json does not match this account/region/resource names. "
            "Legacy state requires a separate reviewed migration; do not edit it to adopt resources. "
            + json.dumps(expected)
        )


def wait_ready(get_resource, ready: str, label: str, attempts: int = 60) -> dict:
    for _ in range(attempts):
        resource = get_resource()
        status = resource.get("status")
        if status == ready:
            return resource
        if "FAILED" in str(status):
            raise RuntimeError(f"{label} failed: {resource}")
        time.sleep(5)
    raise TimeoutError(f"{label} did not reach {ready}; keep setup_config.json and retry")


# ---------------------------------------------------------------------------
# IAM role
# ---------------------------------------------------------------------------


def ensure_gateway_role(iam, account_id: str, cfg: dict) -> str:
    try:
        role = iam.get_role(RoleName=GATEWAY_ROLE_NAME)["Role"]
        arn = role["Arn"]
        tags = {t["Key"]: t["Value"] for t in role.get("Tags", [])}
        intent = load_config().get("create_intents", {}).get("role")
        if not intent or not cfg.get("ownership_id") or tags.get(OWNER_TAG) != cfg["ownership_id"]:
            raise RuntimeError(f"Refusing to adopt unrecorded IAM role {arn}")
        require_equal(intent["params"]["RoleName"], GATEWAY_ROLE_NAME, "role intent")
        require_equal(arn, f"arn:aws:iam::{account_id}:role/{GATEWAY_ROLE_NAME}", "role ARN")
        if cfg.get("role_arn"):
            require_equal(arn, cfg["role_arn"], "recorded role ARN")
        if cfg.get("role_id"):
            require_equal(role["RoleId"], cfg["role_id"], "recorded role ID")
        # An accepted IAM create with a lost response is recoverable only via
        # our pre-existing intent AND the unique tag placed by that request.
        save_config({"role_arn": arn, "role_id": role["RoleId"]})
        print(f"  Gateway role already exists: {arn}")
    except ClientError as error:
        if not absent(error):
            raise
        if cfg.get("role_arn"):
            raise RuntimeError("Recorded IAM role is absent; retain state and audit") from error
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
        params = {"RoleName": GATEWAY_ROLE_NAME, "AssumeRolePolicyDocument": trust,
                  "Tags": [{"Key": OWNER_TAG, "Value": ownership_id()}]}
        create_intent("role", params)  # IAM has no clientToken: recovery uses tags.
        role = iam.create_role(**params)["Role"]
        arn = role["Arn"]
        save_config({"role_arn": arn, "role_id": role["RoleId"]})
        print(f"  Created gateway role: {arn}")

    return arn


def apply_gateway_policy(iam, cfg: dict, provider: dict) -> None:
    """Two phases: exact engine before gateway create; exact OAuth resources after.

    Authorization action set follows @aws/agentcore-cdk 1.0.3 grantAuthorizeAll.
    Directory/vault roots follow aws-cdk-lib 2.269.0 OAuth grants (see README).
    No wildcard engine, gateway, workload identity, provider or secret grants.
    """
    resources = [cfg["engine_arn"]]
    if cfg.get("gateway_arn"):
        resources.append(cfg["gateway_arn"])
    statements = [{
        "Sid": "PolicyEngineAuthorization", "Effect": "Allow",
        "Action": ["bedrock-agentcore:GetPolicyEngine",
                   "bedrock-agentcore:CheckAuthorizePermissions",
                   "bedrock-agentcore:AuthorizeAction",
                   "bedrock-agentcore:PartiallyAuthorizeActions"],
        "Resource": resources,
    }]
    if cfg.get("workload_identity_arn"):
        prefix = f"arn:aws:bedrock-agentcore:{cfg['region']}:{cfg['account_id']}:"
        directory = prefix + "workload-identity-directory/default"
        identity = cfg["workload_identity_arn"]
        statements.extend([
            {"Sid": "GetWorkloadAccessToken", "Effect": "Allow",
             "Action": ["bedrock-agentcore:GetWorkloadAccessToken",
                        "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                        "bedrock-agentcore:GetWorkloadAccessTokenForUserId"],
             "Resource": [directory, identity]},
            {"Sid": "ResourceOauth2Token", "Effect": "Allow",
             "Action": ["bedrock-agentcore:CompleteResourceTokenAuth",
                        "bedrock-agentcore:GetResourceOauth2Token"],
             "Resource": [prefix + "token-vault/default", provider["provider_arn"], directory, identity]},
            {"Sid": "GetSecretValue", "Effect": "Allow",
             "Action": "secretsmanager:GetSecretValue", "Resource": provider["secret_arn"]},
        ])
    iam.put_role_policy(RoleName=cfg["role_name"], PolicyName="GatewayExecutionPolicy",
                        PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": statements}))
    print("  Bounded GatewayExecutionPolicy applied — waiting 15s for IAM propagation...")
    time.sleep(15)


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
        wait_ready(lambda: ctrl.get_policy_engine(policyEngineId=cfg["engine_id"]),
                   "ACTIVE", "Policy engine")
        return cfg["engine_id"], cfg["engine_arn"]

    print(f"  Creating policy engine '{ENGINE_NAME}'...")
    params = {"name": ENGINE_NAME,
              "description": "Temporal policies for the banking assistant",
              "tags": {OWNER_TAG: ownership_id()}}
    token = create_intent("engine", params)
    resp = ctrl.create_policy_engine(**params, clientToken=token)
    engine_id = resp["policyEngineId"]
    engine_arn = resp["policyEngineArn"]

    # Persist immediately so failed/slow creates can be retried or cleaned up.
    save_config({"engine_id": engine_id, "engine_arn": engine_arn})
    wait_ready(lambda: ctrl.get_policy_engine(policyEngineId=engine_id),
               "ACTIVE", "Policy engine")
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
    return wait_ready(lambda: ctrl.get_gateway(gatewayIdentifier=gateway_id),
                      "READY", "Gateway")


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
        create_params = {**params, "tags": {OWNER_TAG: ownership_id()}}
        token = create_intent("gateway", create_params)
        gateway_id = ctrl.create_gateway(**create_params, clientToken=token)["gatewayId"]
        save_config({"gateway_id": gateway_id})

    gw = wait_gateway_ready(ctrl, gateway_id)

    gateway_arn = gw["gatewayArn"]
    identity = gw.get("workloadIdentityDetails", {}).get("workloadIdentityArn", "")
    prefix = gateway_arn.split(":gateway/")[0] + ":workload-identity-directory/default/workload-identity/"
    if not identity.startswith(prefix) or not identity[len(prefix):] or any(c in identity for c in "*?"):
        raise RuntimeError("Gateway did not return an exact account/region workload identity ARN")
    if cfg.get("workload_identity_arn"):
        require_equal(identity, cfg["workload_identity_arn"], "Gateway workload identity")
    gateway_url = gw.get("gatewayUrl", "")
    print(f"  Gateway ARN:  {gateway_arn}")
    print(f"  Gateway URL:  {gateway_url}")
    save_config(
        {
            "gateway_id": gateway_id,
            "gateway_arn": gateway_arn,
            "gateway_url": gateway_url,
            "workload_identity_arn": identity,
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
    params = {
        "gatewayIdentifier": gateway_id, "name": TARGET_NAME,
        "description": "Banking assistant MCP server (all tools)",
        "credentialProviderConfigurations": [{
            "credentialProviderType": "OAUTH",
            "credentialProvider": {"oauthCredentialProvider": {
                "providerArn": credential_provider_arn, "scopes": ["api/mcp"]}},
        }],
        "targetConfiguration": {"mcp": {"mcpServer": {"endpoint": mcp_server_url}}},
    }
    if cfg.get(key):
        target_id = cfg[key]
    else:
        token = create_intent("target", params)
        # Conflict/timeout/access errors are not absence or proof of ownership.
        resp = ctrl.create_gateway_target(**params, clientToken=token)
        target_id = resp["targetId"]
        save_config({key: target_id})
    current = wait_ready(lambda: ctrl.get_gateway_target(
        gatewayIdentifier=gateway_id, targetId=target_id), "READY", "MCP target")
    for field in ("name", "targetConfiguration", "credentialProviderConfigurations"):
        require_equal(current.get(field), params[field], "MCP target " + field)
    require_equal(current.get("targetId"), target_id, "MCP target ID")
    return target_id


# ---------------------------------------------------------------------------
# Base (non-temporal) permits
# ---------------------------------------------------------------------------


def ensure_base_permits(ctrl, engine_id: str, gateway_arn: str, cfg: dict) -> None:
    require_policy_model()  # Also protect direct/helper callers from shadowed SDK models.
    require_policy_client_model(ctrl)
    recorded = dict(cfg.get("base_policy_ids", {}))
    verified = set()
    for target, tool in BASE_PERMITS:
        name = f"permit_{tool}"
        action = f"{target}___{tool}"
        statement = (
            f'permit(principal, action == AgentCore::Action::"{action}", '
            f'resource == AgentCore::Gateway::"{gateway_arn}");'
        )
        definition = {"cedar": {"statement": statement}}
        params = {"policyEngineId": engine_id, "name": name,
                  "description": f"Base permit for {action}",
                  "validationMode": "IGNORE_ALL_FINDINGS",
                  "enforcementMode": "ACTIVE", "definition": definition}
        policy_id = recorded.get(name)
        if not policy_id:
            token = create_intent("policy:" + name, params)
            try:
                policy_id = ctrl.create_policy(**params, clientToken=token)["policyId"]
            except ClientError as error:
                if error.response["Error"]["Code"] != "ConflictException":
                    raise
                # Dedicated, ownership-verified engine only. Name conflicts
                # still require full definition/mode verification below.
                matches, token_next = [], None
                while True:
                    page = ctrl.list_policies(policyEngineId=engine_id,
                        **({"nextToken": token_next} if token_next else {}))
                    matches.extend(p["policyId"] for p in page["policies"] if p["name"] == name)
                    token_next = page.get("nextToken")
                    if not token_next:
                        break
                if len(matches) != 1:
                    raise RuntimeError(f"Cannot uniquely reconcile conflicting policy {name}") from error
                policy_id = matches[0]
            recorded[name] = policy_id
            save_config({"base_policy_ids": recorded})
        current = wait_ready(lambda: ctrl.get_policy(policyEngineId=engine_id, policyId=policy_id),
                             "ACTIVE", "Base policy " + name)
        for field, expected in (("policyId", policy_id), ("policyEngineId", engine_id),
                                ("name", name), ("enforcementMode", "ACTIVE")):
            require_equal(current.get(field), expected, name + " " + field)
        # The published service can return either plain-statement wrapper.
        # Compare the exact statement, not a generated/ambiguous union.
        require_equal(policy_statement(current.get("definition")), statement, name + " definition")
        verified.add(name)
        save_config({"base_permits": sorted(verified)})
        print(f"  Verified ACTIVE base permit: {name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    sdk = verified_policy_session()  # Before even STS or resource clients/provisioning.
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

    session = boto3.Session(botocore_session=sdk, region_name=REGION)
    account_id = session.client("sts").get_caller_identity()["Account"]
    iam = session.client("iam")
    ctrl = session.client("bedrock-agentcore-control", region_name=REGION, api_version=API_VERSION)

    cfg = load_config()
    check_environment(cfg, account_id)
    # Step 2 must have reserved and recorded this exact tagged provider before CLI upsert.
    oauth_state = json.loads(OAUTH_STATE_FILE.read_text())
    require_equal(oauth_state["account_id"], account_id, "OAuth account")
    require_equal(oauth_state["region"], REGION, "OAuth region")
    provider = verify_provider(ctrl, oauth_state)
    require_equal(provider["provider_arn"], credential_provider_arn, "Target OAuth provider")
    # Unresolved response-lost intents are recovered by the same create below.
    # Recorded identities are checked read-only before any updates.
    if any(cfg.get(k) for k in ("engine_id", "gateway_id")):
        verify_resources(ctrl, iam, cfg, allow_unresolved_intents=True)
    save_config({"account_id": account_id, "region": REGION,
                 "role_name": GATEWAY_ROLE_NAME, "gateway_name": GATEWAY_NAME,
                 "engine_name": ENGINE_NAME})

    print("=== Banking Assistant — Setup ===\n")

    print("Step 1: Gateway IAM role")
    role_arn = ensure_gateway_role(iam, account_id, cfg)
    cfg = load_config()
    # Ownership/immutable RoleId was validated above, never use trust_scoped as proof.
    if cfg.get("gateway_arn"):
        update_role_trust_with_gateway(iam, account_id, cfg["gateway_arn"])

    print("\nStep 2: Policy engine")
    engine_id, engine_arn = ensure_policy_engine(ctrl, cfg)
    cfg = load_config()
    apply_gateway_policy(iam, cfg, provider)

    print("\nStep 3: Gateway")
    gateway_id, gateway_arn = ensure_gateway(
        ctrl, role_arn, engine_arn, discovery_url, gateway_client_id, cfg
    )
    cfg = load_config()

    print("\nStep 4: Scope role trust to gateway ARN")
    update_role_trust_with_gateway(iam, account_id, gateway_arn)
    save_config({"trust_scoped": True})  # Historical checkpoint only, never a retry guard.
    apply_gateway_policy(iam, load_config(), provider)

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
