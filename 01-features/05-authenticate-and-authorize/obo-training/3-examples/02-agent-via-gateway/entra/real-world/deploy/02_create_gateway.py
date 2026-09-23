"""
Create the AgentCore Gateway and its Microsoft Graph OpenAPI target.

Creates:
  - One Gateway resource named $GATEWAY_NAME, with inbound CUSTOM_JWT auth
    using Entra's OIDC discovery and `allowedAudience = GATEWAY_CLIENT_ID`.
  - One target on that Gateway:
      * targetConfiguration: OpenAPI (inline payload from gateway/graph_openapi.json)
      * credentialProviderConfigurations: OAuth + the gateway-actor provider,
        with grantType=TOKEN_EXCHANGE and customParameters
        {"requested_token_use": "on_behalf_of"} so Entra performs OBO #2.

Side effects:
  - Writes GATEWAY_MCP_URL back into the project's .env.

Run:
    python deploy/02_create_gateway.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# The Gateway target outbound auth's grantType=TOKEN_EXCHANGE enum was added
# in boto3/botocore 1.43.2. Earlier versions reject the request client-side
# with a confusing ParamValidationError. Fail fast with a clearer message.
_MIN_BOTO_VERSION = (1, 43, 2)
_INSTALLED_BOTO = tuple(int(x) for x in boto3.__version__.split(".")[:3])
if _INSTALLED_BOTO < _MIN_BOTO_VERSION:
    print(
        f"ERROR: boto3 {boto3.__version__} is too old.\n"
        f"       This script needs boto3 >= {'.'.join(map(str, _MIN_BOTO_VERSION))} "
        f"(the enum for grantType=TOKEN_EXCHANGE on Gateway targets was added there).\n"
        f"       Activate the venv created in step 3 and re-run, or:\n"
        f"           pip install -U 'boto3>=1.43.2' 'botocore>=1.43.2'",
        file=sys.stderr,
    )
    sys.exit(1)


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def find_gateway_by_name(client, name: str) -> dict | None:
    paginator = client.get_paginator("list_gateways")
    for page in paginator.paginate():
        for gw in page.get("items", []):
            if gw.get("name") == name:
                return gw
    return None


def find_target_by_name(client, gateway_id: str, name: str) -> dict | None:
    paginator = client.get_paginator("list_gateway_targets")
    for page in paginator.paginate(gatewayIdentifier=gateway_id):
        for target in page.get("items", []):
            if target.get("name") == name:
                return target
    return None


def get_gateway_provider_arn(ac_control, name: str) -> str:
    """Look up the Gateway-actor credential provider ARN by name.

    Created in step 01. We re-fetch rather than asking the user to pass it
    around so the deploy flow stays linear.
    """
    resp = ac_control.get_oauth2_credential_provider(name=name)
    return resp["credentialProviderArn"]


def find_role_arn_by_name(role_name: str, region: str) -> str | None:
    """Best-effort lookup of the Gateway service role ARN."""
    iam = boto3.client("iam")
    try:
        resp = iam.get_role(RoleName=role_name)
        return resp["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] in {"NoSuchEntity", "NoSuchEntityException"}:
            return None
        raise


def create_gateway_service_role(role_name: str, account_id: str, region: str) -> str:
    """Create the Gateway service role with trust + OBO permissions.

    The role is assumable by bedrock-agentcore.amazonaws.com and has:
      - AgentCore Identity OBO permissions (used for OBO #2 inside the Gateway)
      - Access to the AgentCore-managed OAuth secrets in Secrets Manager
      - CloudWatch Logs permissions (Gateway writes logs there)

    Returns the role ARN. Idempotent: if the role exists, just re-attaches
    the inline policy and returns its ARN.
    """
    iam = boto3.client("iam")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                # Recommended confused-deputy guards
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }

    permission_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AgentCoreIdentityOBO",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetResourceOauth2Token",
                ],
                "Resource": "*",
            },
            {
                "Sid": "ReadAgentCoreOauthSecrets",
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": [
                    f"arn:aws:secretsmanager:{region}:{account_id}:secret:bedrock-agentcore-identity!default/oauth2/*"
                ],
            },
            {
                "Sid": "CloudWatchLogs",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                "Resource": (f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/gateway*"),
            },
        ],
    }

    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="AgentCore Gateway service role - OBO Use Case 2 (Entra)",
        )
        role_arn = resp["Role"]["Arn"]
        print(f"  ✓ Created IAM role: {role_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
            print(f"  • IAM role already exists: {role_name}")
        else:
            raise

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="AgentCoreGatewayOboPermissions",
        PolicyDocument=json.dumps(permission_policy),
    )
    print("  ✓ Attached inline policy: AgentCoreGatewayOboPermissions")

    return role_arn


def upsert_env_value(env_path: Path, key: str, value: str) -> None:
    """Replace `KEY=…` line in .env with `KEY=value`, or append it.

    Keeps the file lossless if the var is already set.
    """
    if not env_path.exists():
        env_path.write_text(f"{key}={value}\n")
        return

    lines = env_path.read_text().splitlines()
    pattern = f"{key}="
    found = False
    for i, line in enumerate(lines):
        if line.startswith(pattern):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")


def main() -> None:
    real_world_root = Path(__file__).resolve().parent.parent
    load_dotenv(real_world_root / ".env")
    region = os.environ.get("AWS_REGION", "us-west-2")

    tenant_id = must_env("TENANT_ID")
    gateway_client_id = must_env("GATEWAY_CLIENT_ID")
    gateway_name = must_env("GATEWAY_NAME")
    gateway_obo_provider_name = must_env("GATEWAY_OBO_PROVIDER_NAME")

    env_path = real_world_root / ".env"

    # The Gateway service role: use the env-var override if set. Otherwise
    # look up (or create) a conventionally-named role. This keeps setup
    # single-command — no manual IAM configuration step.
    gateway_service_role_arn = os.environ.get("GATEWAY_SERVICE_ROLE_ARN")
    conventional_role_name = f"AmazonBedrockAgentCoreGatewayRole-{gateway_name}"

    if gateway_service_role_arn:
        print(f"• Using Gateway service role from env: {gateway_service_role_arn}")
    else:
        guessed_arn = find_role_arn_by_name(conventional_role_name, region)
        if guessed_arn:
            gateway_service_role_arn = guessed_arn
            print(f"• Reusing existing Gateway service role: {guessed_arn}")
            # Re-attach the policy in case it drifted.
            account_id = boto3.client("sts").get_caller_identity()["Account"]
            create_gateway_service_role(conventional_role_name, account_id, region)
        else:
            print(f"• Creating Gateway service role: {conventional_role_name}")
            account_id = boto3.client("sts").get_caller_identity()["Account"]
            gateway_service_role_arn = create_gateway_service_role(conventional_role_name, account_id, region)
            print("  ⏳ Waiting 10s for IAM role propagation…")
            import time

            time.sleep(10)

        # Persist for teardown and future re-runs
        upsert_env_value(env_path, "GATEWAY_SERVICE_ROLE_ARN", gateway_service_role_arn)

    ac_control = boto3.client("bedrock-agentcore-control", region_name=region)

    # 1) Look up the gateway-actor credential provider (created in step 01).
    print(f"• Looking up Gateway-actor credential provider: {gateway_obo_provider_name}")
    try:
        gateway_provider_arn = get_gateway_provider_arn(ac_control, gateway_obo_provider_name)
    except ClientError as e:
        if e.response["Error"]["Code"] in {
            "ResourceNotFoundException",
            "NotFoundException",
        }:
            print(
                f"ERROR: Credential provider '{gateway_obo_provider_name}' not found. Run step 01 first.",
                file=sys.stderr,
            )
            sys.exit(1)
        raise
    print(f"  ARN: {gateway_provider_arn}")

    # 2) Create or look up the Gateway.
    #
    # Discovery URL: use the v2.0 endpoint. IMPORTANT: this only works when
    # BOTH the AgentApp AND the GatewayApp have
    #     "api.requestedAccessTokenVersion": 2
    # in their manifests. Without that, Entra issues v1-style access tokens
    # (iss = https://sts.windows.net/<tenant>/) and the Gateway's v2 iss
    # check fails with:
    #     "Claim 'iss' value mismatch with configuration."
    #
    # The 00_create_entra_apps.py automation script sets this flag on both
    # apps. If you created the apps manually, run:
    #     az ad app update --id <AGENT_CLIENT_ID>   --set 'api.requestedAccessTokenVersion=2'
    #     az ad app update --id <GATEWAY_CLIENT_ID> --set 'api.requestedAccessTokenVersion=2'
    discovery_url = f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"
    authorizer_config = {
        "customJWTAuthorizer": {
            "discoveryUrl": discovery_url,
            "allowedAudience": [
                gateway_client_id,
                f"api://{gateway_client_id}",
            ],
        }
    }
    print(f"• Creating Gateway: {gateway_name}")
    existing_gw = find_gateway_by_name(ac_control, gateway_name)
    if existing_gw:
        gateway_id = existing_gw["gatewayId"]
        gateway_url = existing_gw.get("gatewayUrl") or ""
        print(f"  • Gateway already exists. ID: {gateway_id}")

        # Re-apply the authorizer config in case it drifted. The two drift
        # cases we've hit in practice:
        #   1. discoveryUrl set to /v2.0/ variant vs the plain variant
        #   2. allowedAudience list stale after .env's GATEWAY_CLIENT_ID
        #      was rotated (e.g. after 00_create_entra_apps.py --rotate).
        # Both surface at runtime as Gateway 401/403 during MCP init.
        try:
            existing_config = existing_gw.get("authorizerConfiguration") or {}
            existing_authorizer = existing_config.get("customJWTAuthorizer", {}) or {}
            existing_discovery = existing_authorizer.get("discoveryUrl", "")
            existing_audience = set(existing_authorizer.get("allowedAudience", []) or [])
            desired_audience = set(authorizer_config["customJWTAuthorizer"]["allowedAudience"])
            if existing_discovery != discovery_url or existing_audience != desired_audience:
                print("  • Authorizer drift detected — updating.")
                if existing_discovery != discovery_url:
                    print(f"      discoveryUrl was: {existing_discovery or '(unset)'}")
                    print(f"      discoveryUrl now: {discovery_url}")
                if existing_audience != desired_audience:
                    print(f"      allowedAudience was: {sorted(existing_audience) or '(unset)'}")
                    print(f"      allowedAudience now: {sorted(desired_audience)}")
                ac_control.update_gateway(
                    gatewayIdentifier=gateway_id,
                    name=gateway_name,
                    roleArn=gateway_service_role_arn,
                    protocolType="MCP",
                    authorizerType="CUSTOM_JWT",
                    authorizerConfiguration=authorizer_config,
                )
                print("  ✓ Gateway authorizer updated.")
        except ClientError as e:
            # If update_gateway isn't supported in your boto3, just warn.
            print(
                f"  ⚠ Could not reconcile authorizer config: {e}. "
                f"Consider `python deploy/teardown.py` + re-run if the "
                f"'iss mismatch' or 403 error appears at runtime.",
                file=sys.stderr,
            )
    else:
        create_resp = ac_control.create_gateway(
            name=gateway_name,
            roleArn=gateway_service_role_arn,
            protocolType="MCP",
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration=authorizer_config,
        )
        gateway_id = create_resp["gatewayId"]
        gateway_url = create_resp.get("gatewayUrl", "")
        print(f"  ✓ Created. ID: {gateway_id}")

    if gateway_url:
        print(f"  Gateway URL: {gateway_url}")

    # 2b) Wait for the Gateway to reach READY. CreateGatewayTarget rejects
    #     the call while the Gateway is still CREATING. Poll every 3s for
    #     up to 2 minutes.
    import time

    for attempt in range(40):
        gw = ac_control.get_gateway(gatewayIdentifier=gateway_id)
        status = gw.get("status", "UNKNOWN")
        if status == "READY":
            if attempt > 0:
                print(f"  ✓ Gateway status: READY (after {attempt * 3}s)")
            break
        if status in {"FAILED", "DELETING", "DELETED"}:
            print(
                f"ERROR: Gateway entered terminal state {status}. "
                f"Reason: {gw.get('statusReasons', gw.get('failureReason', 'n/a'))}",
                file=sys.stderr,
            )
            sys.exit(1)
        if attempt == 0:
            print(f"  ⏳ Waiting for Gateway to reach READY (current: {status})…")
        time.sleep(3)
    else:
        print(
            "ERROR: Gateway did not reach READY within 2 minutes. Check the console; re-run this script once it does.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 3) Load the OpenAPI spec.
    spec_path = real_world_root / "gateway" / "graph_openapi.json"
    if not spec_path.exists():
        print(f"ERROR: OpenAPI spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)
    spec = json.loads(spec_path.read_text())

    # 4) Create or look up the target.
    target_name = "microsoft-graph-obo"
    print(f"• Creating target: {target_name}")
    existing_target = find_target_by_name(ac_control, gateway_id, target_name)
    if existing_target:
        target_id = existing_target["targetId"]
        print(f"  • Target already exists. ID: {target_id}")
    else:
        target_resp = ac_control.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=target_name,
            description="Microsoft Graph /me with OBO outbound auth (OBO #2 in the chain).",
            targetConfiguration={
                "mcp": {
                    "openApiSchema": {
                        "inlinePayload": json.dumps(spec),
                    }
                }
            },
            credentialProviderConfigurations=[
                {
                    "credentialProviderType": "OAUTH",
                    "credentialProvider": {
                        "oauthCredentialProvider": {
                            "providerArn": gateway_provider_arn,
                            "scopes": ["https://graph.microsoft.com/.default"],
                            "grantType": "TOKEN_EXCHANGE",
                            "customParameters": {
                                "requested_token_use": "on_behalf_of",
                            },
                        }
                    },
                }
            ],
        )
        target_id = target_resp["targetId"]
        print(f"  ✓ Created. ID: {target_id}")

    # 5) Persist GATEWAY_MCP_URL into .env so subsequent steps can pick it up.
    if gateway_url:
        # The Gateway's MCP endpoint is usually `${gateway_url}/mcp`. Some
        # SDKs accept the bare gateway URL and append /mcp internally — to
        # be safe we surface both via the same env var (we use the /mcp form
        # in the agent code).
        mcp_url = gateway_url.rstrip("/") + "/mcp" if not gateway_url.endswith("/mcp") else gateway_url
        upsert_env_value(env_path, "GATEWAY_MCP_URL", mcp_url)
        print(f"\n✓ Wrote GATEWAY_MCP_URL to {env_path.relative_to(real_world_root.parent)}:")
        print(f"  GATEWAY_MCP_URL={mcp_url}")
    else:
        print("\n⚠ Gateway URL not returned in API response. Look it up later via:")
        print(f"    aws bedrock-agentcore-control get-gateway --gateway-identifier {gateway_id}")
        print("  and set GATEWAY_MCP_URL=<gateway-url>/mcp in .env manually.")

    print()
    print(
        "Next step: python deploy/03_patch_agentcore_json.py "
        "(after `agentcore create --defaults` has scaffolded the project)"
    )


if __name__ == "__main__":
    main()
