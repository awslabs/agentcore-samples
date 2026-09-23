"""
Create the AgentCore Gateway and its downstream OpenAPI target (Okta variant).

Creates:
  - The Gateway service role (auto-created if not present under the
    conventional name AmazonBedrockAgentCoreGatewayRole-<GATEWAY_NAME>,
    unless GATEWAY_SERVICE_ROLE_ARN is set in .env).
  - One Gateway resource named $GATEWAY_NAME, with inbound CUSTOM_JWT auth
    using Okta's OIDC discovery and `allowedAudience = [OKTA_AUDIENCE]`
    (typically `api://default`).
  - One target on that Gateway:
      * targetConfiguration: OpenAPI (inline payload from
        gateway/downstream_openapi.json).
      * credentialProviderConfigurations: OAuth + the gateway-actor provider,
        with grantType=TOKEN_EXCHANGE and
        customParameters={"subject_token_type": "..."} so Okta performs
        OBO #2.

Side effects:
  - Writes GATEWAY_MCP_URL and GATEWAY_SERVICE_ROLE_ARN back into the
    project's .env.

Key Okta-vs-Entra differences from UC2 Entra's version of this script:
  - Discovery URL is Okta-flavored: /oauth2/<auth-server>/.well-known/openid-configuration
  - allowedAudience is a single string (OKTA_AUDIENCE, typically api://default)
    — NOT a list of client IDs. Okta mints every token from the default
    authorization server with the same `aud`; the two hops are differentiated
    by SCOPE (gateway.access vs downstream.access), not audience.
  - Target customParameters carry `subject_token_type` (required by RFC 8693
    Token Exchange), NOT `requested_token_use` (which was Entra-specific).

Run:
    python deploy/02_create_gateway.py
"""

from __future__ import annotations

import json
import os
import sys
import time
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
            Description="AgentCore Gateway service role - OBO Use Case 2 (Okta)",
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
    """Replace `KEY=…` line in .env with `KEY=value`, or append it."""
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

    okta_domain = must_env("OKTA_DOMAIN")
    okta_auth_server_id = must_env("OKTA_AUTH_SERVER_ID")
    okta_audience = must_env("OKTA_AUDIENCE")
    gateway_name = must_env("GATEWAY_NAME")
    gateway_obo_provider_name = must_env("GATEWAY_OBO_PROVIDER_NAME")
    downstream_scope = must_env("DOWNSTREAM_SCOPE")

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
            time.sleep(10)

        # Persist for teardown and future re-runs.
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
    # Discovery URL: Okta's per-auth-server OIDC discovery. NOT the tenant
    # root — Okta doesn't serve /.well-known/openid-configuration at the
    # apex domain.
    #
    # allowedAudience: Okta mints every token from the default authorization
    # server with `aud = api://default` (or whatever OKTA_AUDIENCE resolves
    # to for a custom server). Both Runtime and Gateway configure the same
    # allowedAudience; the two hops are differentiated by SCOPE (agent.access
    # vs gateway.access), not by audience.
    discovery_url = f"https://{okta_domain}/oauth2/{okta_auth_server_id}/.well-known/openid-configuration"
    authorizer_config = {
        "customJWTAuthorizer": {
            "discoveryUrl": discovery_url,
            "allowedAudience": [okta_audience],
        }
    }
    print(f"• Creating Gateway: {gateway_name}")
    existing_gw = find_gateway_by_name(ac_control, gateway_name)
    if existing_gw:
        gateway_id = existing_gw["gatewayId"]
        gateway_url = existing_gw.get("gatewayUrl") or ""
        print(f"  • Gateway already exists. ID: {gateway_id}")

        # Re-apply the authorizer config in case it drifted (e.g. someone
        # ran 00_create_okta_apps.py against a different tenant, or the
        # audience env-var changed). Both surface at runtime as a 401 on
        # MCP init.
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
            print(
                f"  ⚠ Could not reconcile authorizer config: {e}. "
                f"Consider `python deploy/teardown.py` + re-run if the "
                f"401 error appears at runtime.",
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
    spec_path = real_world_root / "gateway" / "downstream_openapi.json"
    if not spec_path.exists():
        print(f"ERROR: OpenAPI spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)
    spec = json.loads(spec_path.read_text())

    # 4) Create or update the target.
    #
    # Target-level customParameters carry the Okta Token Exchange form
    # parameters. Unlike the boto3 `get_resource_oauth2_token` call (which
    # accepts `audiences=[...]` as a first-class kwarg), the Gateway target
    # only exposes customParameters — so audience goes here too.
    #
    #   subject_token_type — REQUIRED by RFC 8693. Okta rejects Token
    #     Exchange requests without this. AgentCore Identity does NOT
    #     auto-add it for CustomOauth2 providers.
    #   audience — Okta uses the auth server's default audience if omitted,
    #     but some tenants (and all custom auth servers with multiple
    #     audiences) require it explicitly. Sending it defensively costs
    #     nothing and prevents opaque "internal error" from Gateway when
    #     Okta silently rejects the exchange.
    target_name = "downstream-echo-obo"
    target_desc = (
        "Mock downstream API (httpbin.org/anything) with OBO outbound "
        "auth. This is OBO #2 in the chain — Okta Token Exchange."
    )
    target_config = {
        "mcp": {
            "openApiSchema": {
                "inlinePayload": json.dumps(spec),
            }
        }
    }
    cred_config = [
        {
            "credentialProviderType": "OAUTH",
            "credentialProvider": {
                "oauthCredentialProvider": {
                    "providerArn": gateway_provider_arn,
                    "scopes": [downstream_scope],
                    "grantType": "TOKEN_EXCHANGE",
                    "customParameters": {
                        "subject_token_type": ("urn:ietf:params:oauth:token-type:access_token"),
                        "audience": okta_audience,
                    },
                }
            },
        }
    ]

    print(f"• Creating target: {target_name}")
    existing_target = find_target_by_name(ac_control, gateway_id, target_name)
    if existing_target:
        target_id = existing_target["targetId"]
        print(f"  • Target already exists. ID: {target_id}")
        # update_gateway_target has been observed to silently drop
        # customParameters changes on some API versions — the call
        # succeeds but the target's customParameters stays at the
        # original values. That surfaces at runtime as opaque
        # "internal error" from Gateway because Okta rejects the OBO #2
        # request with missing_token_request_parameter (usually missing
        # audience). To avoid this trap: delete the existing target and
        # recreate. The target has no persistent state we need to
        # preserve, so recreation is safe.
        print("  • Deleting and recreating to guarantee fresh config…")
        try:
            ac_control.delete_gateway_target(
                gatewayIdentifier=gateway_id,
                targetId=target_id,
            )
        except ClientError as e:
            print(f"  ⚠ Could not delete existing target: {e}", file=sys.stderr)
            print("    Manual fallback:")
            print(
                f"      aws bedrock-agentcore-control delete-gateway-target "
                f"--gateway-identifier {gateway_id} --target-id {target_id} "
                f"--region {region}"
            )
            sys.exit(1)
        # Wait briefly for async deletion to complete.
        for _ in range(20):
            if not find_target_by_name(ac_control, gateway_id, target_name):
                break
            time.sleep(1)
        else:
            print(
                "  ⚠ Target still present after 20s. Re-run this script in a moment.",
                file=sys.stderr,
            )
            sys.exit(1)
        print("  ✓ Old target deleted.")

    target_resp = ac_control.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=target_name,
        description=target_desc,
        targetConfiguration=target_config,
        credentialProviderConfigurations=cred_config,
    )
    target_id = target_resp["targetId"]
    print(f"  ✓ Created. ID: {target_id}")

    # 5) Persist GATEWAY_MCP_URL into .env so subsequent steps can pick it up.
    if gateway_url:
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
