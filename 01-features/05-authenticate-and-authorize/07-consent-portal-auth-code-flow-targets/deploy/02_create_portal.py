"""Create the AgentCore managed consent portal over the gateway from step 01.

Three resources, each idempotent:

  1. The PRIMARY IdP OAuth2 credential provider — who your end users sign in
     as. This is not the outbound provider they later connect to. It must
     issue JWT access tokens, which rules out the OAuth2-only vendors
     (GithubOauth2, SlackOauth2, SalesforceOauth2, AtlasianOauth2,
     LinkedinOauth2); those are valid *outbound* providers, which is exactly
     what GitHub is used for in step 03.

     Vendor is CustomOauth2 even for Entra: the MicrosoftOauth2 config takes a
     tenantId and leaves nowhere to state a discovery URL, and with Entra the
     discovery URL (specifically its /v2.0/ segment) is the thing that has to
     be right.

  2. The execution role the portal assumes in your account to read the
     gateway, its targets and the credential providers, and to call the token
     operations. There is a chicken-and-egg problem in its trust policy's
     aws:SourceArn — you cannot know the portal ARN before the portal exists —
     so the role is created with a wildcard consent-portal/* ARN and re-put
     pinned to the exact ARN as soon as CreateConsentPortal returns it.

  3. The consent portal itself, polled until status is ACTIVE *and* portalUrl
     is non-empty. Everything after this is built from that URL.

Then it registers `https://<portalUrl>/callback` as a redirect URI on the
primary IdP app — Entra via `az`, Okta via the Admin API — because that URL
does not exist until now. If the CLI or admin token is unavailable it prints
the command for you to run.

Writes to .env: PORTAL_ID, PORTAL_ARN, PORTAL_URL (bare host),
PORTAL_CALLBACK_URL, PORTAL_CONNECT_RETURN_URL, IDP_PROVIDER_ARN,
PORTAL_EXECUTION_ROLE_ARN.

Run from the sample root:
    python deploy/02_create_portal.py --entra
    python deploy/02_create_portal.py --okta
"""

from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
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
    find_portal_by_name,
    idp_provider_name,
    load_env,
    must_env,
    okta_domain,
    portal_name,
    portal_role_name,
    portal_scopes,
    save_env,
    select_idp,
)

# Public and stable — see the consent portal execution role documentation.
CONSENT_PORTAL_SERVICE_PRINCIPAL = "bedrock-agentcore.amazonaws.com"
TERMINAL_STATUSES = ("FAILED", "UPDATE_FAILED", "DELETING")
MAX_PORTAL_NAME_LENGTH = 50
ROLE_POLICY_NAME = "ConsentPortalAccess"


def portal_client_secret() -> str:
    """Read the portal login app's secret without putting it in argv."""
    secret = os.environ.get("PORTAL_CLIENT_SECRET")
    if not secret or secret in ("replace-me", "REPLACE_ME"):
        secret = getpass.getpass("Portal login client secret: ")
    if not secret:
        print("ERROR: PORTAL_CLIENT_SECRET not set and nothing entered.", file=sys.stderr)
        sys.exit(1)
    return secret


def ensure_idp_provider(control, idp: dict, name: str, client_id: str, secret: str, disco: str) -> str:
    print(f"--- Step 1: primary IdP credential provider ({idp['displayName']}) ---")
    config = {
        "clientId": client_id,
        "clientSecret": secret,
        "oauthDiscovery": {"discoveryUrl": disco},
    }
    try:
        arn = control.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor=idp["vendor"],
            oauth2ProviderConfigInput={idp["providerConfigKey"]: config},
        )["credentialProviderArn"]
        print(f"  ✓ Created: {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("ConflictException", "ValidationException"):
            raise
        arn = control.get_oauth2_credential_provider(name=name)["credentialProviderArn"]
        print(f"  • Reusing existing: {name}")
    print(f"  arn: {arn}")
    return arn


def trust_policy(account_id: str, region: str, portal_arn: str | None = None) -> dict:
    source_arn = portal_arn or f"arn:aws:bedrock-agentcore:{region}:{account_id}:consent-portal/*"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ConsentPortalAssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": CONSENT_PORTAL_SERVICE_PRINCIPAL},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": source_arn},
                },
            }
        ],
    }


def ensure_execution_role(iam, role_name: str, gateway_id: str, region: str, account_id: str) -> str:
    """Create or repair the portal's execution role.

    The permissions policy mirrors the one in the AgentCore developer guide:
    read the gateway and its targets, read credential providers in the default
    token vault, the three token operations, and the identity service's own
    OAuth secrets (tag-conditioned).
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
                "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/{gateway_id}",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetOauth2CredentialProvider",
                    "bedrock-agentcore:ListOauth2CredentialProviders",
                ],
                "Resource": [
                    (f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default/oauth2credentialprovider/*"),
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
                # Scoped, not "*" — these actions support resource-level
                # permissions. See the matching comment in
                # 01_create_gateway.py for which resource types each accepts
                # and why the vault/directory ids stay wildcarded.
                "Resource": [
                    # token-vault/* also covers .../oauth2credentialprovider/*,
                    # and workload-identity-directory/* also covers
                    # .../workload-identity/*, because an IAM wildcard spans "/".
                    # Listing the children as well is redundant — IAM Access
                    # Analyzer flags it.
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/*",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": [
                    (f"arn:aws:secretsmanager:{region}:{account_id}:secret:bedrock-agentcore-identity!default/oauth2/*")
                ],
                "Condition": {
                    "StringEquals": {"aws:ResourceTag/aws:secretsmanager:owningService": "bedrock-agentcore-identity"}
                },
            },
        ],
    }

    try:
        role_arn = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy(account_id, region)),
            Description="Consent portal execution role - GitHub MCP sample",
        )["Role"]["Arn"]
        print(f"  ✓ Created: {role_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        print(f"  • Reusing existing: {role_name}")

    # Unconditional, not only on create: a re-run should repair drift.
    iam.put_role_policy(RoleName=role_name, PolicyName=ROLE_POLICY_NAME, PolicyDocument=json.dumps(access_policy))
    print(f"  arn: {role_arn}")
    print(f"  trusts: {CONSENT_PORTAL_SERVICE_PRINCIPAL}")
    return role_arn


def wait_for_active(control, portal_id: str) -> str:
    """Poll until ACTIVE with a portalUrl, or fail loudly.

    Waits on portalUrl and not just on ACTIVE: the URL is what every later
    step needs, and an ACTIVE portal without one would sail through here and
    break in step 04 instead.
    """
    print("\n  waiting for ACTIVE…")
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
            print(f"ERROR: portal reached terminal status {status}", file=sys.stderr)
            reason = portal.get("statusReason")
            if reason:
                print(f"  reason: {reason}", file=sys.stderr)
            sys.exit(1)
        time.sleep(10)
    print("ERROR: portal did not become ACTIVE with a portalUrl in time.", file=sys.stderr)
    print(
        f"  check: aws bedrock-agentcore-control get-consent-portal --consent-portal-identifier {portal_id}",
        file=sys.stderr,
    )
    sys.exit(1)


def create_portal_with_retry(control, **kwargs):
    """CreateConsentPortal, retrying while IAM is still eventually consistent.

    A role created seconds ago is not always assumable yet, and the API
    surfaces that as a validation error about the execution role rather than
    as a retryable throttle.
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


# --- Registering the portal callback on the IdP ---------------------------------


def register_callback_entra(client_id: str, callback_url: str) -> bool:
    """Add the portal callback to the Entra app's web.redirectUris.

    --web-redirect-uris REPLACES the array, so the current list is read first
    and the new URI appended. Under Entra this app is also the gateway
    resource app and may already carry other web redirects.
    """
    if not shutil.which("az"):
        return False
    show = subprocess.run(
        ["az", "ad", "app", "show", "--id", client_id, "--query", "web.redirectUris", "--output", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if show.returncode != 0:
        return False
    try:
        current = json.loads(show.stdout or "[]") or []
    except json.JSONDecodeError:
        current = []
    if callback_url in current:
        print(f"  • Callback already registered on Entra app {client_id}")
        return True
    merged = current + [callback_url]
    update = subprocess.run(
        ["az", "ad", "app", "update", "--id", client_id, "--web-redirect-uris", *merged],
        capture_output=True,
        text=True,
        check=False,
    )
    if update.returncode != 0:
        print(f"  ⚠ az ad app update failed: {(update.stderr or '').strip().splitlines()[-1:]}", file=sys.stderr)
        return False
    print(f"  ✓ Registered callback on Entra app (now {len(merged)} web redirect URI(s))")
    return True


def register_callback_okta(callback_url: str) -> bool:
    """Replace the placeholder redirect on the Okta portal app with the real one.

    Okta full-object PUTs replace the whole app, so the app is fetched, its
    redirect_uris swapped, and the whole object put back.
    """
    domain = okta_domain() if os.environ.get("OKTA_DOMAIN", "").strip() else ""
    token = os.environ.get("OKTA_ADMIN_TOKEN", "").strip()
    app_id = os.environ.get("PORTAL_APP_ID", "").strip()
    if not (domain and token and app_id):
        return False
    import requests

    headers = {
        "Authorization": f"SSWS {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    base = f"https://{domain}/api/v1/apps/{app_id}"
    resp = requests.get(base, headers=headers, timeout=30)
    if resp.status_code >= 400:
        print(f"  ⚠ Okta GET /apps/{app_id} returned {resp.status_code}", file=sys.stderr)
        return False
    app = resp.json()
    uris = app["settings"]["oauthClient"].get("redirect_uris") or []
    # Drop the Step-1 placeholder; keep any real URIs already registered.
    uris = [u for u in uris if not u.startswith("https://localhost/placeholder")]
    if callback_url not in uris:
        uris.append(callback_url)
    app["settings"]["oauthClient"]["redirect_uris"] = uris
    put = requests.put(base, headers=headers, json=app, timeout=30)
    if put.status_code >= 400:
        print(f"  ⚠ Okta PUT /apps/{app_id} returned {put.status_code}", file=sys.stderr)
        return False
    print(f"  ✓ Registered callback on Okta portal app (redirect_uris={uris})")
    return True


def print_callback_hint(idp: dict, callback_url: str) -> None:
    print()
    print("=" * 68)
    print("  DO THIS NOW, before creating the GitHub target:")
    print()
    print("  Register the portal's callback on its login app. Enter it exactly,")
    print("  with NO trailing slash — a trailing slash makes the IdP reject the")
    print("  callback as unregistered, and the failure surfaces as a generic")
    print("  login error.")
    print()
    for line in idp.get("portalCallbackHint", []):
        print(line.replace("<PORTAL_CALLBACK_URL>", callback_url))
    print("=" * 68)


def main() -> None:
    idp = select_idp(__doc__)
    check_boto_version()
    load_env()

    gateway_id = must_env("GATEWAY_ID")
    audience = must_env("IDP_AUDIENCE")
    client_id = must_env("PORTAL_CLIENT_ID")
    disco = discovery_url(idp)
    scopes = portal_scopes()
    secret = portal_client_secret()

    name = portal_name(idp)
    if not 1 <= len(name) <= MAX_PORTAL_NAME_LENGTH:
        print(f"ERROR: portal name must be 1-{MAX_PORTAL_NAME_LENGTH} characters.", file=sys.stderr)
        print(f"  got: {name} ({len(name)})", file=sys.stderr)
        sys.exit(1)

    control, region = control_client()
    iam = boto3.client("iam", region_name=region)
    account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]

    idp_arn = ensure_idp_provider(control, idp, idp_provider_name(idp), client_id, secret, disco)
    role_arn = ensure_execution_role(iam, portal_role_name(idp), gateway_id, region, account_id)

    print("\n--- Step 3: consent portal ---")
    print(f"  name:     {name}")
    print(f"  scopes:   {', '.join(scopes)}")
    print(f"  audience: {audience}")
    print(f"  source:   gateway {gateway_id}")
    existing = find_portal_by_name(control, name)
    if existing:
        portal_id = existing["consentPortalId"]
        print(f"  • Reusing existing portal: {portal_id}")
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
                # openid is guaranteed present by portal_scopes(): the portal
                # always requests it, and every scope it requests must be
                # permitted on the IdP app or authorization fails with
                # invalid_scope.
                "scopes": scopes,
                "audience": audience,
            },
            # Exactly one source, and it is not updatable — the gateway a
            # portal fronts is fixed for the portal's life.
            sources=[{"identifier": gateway_id, "type": "agentcore-gateway"}],
            description=f"GitHub MCP consent portal backed by {idp['displayName']}",
        )
        portal_id = response["consentPortalId"]
        portal_arn = response["consentPortalArn"]
        print(f"  ✓ Created. ID: {portal_id}")
        portal_url = response.get("portalUrl")
        if not portal_url or response["status"] != "ACTIVE":
            portal_url = wait_for_active(control, portal_id)

    iam.update_assume_role_policy(
        RoleName=portal_role_name(idp),
        PolicyDocument=json.dumps(trust_policy(account_id, region, portal_arn)),
    )
    print(f"  ✓ Trust policy pinned to: {portal_arn}")

    # portalUrl's scheme is not guaranteed: observed WITH "https://" in
    # us-west-2, and documented elsewhere as a bare host. Normalize to a bare
    # host, then derive the two URLs with the scheme attached, so nothing
    # downstream has to get this right twice or double up the prefix.
    #
    # The hostname is derived from the GATEWAY id, not the portal id
    # (entra-consent-github-gw-xxxx.consent-portal.<region>.amazonaws.com),
    # which is worth knowing when you go looking for it in a browser.
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
        PORTAL_EXECUTION_ROLE_ARN=role_arn,
    )
    print(f"\n  portal url: https://{bare_url}")
    print("  Saved to .env: PORTAL_ID, PORTAL_ARN, PORTAL_URL, PORTAL_CALLBACK_URL,")
    print("    PORTAL_CONNECT_RETURN_URL, IDP_PROVIDER_ARN, PORTAL_EXECUTION_ROLE_ARN")

    print("\n--- Step 4: register the portal callback on the IdP ---")
    registered = (
        register_callback_entra(client_id, callback_url)
        if idp["name"] == "entra"
        else register_callback_okta(callback_url)
    )
    if not registered:
        print("  • Could not register it automatically.")
        print_callback_hint(idp, callback_url)

    print()
    print("Verify the login leg now:")
    print(f"    open https://{bare_url}")
    print("  Sign in with an IdP user. Expect an EMPTY Connections page — that")
    print("  empty page is the success condition: the gateway has no")
    print("  AUTHORIZATION_CODE targets yet, so there is nothing to consent to.")
    print()
    print("Next: create the GitHub OAuth App, export GITHUB_CLIENT_ID/SECRET, then")
    print("    python deploy/03_create_github_provider.py")


if __name__ == "__main__":
    main()
