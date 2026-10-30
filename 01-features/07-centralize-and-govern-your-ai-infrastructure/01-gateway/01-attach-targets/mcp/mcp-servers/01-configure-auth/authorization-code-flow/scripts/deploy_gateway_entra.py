"""Create an AgentCore gateway with OIDC (JWT) inbound auth (entra/gateway.md).

The consent portal requires a gateway whose inbound authentication type is JWT,
because the portal delegates its authorization decision to that authorizer. This
script creates one, with the IdP named by the required --<idp> flag.

Differs from deploy_gateway.py, which does Cognito inbound auth for
github/README.md, in the authorizer only:

    allowedAudience         the resource app GUID -- with v2.0 tokens Entra sets
                            aud to the application id, never to api://<id>
    allowedScopes           the SHORT scope the gateway matches in the token's
                            scp claim
    advertisedScopeMapping  short -> fully qualified, for MCP clients reading the
                            gateway's RFC 9728 metadata. The consent portal is not
                            an MCP client and never reads this.

The protocol configuration is shared with deploy_gateway.py via
mcp_config.mcp_protocol_configuration() so the two gateways cannot drift.

Requires (see idp_config.py for the full contract):
    ENTRA_DISCOVERY_URL, ENTRA_RESOURCE

Usage:
    uv run python scripts/deploy_gateway_entra.py --entra
"""

import time

import boto3
from gateway_admin import GatewayBoto3Client
from idp_config import (
    advertised_scope_mapping_is_identity,
    audience,
    discovery_url,
    gateway_advertised_scope_mapping,
    gateway_name,
    identifier_uri_hint_lines,
    select_idp,
)
from mcp_config import load_env, mcp_protocol_configuration, save_env

TERMINAL_STATUSES = ("READY", "FAILED", "CREATE_FAILED")


def find_gateway(control, name):
    """Return the gatewayId of an existing gateway with this name, or None.

    CreateGateway is not idempotent -- a second run raises ConflictException. A
    paged name scan lets a re-run reuse the gateway instead of failing, matching
    deploy_portal.py's find-by-name behaviour.
    """
    paginator = control.get_paginator("list_gateways")
    for page in paginator.paginate():
        for gw in page.get("items", []):
            if gw.get("name") == name:
                return gw["gatewayId"]
    return None


def main():
    idp = select_idp(__doc__)
    load_env()

    gw_name = gateway_name(idp)
    discovery = discovery_url(idp)
    aud = audience(idp)
    scopes = idp["gatewayScopes"]
    advertised = gateway_advertised_scope_mapping(idp)
    advertise = not advertised_scope_mapping_is_identity(idp)

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    print("--- Step 1: gateway IAM role ---")
    role_arn = admin.create_gateway_role(gw_name, oauth_targets=True)
    print(f"  arn: {role_arn}")

    print(f"\n--- Step 2: gateway ({idp['displayName']} inbound auth) ---")
    print(f"  name:      {gw_name}")
    print(f"  discovery: {discovery}")
    print(f"  audience:  {aud}")
    print(f"  scopes:    {', '.join(scopes)}")
    if advertise:
        for short, qualified in advertised.items():
            print(f"  advertises {short} as {qualified}")

    existing_id = find_gateway(control, gw_name)
    if existing_id:
        gw = control.get_gateway(gatewayIdentifier=existing_id)
        gateway_id = gw["gatewayId"]
        gw_url = gw["gatewayUrl"]
        print(f"\n  Gateway already exists: {gateway_id} ({gw['status']}) -- reusing")
    else:
        authorizer = {
            "discoveryUrl": discovery,
            "allowedAudience": [aud],
            "allowedScopes": scopes,
        }
        # Omitted when it maps every scope to itself (Okta): sending an identity
        # map is a no-op, and only MCP clients ever read it anyway.
        if advertise:
            authorizer["advertisedScopeMapping"] = advertised
        gw_resp = control.create_gateway(
            name=gw_name,
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration={"customJWTAuthorizer": authorizer},
            protocolConfiguration=mcp_protocol_configuration(),
            exceptionLevel="DEBUG",
        )
        gateway_id = gw_resp["gatewayId"]
        gw_url = gw_resp["gatewayUrl"]

    # CreateGateway (and get_gateway) return gatewayUrl with the /mcp path already
    # appended. Store the bare host instead: the identifier-URI PATCH below, the
    # RFC 9728 .well-known probe in entra/gateway.md, and the 401 check all build on
    # top of GATEWAY_URL, and GatewayMCPClient re-appends /mcp when it is missing.
    # Keeping the suffix here would yield .../mcp/mcp on the identifier URI.
    gateway_url = gw_url.rstrip("/").removesuffix("/mcp")
    print(f"\n  Gateway ID:  {gateway_id}")
    print(f"  Gateway URL: {gateway_url}")

    print("\n  Waiting for READY...")
    last_status = None
    while True:
        time.sleep(10)
        status = control.get_gateway(gatewayIdentifier=gateway_id)["status"]
        if status != last_status:
            print(f"    Status: {status}")
            last_status = status
        if status in TERMINAL_STATUSES:
            break

    save_env(GATEWAY_ID=gateway_id, GATEWAY_URL=gateway_url)
    print("\n  Saved to .env: GATEWAY_ID, GATEWAY_URL")

    print()
    print("=" * 62)
    hint = identifier_uri_hint_lines(idp, audience=aud, gateway_url=gateway_url)
    if hint:
        print("  NEXT, before pointing an MCP client at this gateway:")
        print()
        for line in hint:
            print(line)
        print()
    print("  Then create the consent portal:")
    print(f"    uv run python scripts/deploy_portal.py --{idp['name']}")
    print("=" * 62)


if __name__ == "__main__":
    main()
