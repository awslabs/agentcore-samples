"""
Provision an AgentCore Gateway with a bedrock-mantle inference target.

    python setup.py [--region us-east-1] [--prefix claude-code-gw]

Creates a Cognito user pool, a resource server with an "invoke" scope, a
client_credentials app client, a user pool domain, an IAM role trusted by
bedrock-agentcore.amazonaws.com, a gateway with a CUSTOM_JWT authorizer, and one
inference target using the bedrock-mantle connector. Prints the resulting Claude Code
environment when done.

Uses boto3 rather than CloudFormation because AWS::BedrockAgentCore::GatewayTarget
accepts only Mcp and Http target configurations, not Inference. Needs a boto3 recent
enough to know bedrock-agentcore-control inference targets.

Every created resource id is written to .provision-state.json as it is created, so
cleanup.py never guesses and a half-finished run is still cleanable. Re-running setup
against a half-finished run resumes it (creates the missing target) rather than
duplicating resources.
"""

import argparse
import json
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

STATE_FILE = Path(__file__).with_name(".provision-state.json")
SCOPE_NAME = "invoke"
RESOURCE_SERVER_ID = "bedrock-gateway"
TARGET_NAME = "mantle"
TAGS = {"Project": "claude-code-gateway-inference-sample", "ManagedBy": "setup.py"}


# ── State ─────────────────────────────────────────────────────────────────────


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"region": None, "prefix": None, "resources": []}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def record(state: dict[str, Any], kind: str, **ids: Any) -> None:
    # Flush every created resource to disk immediately, so a crash still leaves a
    # cleanable record for cleanup.py.
    state["resources"].append({"kind": kind, **ids})
    save_state(state)
    print(f"  recorded {kind}: {ids}")


# ── Create ────────────────────────────────────────────────────────────────────


def create(region: str, prefix: str) -> None:
    state = load_state()
    if state["resources"]:
        # A recorded gateway means an earlier run stopped midway; resume by ensuring
        # the target exists rather than exiting.
        if any(r["kind"] == "gateway" for r in state["resources"]):
            print(f"{STATE_FILE.name} already lists a gateway; ensuring the target exists")
            create_target(state.get("region") or region, state)
            _write_outputs(state, state.get("region") or region)
            _print_config(state)
            return
        sys.exit(
            f"{STATE_FILE.name} already lists {len(state['resources'])} resources.\n"
            "Run `python cleanup.py` first, or move the state file aside."
        )
    state["region"] = region
    state["prefix"] = prefix
    save_state(state)

    session = boto3.Session(region_name=region)
    idp = session.client("cognito-idp")
    iam = session.client("iam")
    agc = session.client("bedrock-agentcore-control")
    account = session.client("sts").get_caller_identity()["Account"]

    # 1. user pool
    print("1. Cognito user pool")
    pool = idp.create_user_pool(PoolName=f"{prefix}-pool", UserPoolTags=TAGS)["UserPool"]
    pool_id = pool["Id"]
    record(state, "user_pool", user_pool_id=pool_id)

    # 2. resource server (defines the scope the client_credentials grant asks for)
    print("2. resource server + scope")
    idp.create_resource_server(
        UserPoolId=pool_id,
        Identifier=RESOURCE_SERVER_ID,
        Name=RESOURCE_SERVER_ID,
        Scopes=[{"ScopeName": SCOPE_NAME, "ScopeDescription": "Invoke models"}],
    )
    scope = f"{RESOURCE_SERVER_ID}/{SCOPE_NAME}"
    # No separate record: deleting the pool removes it.

    # 3. app client (the workload credential the agent will use)
    print("3. app client (client_credentials)")
    client = idp.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=f"{prefix}-client",
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=[scope],
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
    )["UserPoolClient"]
    client_id = client["ClientId"]
    record(state, "user_pool_client", user_pool_id=pool_id, client_id=client_id)

    # 4. domain (required before the /oauth2/token endpoint exists)
    print("4. user pool domain")
    domain = f"{prefix}-{secrets.token_hex(4)}"
    idp.create_user_pool_domain(Domain=domain, UserPoolId=pool_id)
    record(state, "user_pool_domain", domain=domain, user_pool_id=pool_id)

    discovery_url = (
        f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
    )

    # 5. gateway execution role (the egress credential)
    print("5. IAM role")
    role_name = f"{prefix}-gateway-role"
    role = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                        "Condition": {"StringEquals": {"aws:SourceAccount": account}},
                    }
                ],
            }
        ),
        Description="AgentCore Gateway egress role for the Claude Code inference sample",
        Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()],
    )["Role"]
    policy_name = f"{prefix}-bedrock-invoke"
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=policy_name,
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        # bedrock-mantle is its own IAM service namespace, not covered by
                        # bedrock:*. The inference connector runs model discovery at
                        # target-creation time, which needs ListModels -- without it
                        # CreateGatewayTarget succeeds and then the target goes FAILED.
                        # CreateInference is the inference call itself.
                        "Effect": "Allow",
                        "Action": [
                            "bedrock-mantle:ListModels",
                            "bedrock-mantle:CreateInference",
                        ],
                        # bedrock-mantle publishes no resource-level ARN format, so these
                        # two actions cannot be narrowed further. They are scoped instead
                        # by being the only mantle actions granted.
                        "Resource": "*",
                    },
                ],
            }
        ),
    )
    record(state, "iam_role", role_name=role_name, policy_name=policy_name)
    print("   waiting 10s for the role to propagate")
    time.sleep(10)

    # 6. gateway. protocolType is omitted deliberately: its only enum value is MCP,
    # which does not apply to an inference gateway.
    print("6. gateway (CUSTOM_JWT)")
    gw = agc.create_gateway(
        name=f"{prefix}-gateway",
        roleArn=role["Arn"],
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": discovery_url,
                "allowedClients": [client_id],
                # Enforce the scope the agent requests, so a token from this client
                # without it is rejected.
                "allowedScopes": [scope],
            }
        },
        description="Claude Code inference gateway sample",
        tags=TAGS,
    )
    gateway_id = gw["gatewayId"]
    record(state, "gateway", gateway_id=gateway_id)
    _wait_gateway_ready(agc, gateway_id)

    create_target(region, state, agc)

    _write_outputs(state, region)
    print("\nDone. Outputs written to", STATE_FILE.name)
    _print_config(state)


def _print_config(state: dict[str, Any]) -> None:
    # For running Claude Code locally against the gateway; deploy.py reads the same
    # values from the state file.
    out = state.get("outputs") or {}
    if not out.get("inference_url"):
        return
    print("\n# OAuth client-credentials configuration")
    print(f'export OAUTH_TOKEN_URL="{out["token_url"]}"')
    print(f'export OAUTH_CLIENT_ID="{out["client_id"]}"')
    print(f'export OAUTH_CLIENT_SECRET="{out["client_secret"]}"')
    print(f'export OAUTH_SCOPE="{out["scope"]}"')
    print()
    print("# AgentCore Gateway serves the Anthropic Messages format, so this is")
    print("# ANTHROPIC_BASE_URL -- not CLAUDE_CODE_USE_BEDROCK.")
    print(f'export ANTHROPIC_BASE_URL="{out["inference_url"]}"')
    print()
    print(f"# Pick a model with a target-qualified id, e.g. {TARGET_NAME}/anthropic.claude-sonnet-5")


def _write_outputs(state: dict[str, Any], region: str) -> None:
    # Everything here is derived from recorded resource ids, so a resumed run still
    # leaves a complete outputs block.
    session = boto3.Session(region_name=region)
    idp = session.client("cognito-idp")
    agc = session.client("bedrock-agentcore-control")

    def find(kind: str, key: str) -> Any:
        return next((r[key] for r in state["resources"] if r["kind"] == kind), None)

    pool_id = find("user_pool", "user_pool_id")
    client_id = find("user_pool_client", "client_id")
    domain = find("user_pool_domain", "domain")
    gateway_id = find("gateway", "gateway_id")

    gateway_url = None
    if gateway_id:
        gateway_url = agc.get_gateway(gatewayIdentifier=gateway_id).get("gatewayUrl")

    secret = None
    if pool_id and client_id:
        secret = idp.describe_user_pool_client(UserPoolId=pool_id, ClientId=client_id)[
            "UserPoolClient"
        ].get("ClientSecret")

    state["outputs"] = {
        "gateway_id": gateway_id,
        "gateway_url": gateway_url,
        "inference_url": f"{gateway_url.rstrip('/')}/inference" if gateway_url else None,
        "discovery_url": (
            f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
            "/.well-known/openid-configuration"
            if pool_id
            else None
        ),
        "token_url": (
            f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token" if domain else None
        ),
        "client_id": client_id,
        "client_secret": secret,
        "scope": f"{RESOURCE_SERVER_ID}/{SCOPE_NAME}",
    }
    save_state(state)


def _wait_gateway_ready(agc: Any, gateway_id: str) -> str | None:
    # Poll until the gateway reports READY, then return its URL. Waiting on status
    # rather than on the URL matters: create_gateway returns a URL while the gateway
    # is still CREATING, and CreateGatewayTarget fails against a gateway in that state.
    for _ in range(60):
        gw = agc.get_gateway(gatewayIdentifier=gateway_id)
        status = gw.get("status")
        if status == "READY":
            return gw.get("gatewayUrl")
        if status == "FAILED":
            print("   gateway FAILED:", gw.get("statusReasons"))
            return None
        time.sleep(3)
    print("   gateway did not reach READY in time; re-run setup.py once it does")
    return gw.get("gatewayUrl")


def create_target(region: str, state: dict[str, Any], agc: Any | None = None) -> None:
    # Idempotent on the target name, so a resumed setup run can call it safely.
    agc = agc or boto3.client("bedrock-agentcore-control", region_name=region)
    gateway_id = next(
        (r["gateway_id"] for r in state["resources"] if r["kind"] == "gateway"), None
    )
    if not gateway_id:
        sys.exit("No gateway recorded in state.")

    status = agc.get_gateway(gatewayIdentifier=gateway_id).get("status")
    if status != "READY":
        sys.exit(f"Gateway is {status}, not READY. Wait and re-run setup.py.")

    # A FAILED target still occupies its name, so clear it out before recreating.
    # Otherwise a permissions fix can never be retried without renaming the target.
    for t in agc.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", []):
        if t.get("name") != TARGET_NAME:
            continue
        if t.get("status") == "FAILED":
            print(f"   deleting FAILED target '{TARGET_NAME}' so it can be recreated")
            agc.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=t["targetId"])
            state["resources"] = [
                r for r in state["resources"] if r.get("target_id") != t["targetId"]
            ]
            save_state(state)
            time.sleep(3)
        else:
            print(f"   target '{TARGET_NAME}' already exists, skipping")
            return

    # The bedrock-mantle connector handles operations, model discovery, model-id
    # translation and path rewriting automatically. The gateway authenticates to
    # Bedrock with its own IAM role; no secret is stored.
    print(f"   creating target '{TARGET_NAME}' (bedrock-mantle connector)")
    config = {"inference": {"connector": {"source": {"connectorId": "bedrock-mantle"}}}}
    cred = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
    try:
        resp = agc.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=TARGET_NAME,
            targetConfiguration=config,
            credentialProviderConfigurations=cred,
        )
    except ClientError as exc:
        print(f"   FAILED to create target '{TARGET_NAME}': {exc}")
        return
    target_id = resp.get("targetId", "")
    # A failed target is still recorded, because a FAILED target occupies its name and
    # must be deleted before the name can be reused.
    record(state, "gateway_target", gateway_id=gateway_id, target_id=target_id, name=TARGET_NAME)
    for _ in range(40):
        status = agc.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id).get(
            "status"
        )
        if status == "READY":
            print(f"   target '{TARGET_NAME}' READY")
            return
        if status == "FAILED":
            detail = agc.get_gateway_target(
                gatewayIdentifier=gateway_id, targetId=target_id
            ).get("statusReasons")
            print(f"   target '{TARGET_NAME}' FAILED: {detail}")
            return
        time.sleep(3)
    print(f"   target '{TARGET_NAME}' still provisioning; re-run setup.py to check")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--prefix", default="claude-code-gw")
    args = ap.parse_args()
    create(args.region, args.prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
