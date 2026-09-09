"""Create an AgentCore Consent Portal over an existing gateway.

Creates three things, each idempotently, so this script is safe to re-run:

    1. the primary IdP OAuth2 credential provider
    2. the execution role Consent Portal assumes
    3. the consent portal itself

If a portal with the same name already exists, this reuses it, writes the same
variables to scripts/.env as a fresh create would, and exits 0. It never creates
a second portal under a different id.

Takes a required --<idp> flag naming a profile in scripts/idps/, not a
--<server> flag: a consent portal is a property of the gateway and its identity
provider, not of any one MCP server.

Requires (see idp_config.py for the full contract):
    GATEWAY_ID, ENTRA_DISCOVERY_URL, ENTRA_RESOURCE, ENTRA_ALLOWED_SCOPES,
    PORTAL_CLIENT_ID, PORTAL_CLIENT_SECRET

Usage:
    uv run python scripts/deploy_portal.py --entra
"""

import json
import sys
import time

import boto3
from botocore.exceptions import ClientError
from idp_config import (
    audience,
    client_id,
    client_secret,
    discovery_url,
    idp_provider_name,
    portal_callback_hint_lines,
    portal_name,
    portal_role_name,
    portal_scopes,
    select_idp,
)
from mcp_config import get_required_env, load_env, save_env

# The Consent Portal service principal. Public and stable -- see
# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal-execution-role.html
CONSENT_PORTAL_SERVICE_PRINCIPAL = "bedrock-agentcore.amazonaws.com"

# Terminal states that mean the portal will never become ACTIVE on its own.
TERMINAL_STATUSES = ("FAILED", "UPDATE_FAILED", "DELETING")

# CreateConsentPortal constrains the name to 1-50 characters. Checked here so an
# overridden PORTAL_NAME fails before three resources have been created.
MAX_PORTAL_NAME_LENGTH = 50

ROLE_POLICY_NAME = "ConsentPortalAccess"


def ensure_idp_provider(control, idp, name, secret):
    """Create the primary IdP credential provider, or reuse it if it exists.

    Vendor comes from the profile. For Entra it is CustomOauth2 rather than
    MicrosoftOauth2: the Microsoft config takes a tenantId and leaves nowhere to
    state the discovery URL, and with Entra the discovery URL (specifically its
    /v2.0/ segment) is exactly the thing that has to be right.

    The provider behind idpConfig.credentialProviderArn must issue JWT access
    tokens, which rules out the OAuth2-only vendors -- GithubOauth2, SlackOauth2,
    SalesforceOauth2, AtlasianOauth2, LinkedinOauth2. Those remain valid outbound;
    deploy_credential.py --github uses GithubOauth2 for exactly that.
    """
    print(f"--- Step 1: primary IdP credential provider ({idp['displayName']}) ---")
    config = {
        "clientId": client_id(idp),
        "clientSecret": secret,
        "oauthDiscovery": {"discoveryUrl": discovery_url(idp)},
    }
    try:
        response = control.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor=idp["vendor"],
            oauth2ProviderConfigInput={idp["providerConfigKey"]: config},
        )
        arn = response["credentialProviderArn"]
        print(f"  created: {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] not in (
            "ConflictException",
            "ValidationException",
        ):
            raise
        existing = control.get_oauth2_credential_provider(name=name)
        arn = existing["credentialProviderArn"]
        print(f"  reusing existing: {name}")

    print(f"  arn: {arn}")
    return arn


def trust_policy(account_id, region, portal_arn=None):
    """The role's trust policy, optionally pinned to one portal.

    The aws:SourceAccount and aws:SourceArn conditions guard against the confused
    deputy problem. SourceArn cannot be set on the first pass -- the portal does
    not exist yet -- so the role is created account-scoped and then re-put with
    the ARN once CreateConsentPortal returns it.
    """
    condition = {"StringEquals": {"aws:SourceAccount": account_id}}
    if portal_arn:
        condition["ArnLike"] = {"aws:SourceArn": portal_arn}
    else:
        condition["ArnLike"] = {
            "aws:SourceArn": (
                f"arn:aws:bedrock-agentcore:{region}:{account_id}:consent-portal/*"
            )
        }

    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ConsentPortalAssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": CONSENT_PORTAL_SERVICE_PRINCIPAL},
                "Action": "sts:AssumeRole",
                "Condition": condition,
            }
        ],
    }


def ensure_execution_role(iam, role_name, gateway_id, region, account_id):
    """Create the role Consent Portal assumes, or reuse and repair it.

    The permissions policy mirrors the one in the AgentCore developer guide:
    read the gateway and its targets, read credential providers in the default
    token vault, the three token operations, and the identity service's own
    OAuth secrets.
    """
    print("\n--- Step 2: execution role ---")

    access_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetGateway",
                    "bedrock-agentcore:GetGatewayTarget",
                    "bedrock-agentcore:ListGatewayTargets",
                ],
                "Resource": (
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/{gateway_id}"
                ),
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetOauth2CredentialProvider",
                    "bedrock-agentcore:ListOauth2CredentialProviders",
                ],
                "Resource": [
                    (
                        f"arn:aws:bedrock-agentcore:{region}:{account_id}"
                        ":token-vault/default/oauth2credentialprovider/*"
                    ),
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default",
                ],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CompleteResourceTokenAuth",
                    "bedrock-agentcore:GetResourceOauth2Token",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": [
                    (
                        f"arn:aws:secretsmanager:{region}:{account_id}"
                        ":secret:bedrock-agentcore-identity!default/oauth2/*"
                    )
                ],
                "Condition": {
                    "StringEquals": {
                        "aws:ResourceTag/aws:secretsmanager:owningService": (
                            "bedrock-agentcore-identity"
                        )
                    }
                },
            },
        ],
    }

    try:
        role_arn = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy(account_id, region)),
            Description="Consent Portal tutorial execution role",
        )["Role"]["Arn"]
        print(f"  created: {role_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        print(f"  reusing existing: {role_name}")

    # Unconditional, not only on create: a re-run should repair a policy that has
    # drifted, and put_role_policy is an upsert.
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=ROLE_POLICY_NAME,
        PolicyDocument=json.dumps(access_policy),
    )

    print(f"  arn: {role_arn}")
    print(f"  trusts: {CONSENT_PORTAL_SERVICE_PRINCIPAL}")
    return role_arn


def pin_role_to_portal(iam, role_name, portal_arn, account_id, region):
    """Narrow the trust policy's aws:SourceArn to this one portal.

    The developer guide describes this as a manual second pass, because you do not
    know the portal ARN when you create the role. We do know it by now, so it
    happens automatically rather than being left as an exercise.
    """
    iam.update_assume_role_policy(
        RoleName=role_name,
        PolicyDocument=json.dumps(trust_policy(account_id, region, portal_arn)),
    )
    print(f"  trust policy pinned to: {portal_arn}")


def find_portal(control, name):
    """Return the summary of the portal called `name`, or None.

    Paged, because the account may hold more portals than one page returns and a
    truncated scan would silently create a duplicate.
    """
    kwargs = {"maxResults": 50}
    while True:
        page = control.list_consent_portals(**kwargs)
        for item in page.get("consentPortals", []):
            if item.get("name") == name:
                return item
        token = page.get("nextToken")
        if not token:
            return None
        kwargs["nextToken"] = token


def wait_for_active(control, portal_id):
    """Poll until the portal is ACTIVE with a portalUrl, or fail loudly.

    Waits on portalUrl and not just on ACTIVE: the URL is what every later step
    needs, and an ACTIVE portal without one would sail through and break at the
    next step instead of here.
    """
    print("\n  waiting for ACTIVE...")
    last_status = None
    for _ in range(30):
        portal = control.get_consent_portal(consentPortalIdentifier=portal_id)
        status = portal["status"]
        if status != last_status:
            print(f"    status: {status}")
            last_status = status
        url = portal.get("portalUrl")
        if status == "ACTIVE" and url:
            return url
        if status in TERMINAL_STATUSES:
            print(f"ERROR: portal reached terminal status {status}")
            reason = portal.get("statusReason")
            if reason:
                print(f"  reason: {reason}")
            sys.exit(1)
        time.sleep(10)

    print("ERROR: portal did not become ACTIVE with a portalUrl in time.")
    print(
        "  check: aws bedrock-agentcore-control get-consent-portal"
        f" --consent-portal-identifier {portal_id}"
    )
    sys.exit(1)


def create_portal_with_retry(control, **kwargs):
    """CreateConsentPortal, retrying while IAM is still eventually consistent.

    A role created seconds ago is not always assumable yet, and the API surfaces
    that as a validation error about the execution role rather than as a retryable
    throttle. Retrying here beats telling the reader to sleep and rerun by hand.
    """
    for attempt in range(6):
        try:
            return control.create_consent_portal(**kwargs)
        except ClientError as e:
            err = e.response["Error"]
            message = err.get("Message", "")
            retryable = err["Code"] == "ValidationException" and (
                "role" in message.lower() or "assume" in message.lower()
            )
            if not retryable or attempt == 5:
                raise
            print(f"    execution role not assumable yet, retrying: {message}")
            time.sleep(10)
    raise AssertionError("unreachable")


def main():
    idp = select_idp(__doc__)
    load_env()

    gateway_id = get_required_env("GATEWAY_ID")
    aud = audience(idp)
    scopes = portal_scopes(idp)
    secret = client_secret(idp)

    name = portal_name(idp)
    if not 1 <= len(name) <= MAX_PORTAL_NAME_LENGTH:
        print(f"ERROR: portal name must be 1-{MAX_PORTAL_NAME_LENGTH} characters.")
        print(f"  got: {name} ({len(name)})")
        sys.exit(1)

    region = boto3.Session().region_name
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    iam = boto3.client("iam", region_name=region)
    account_id = boto3.client("sts", region_name=region).get_caller_identity()[
        "Account"
    ]

    idp_arn = ensure_idp_provider(control, idp, idp_provider_name(idp), secret)
    role_arn = ensure_execution_role(
        iam, portal_role_name(idp), gateway_id, region, account_id
    )

    print("\n--- Step 3: consent portal ---")
    print(f"  scopes: {', '.join(scopes)}")
    existing = find_portal(control, name)
    if existing:
        portal_id = existing["consentPortalId"]
        print(f"  reusing existing portal: {name}")
        print(f"  id: {portal_id}")
        portal = control.get_consent_portal(consentPortalIdentifier=portal_id)
        portal_arn = portal["consentPortalArn"]
        portal_url = portal.get("portalUrl")
        if not portal_url or portal["status"] != "ACTIVE":
            portal_url = wait_for_active(control, portal_id)
    else:
        response = create_portal_with_retry(
            control,
            name=name,
            executionRoleArn=role_arn,
            idpConfig={
                "credentialProviderArn": idp_arn,
                # openid is guaranteed present by portal_scopes: the portal always
                # requests it, and every scope it requests must be permitted on
                # the IdP app or authorization fails with invalid_scope.
                "scopes": scopes,
                "audience": aud,
            },
            sources=[{"identifier": gateway_id, "type": "agentcore-gateway"}],
            description=f"Consent portal backed by {idp['displayName']}",
        )
        portal_id = response["consentPortalId"]
        portal_arn = response["consentPortalArn"]
        print(f"  created: {name}")
        print(f"  id: {portal_id}")
        portal_url = response.get("portalUrl")
        if not portal_url or response["status"] != "ACTIVE":
            portal_url = wait_for_active(control, portal_id)

    pin_role_to_portal(iam, portal_role_name(idp), portal_arn, account_id, region)

    # Stored scheme-stripped and without a trailing slash. The two derived URLs
    # keep their scheme so no consumer has to re-add it and get it wrong.
    bare_url = portal_url.removeprefix("https://").rstrip("/")
    callback_url = f"https://{bare_url}/callback"
    connect_return_url = f"https://{bare_url}/connect/callback"

    save_env(
        PORTAL_ID=portal_id,
        PORTAL_ARN=portal_arn,
        PORTAL_URL=bare_url,
        PORTAL_CALLBACK_URL=callback_url,
        PORTAL_CONNECT_RETURN_URL=connect_return_url,
        IDP_PROVIDER_ARN=idp_arn,
        EXECUTION_ROLE_ARN=role_arn,
    )

    print(f"\n  portal url: https://{bare_url}")
    print("  Saved to .env: PORTAL_ID, PORTAL_ARN, PORTAL_URL,")
    print("    PORTAL_CALLBACK_URL, PORTAL_CONNECT_RETURN_URL,")
    print("    IDP_PROVIDER_ARN, EXECUTION_ROLE_ARN")

    print()
    print("=" * 62)
    print("  DO THIS NOW, before creating a target:")
    print()
    print("  Register the portal's callback on its IdP application. Enter it")
    print("  exactly, with NO trailing slash -- a trailing slash makes the IdP")
    print("  reject the callback as unregistered. This is a *web* redirect, not")
    print("  a public-client one: the portal holds a secret and exchanges the")
    print("  code server-side.")
    print()
    for line in portal_callback_hint_lines(
        idp, client_id=client_id(idp), callback_url=callback_url
    ):
        print(line)
    print()
    print("  Then sign in at the portal URL above to confirm the IdP leg works.")
    print("  The Connections page will be empty until a target exists.")
    print()
    print("  Any target's defaultReturnUrl must be:")
    print(f"    {connect_return_url}")
    print("  deploy_target_schema.py reads PORTAL_CONNECT_RETURN_URL from .env,")
    print("  so you do not need to pass it by hand.")
    print("=" * 62)


if __name__ == "__main__":
    main()
