"""Create the AgentCore Gateway that the consent portal will front.

Creates, idempotently:
  1. The gateway service role (AmazonBedrockAgentCoreGatewayRole-<GATEWAY_NAME>),
     unless GATEWAY_SERVICE_ROLE_ARN is set. It needs the AgentCore Identity
     token operations, because the gateway itself performs the outbound 3LO
     exchange against GitHub on the user's behalf, plus read access to the
     identity service's own OAuth secrets and CloudWatch Logs.
  2. The gateway: protocolType MCP, inbound authorizerType CUSTOM_JWT over
     your IdP's OIDC discovery URL.

Two details that are load-bearing and easy to get wrong:

  * supportedVersions must include "2025-11-25". That MCP protocol version is
    what enables URL-mode elicitation, which is how the gateway asks an
    unconsented user to authorize a 3LO target. On older versions only, an
    AUTHORIZATION_CODE target has no way to prompt.

  * The gateway's authorizer and the consent portal's IdP credential provider
    must reference the SAME OIDC issuer. CreateConsentPortal validates this at
    create time, so a mismatch is rejected in step 02 rather than at login.
    Both read IDP_DISCOVERY_URL from .env, so they agree by construction.

On a re-run the authorizer config is reconciled if it drifted (a rotated
GATEWAY_CLIENT_ID or a discovery URL edited by hand) — both of those surface
at runtime as an opaque 401 during MCP initialization.

Writes to .env: GATEWAY_ID, GATEWAY_URL, GATEWAY_MCP_URL,
GATEWAY_SERVICE_ROLE_ARN.

If the consent portal later answers `login_unavailable` on GET /login, re-run
this with --reapply-authorizer. Re-sending an unchanged authorizer makes the
portal re-resolve it; a portal created before the gateway's authorizer was
resolvable otherwise stays broken with a deliberately non-diagnostic error.

Run from the sample root:
    python deploy/01_create_gateway.py --entra
    python deploy/01_create_gateway.py --okta
    python deploy/01_create_gateway.py --okta --reapply-authorizer
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    check_boto_version,
    control_client,
    discovery_url,
    find_gateway_by_name,
    gateway_name,
    gateway_role_name,
    load_env,
    must_env,
    save_env,
    select_idp_with_args,
)

# Newest first. 2025-11-25 enables URL-mode elicitation (the authorization
# code flow); the older two are listed so a client that cannot speak it still
# connects instead of failing version negotiation.
MCP_SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"]

ROLE_POLICY_NAME = "AgentCoreGatewayOutboundAuth"


def allowed_audiences(idp: dict, audience: str) -> list[str]:
    """The audiences the gateway accepts on inbound tokens.

    Entra with requestedAccessTokenVersion 2 sets aud to the bare application
    id, but the identifier-URI form is accepted too by some client libraries,
    so both are listed. Okta stamps aud from the authorization server's
    `audiences` value verbatim, and api://<that> would be wrong.
    """
    if idp["name"] == "entra":
        return [audience, f"api://{audience}"]
    return [audience]


def ensure_service_role(iam, role_name: str, account_id: str, region: str) -> str:
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                # Confused-deputy guards.
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
                # The gateway calls these to obtain the user's GitHub token
                # from the token vault (or to mint the authorization URL when
                # the user has not consented yet).
                "Sid": "AgentCoreIdentityOutbound",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                    "bedrock-agentcore:GetResourceOauth2Token",
                    "bedrock-agentcore:CompleteResourceTokenAuth",
                ],
                "Resource": "*",
            },
            {
                "Sid": "ReadAgentCoreOauthSecrets",
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": [
                    (f"arn:aws:secretsmanager:{region}:{account_id}:secret:bedrock-agentcore-identity!default/oauth2/*")
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
                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/gateway*",
            },
        ],
    }

    created = False
    try:
        role_arn = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="AgentCore Gateway service role - consent portal + GitHub MCP sample",
        )["Role"]["Arn"]
        print(f"  ✓ Created IAM role: {role_name}")
        created = True
    except ClientError as e:
        if e.response["Error"]["Code"] not in {"EntityAlreadyExists", "EntityAlreadyExistsException"}:
            raise
        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        print(f"  • IAM role already exists: {role_name}")

    # Unconditional: put_role_policy is an upsert, so a re-run repairs drift.
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=ROLE_POLICY_NAME,
        PolicyDocument=json.dumps(permission_policy),
    )
    print(f"  ✓ Attached inline policy: {ROLE_POLICY_NAME}")

    if created:
        print("  ⏳ Waiting 10s for IAM role propagation…")
        time.sleep(10)
    return role_arn


def wait_for_ready(control, gateway_id: str) -> None:
    """CreateGatewayTarget is rejected while the gateway is still CREATING."""
    for attempt in range(40):
        gw = control.get_gateway(gatewayIdentifier=gateway_id)
        status = gw.get("status", "UNKNOWN")
        if status == "READY":
            if attempt:
                print(f"  ✓ Gateway status: READY (after {attempt * 3}s)")
            return
        if status in {"FAILED", "DELETING", "DELETED"}:
            reason = gw.get("statusReasons") or gw.get("failureReason") or "n/a"
            print(f"ERROR: gateway entered terminal state {status}. Reason: {reason}", file=sys.stderr)
            sys.exit(1)
        if attempt == 0:
            print(f"  ⏳ Waiting for gateway to reach READY (current: {status})…")
        time.sleep(3)
    print("ERROR: gateway did not reach READY within 2 minutes.", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    idp, args = select_idp_with_args(
        __doc__,
        [
            (
                ["--reapply-authorizer"],
                {
                    "action": "store_true",
                    "help": "Re-send the authorizer even if unchanged. Use this when the "
                    "consent portal answers login_unavailable — it makes the portal "
                    "re-resolve the gateway's authorizer.",
                },
            )
        ],
    )
    reapply = args.reapply_authorizer
    check_boto_version()
    load_env()

    name = gateway_name(idp)
    disco = discovery_url(idp)
    audience = must_env("IDP_AUDIENCE")
    audiences = allowed_audiences(idp, audience)

    control, region = control_client()
    iam = boto3.client("iam", region_name=region)
    account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]

    print(f"--- Step 1: gateway service role ({idp['displayName']}) ---")
    import os

    role_arn = os.environ.get("GATEWAY_SERVICE_ROLE_ARN", "").strip()
    if role_arn:
        print(f"  • Using GATEWAY_SERVICE_ROLE_ARN from .env: {role_arn}")
    else:
        role_arn = ensure_service_role(iam, gateway_role_name(idp), account_id, region)

    authorizer_config = {"customJWTAuthorizer": {"discoveryUrl": disco, "allowedAudience": audiences}}
    protocol_config = {
        "mcp": {
            "supportedVersions": MCP_SUPPORTED_VERSIONS,
            "searchType": "SEMANTIC",
            "sessionConfiguration": {"sessionTimeoutInSeconds": 3600},
            "streamingConfiguration": {"enableResponseStreaming": True},
        }
    }

    print(f"\n--- Step 2: gateway ({name}) ---")
    print(f"  discoveryUrl:    {disco}")
    print(f"  allowedAudience: {audiences}")
    existing = find_gateway_by_name(control, name)
    if existing:
        gateway_id = existing["gatewayId"]
        gateway_url = existing.get("gatewayUrl") or ""
        print(f"  • Gateway already exists. ID: {gateway_id}")

        # Reconcile authorizer drift. Both known causes (a rotated client id,
        # or a hand-edited discovery URL) surface at runtime as a 401 during
        # MCP initialization, which is very hard to attribute.
        #
        # Read the authorizer from GetGateway, not from the ListGateways
        # summary: the summary carries only authorizerType, never
        # authorizerConfiguration, so comparing against it reports drift on
        # every run and rewrites a gateway that was already correct.
        full = control.get_gateway(gatewayIdentifier=gateway_id)
        current = (full.get("authorizerConfiguration") or {}).get("customJWTAuthorizer", {}) or {}
        drifted = current.get("discoveryUrl") != disco or set(current.get("allowedAudience") or []) != set(audiences)
        if drifted or reapply:
            if drifted:
                print("  • Authorizer drift detected — updating.")
                print(f"      discoveryUrl was: {current.get('discoveryUrl') or '(unset)'}")
                print(f"      allowedAudience was: {sorted(current.get('allowedAudience') or [])}")
            else:
                print("  • --reapply-authorizer: re-sending an unchanged authorizer.")
                print("      This is the fix for a portal answering login_unavailable —")
                print("      it makes the portal re-resolve the gateway's authorizer.")
            control.update_gateway(
                gatewayIdentifier=gateway_id,
                name=name,
                roleArn=role_arn,
                protocolType="MCP",
                protocolConfiguration=protocol_config,
                authorizerType="CUSTOM_JWT",
                authorizerConfiguration=authorizer_config,
            )
            print("  ✓ Gateway authorizer updated.")
    else:
        response = control.create_gateway(
            name=name,
            roleArn=role_arn,
            description="Consent portal + GitHub MCP server sample",
            protocolType="MCP",
            protocolConfiguration=protocol_config,
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration=authorizer_config,
            # DEBUG surfaces the underlying cause in tool-call errors, which
            # is what makes the -32042 elicitation legible in the agent logs.
            exceptionLevel="DEBUG",
        )
        gateway_id = response["gatewayId"]
        gateway_url = response.get("gatewayUrl", "")
        print(f"  ✓ Created. ID: {gateway_id}")

    wait_for_ready(control, gateway_id)

    if not gateway_url:
        gateway_url = control.get_gateway(gatewayIdentifier=gateway_id).get("gatewayUrl", "")
    mcp_url = gateway_url if gateway_url.endswith("/mcp") else gateway_url.rstrip("/") + "/mcp"

    save_env(
        GATEWAY_NAME=name,
        GATEWAY_ID=gateway_id,
        GATEWAY_URL=gateway_url,
        GATEWAY_MCP_URL=mcp_url,
        GATEWAY_SERVICE_ROLE_ARN=role_arn,
    )
    print(f"\n  MCP endpoint: {mcp_url}")
    print("  Saved to .env: GATEWAY_NAME, GATEWAY_ID, GATEWAY_URL, GATEWAY_MCP_URL,")
    print("    GATEWAY_SERVICE_ROLE_ARN")
    print()
    print(f"Next: python deploy/02_create_portal.py --{idp['name']}")


if __name__ == "__main__":
    main()
