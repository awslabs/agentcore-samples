"""
Interactive, guided walkthrough of Use Case 1 (Okta flavor).

This script is not just a runnable example — it's a teaching tool. It pauses
at each chapter, explains what is about to happen, runs the API call, then
highlights what changed in the returned tokens so you can see OBO doing its
job.

Run:
    python 02_run_example.py

Non-interactive mode (skips pauses, useful in CI or demos):
    INTERACTIVE_NO_PAUSE=1 python 02_run_example.py

Disable ANSI colors:
    NO_COLOR=1 python 02_run_example.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import webbrowser
from typing import Any
from urllib.parse import urlparse as _urlparse

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

from callback_server import start_callback_server
from interactive import (
    action,
    chapter,
    compare_claims,
    explain,
    header,
    info,
    observe,
    pause,
    show_claims,
    success,
)

CALLBACK_PORT = 8081
CALLBACK_URL = f"http://localhost:{CALLBACK_PORT}/callback"


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: required env var {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def decode_jwt_claims(token: str) -> dict[str, Any]:
    """Decode a JWT's payload without verifying the signature (debug/display only)."""
    try:
        payload_b64 = token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8"))
    except Exception as e:  # pragma: no cover
        return {"_decode_error": str(e)}


def chapter_1_sign_in(
    ac_identity,
    workload_name: str,
    client_provider_name: str,
    upstream_scope: str,
    user_alias: str,
) -> str:
    """Obtain a user access token — either from AgentCore's cache or via a fresh 3LO."""
    chapter(
        1,
        "Sign the user in (simulating the frontend)",
        "Get an Okta access token that says 'Alice is signed in and consented'",
    )
    explain("""
In production, your frontend handles this. It redirects the user to Okta,
the user signs in, and the frontend ends up with an access token whose `aud`
claim points at your custom authorization server. The frontend then hands
that token to the agent.

For this local example, we use AgentCore Identity's USER_FEDERATION flow to
simulate exactly what the frontend would produce. The result is identical:
a standard Okta-issued access token you can feed into the next chapter.
""")
    pause()

    action(
        "Calling get_workload_access_token_for_user_id — gets a scratch token AgentCore will use to track this user's session"
    )
    wl_resp = ac_identity.get_workload_access_token_for_user_id(
        workloadName=workload_name,
        userId=user_alias,
    )
    user_wl_token = wl_resp["workloadAccessToken"]
    success("Got a workload token for the test user")

    action("Calling get_resource_oauth2_token with oauth2Flow=USER_FEDERATION")
    fed_resp = ac_identity.get_resource_oauth2_token(
        workloadIdentityToken=user_wl_token,
        resourceCredentialProviderName=client_provider_name,
        scopes=[upstream_scope],
        oauth2Flow="USER_FEDERATION",
        resourceOauth2ReturnUrl=CALLBACK_URL,
    )

    if "accessToken" in fed_resp:
        info("AgentCore had a cached token from a previous sign-in — reusing it")
        user_token = fed_resp["accessToken"]
    else:
        info("No cached token; running the full 3-legged OAuth flow")
        server, code_future = start_callback_server(CALLBACK_PORT)
        try:
            webbrowser.open(fed_resp["authorizationUrl"])
            info("Browser opened. Waiting for you to sign in...")
            code_future.result(timeout=300)
            success("Callback received")

            ac_identity.complete_resource_token_auth(
                userIdentifier={"userId": user_alias},
                sessionUri=fed_resp["sessionUri"],
            )
            token_resp = ac_identity.get_resource_oauth2_token(
                workloadIdentityToken=user_wl_token,
                resourceCredentialProviderName=client_provider_name,
                scopes=[upstream_scope],
                oauth2Flow="USER_FEDERATION",
                resourceOauth2ReturnUrl=CALLBACK_URL,
            )
            user_token = token_resp["accessToken"]
        finally:
            server.shutdown()

    success("User access token obtained")
    return user_token


def chapter_2_inspect_inbound(user_token: str, native_client_id: str, audience: str) -> dict:
    """Decode the inbound user token and point out the claims that matter."""
    chapter(
        2,
        "Inspect the inbound user access token",
        "Confirm this token really is 'for your auth server, about the user, issued to the native app'",
    )
    explain("""
This is the token the agent would receive from the frontend. Before calling
any downstream API, we decode it to confirm:

  • `aud` (audience) — who this token is FOR. Must match your Okta custom
                        authorization server audience (typically api://default).
  • `sub` (subject)  — the user's login (e.g. alice@example.com). This is
                        the Okta claim we want to preserve across the exchange.
  • `cid` (client id)— the app that requested the token. For the inbound
                        token this is the native app (the frontend client).
  • `scp` (scope)    — what the user consented the token-bearer to do.
                        Usually `openid` for the upstream leg.

Okta will only agree to OBO if `aud` matches what's configured on the custom
authorization server. If it doesn't, the exchange fails at the IdP.
""")
    pause()

    claims = decode_jwt_claims(user_token)
    show_claims(
        "Inbound user access token claims",
        claims,
        highlight=["aud", "sub", "cid", "scp"],
    )

    if claims.get("cid") == native_client_id:
        success(f"cid matches NATIVE_APP_CLIENT_ID ({native_client_id}) — token issued to the frontend app")
    else:
        info(f"cid is {claims.get('cid')!r}, expected {native_client_id!r} — token was issued to a different client")

    if claims.get("aud") == audience:
        success(f"aud matches OKTA_AUDIENCE ({audience}) — token is for your auth server")
    else:
        info(f"aud is {claims.get('aud')!r}, expected {audience!r} — would fail OBO at the exchange step")

    observe(
        "Key property: user identity is encoded in `sub`",
        f"The `sub` claim ({claims.get('sub', '?')}) identifies this user stably. "
        "Remember this value — we will look for it again in the OBO'd token.",
    )
    pause()
    return claims


def chapter_3_obo_exchange(
    ac_identity,
    workload_name: str,
    actor_provider_name: str,
    downstream_scope: str,
    audience: str,
    user_token: str,
) -> str:
    """Perform the Okta token-exchange OBO flow and return the downstream token."""
    chapter(
        3,
        "Perform the OBO token exchange (RFC 8693)",
        "Swap the user token for an API2-scoped token — no user interaction",
    )
    explain(f"""
This is where Okta-flavored OBO happens. Two AgentCore Identity calls:

  1. GetWorkloadAccessTokenForJWT — wraps the inbound user token into an
     AgentCore-internal token. AgentCore will unwrap it to get the user
     token back when it talks to Okta.

  2. GetResourceOauth2Token with oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE —
     AgentCore POSTs to Okta's token endpoint with:
         grant_type=urn:ietf:params:oauth:grant-type:token-exchange
         subject_token=<user token>
         subject_token_type=urn:ietf:params:oauth:token-type:access_token
         audience={audience}
         scope={downstream_scope}
     Okta validates the subject token, checks the service app's Token
     Exchange rule, and mints a new access token audienced at your auth
     server with the downstream scope.

Two Okta-specific knobs you will NOT find in Entra:
  • `subject_token_type` — passed as a `customParameters` entry because
    Okta requires it on every exchange. Without it you get a 400.
  • `audiences` — passed as a list on the SDK call. Okta requires an
    `audience` parameter on the exchange so it knows which auth server
    the resulting token is for; the SDK models it as a list because
    some providers accept multiple audiences at once.

We never handle the service app's client secret, never build the POST body
ourselves, and the user is NOT re-prompted to consent.
""")
    pause()

    action("Calling GetWorkloadAccessTokenForJWT to wrap the user token")
    obo_wl_resp = ac_identity.get_workload_access_token_for_jwt(
        workloadName=workload_name,
        userToken=user_token,
    )
    obo_workload_token = obo_wl_resp["workloadAccessToken"]
    success("User token wrapped into an AgentCore workload token")

    action("Calling GetResourceOauth2Token with oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE")
    info("  customParameters: subject_token_type=urn:ietf:params:oauth:token-type:access_token")
    info(f"  audiences:        [{audience}]")
    info(f"  scopes:           [{downstream_scope}]")
    obo_resp = ac_identity.get_resource_oauth2_token(
        workloadIdentityToken=obo_workload_token,
        resourceCredentialProviderName=actor_provider_name,
        scopes=[downstream_scope],
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        customParameters={
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        },
        audiences=[audience],
    )
    success("OBO exchange complete — we have a downstream-scoped access token")
    return obo_resp["accessToken"]


def chapter_4_compare_tokens(inbound_claims: dict, downstream_token: str) -> dict:
    """Side-by-side comparison that makes the OBO guarantee obvious."""
    chapter(
        4,
        "Compare the inbound and outbound tokens",
        "See what changed (scope, actor) and what stayed the same (user, audience)",
    )
    explain("""
Look for three things:

  1. `sub` MUST stay the same — this is the OBO guarantee in Okta's flavor.
     The user is the same human across the exchange.

  2. `cid` MUST change — the inbound token was issued to the native app;
     the outbound token was minted for the service app. `cid` is Okta's
     record of who acted. This is the OBO breadcrumb.

  3. `scp` MUST change — the inbound token carried the upstream scope
     (typically `openid`); the outbound carries the downstream custom scope
     (e.g. `oboe2e.apiC.read`).

An Okta-specific subtlety: `aud` usually does NOT change within a single
auth server — both tokens are for `api://default`. What distinguishes them
is `cid` + `scp`, not `aud`. Compare Entra, where `aud` rotates from the
agent app to Microsoft Graph. Which claim moves depends on the IdP.
""")
    pause()

    outbound_claims = decode_jwt_claims(downstream_token)
    compare_claims(
        "Inbound (user → agent)",
        inbound_claims,
        "Outbound (agent → API2)",
        outbound_claims,
        keys=["aud", "sub", "cid", "scp", "iss", "uid"],
    )

    # Verify the OBO invariants
    if inbound_claims.get("sub") == outbound_claims.get("sub"):
        success("USER IDENTITY PRESERVED — sub matches on both tokens")
    else:
        info("sub changed — this would mean the user identity was lost. Check your setup.")

    if inbound_claims.get("cid") != outbound_claims.get("cid"):
        success("ACTOR ROTATED — cid changed from the native app to the service app")
    else:
        info("cid did not change — the OBO exchange may not have happened")

    inbound_scp = inbound_claims.get("scp", [])
    outbound_scp = outbound_claims.get("scp", [])
    if inbound_scp != outbound_scp:
        success(f"SCOPE CHANGED — {inbound_scp} → {outbound_scp}")
    else:
        info(f"scp unchanged ({inbound_scp}) — the exchange did not re-scope the token")

    observe(
        "This is the OBO guarantee in Okta's flavor",
        "Same user (sub). Different actor (cid). Different scope (scp). No extra "
        "consent. The agent now holds a token it can send to the downstream API, "
        "and the API will accept it and run as the user, not as the agent.",
    )
    pause()
    return outbound_claims


def chapter_5_simulate_downstream(outbound_claims: dict, downstream_scope: str) -> None:
    """Show what the agent would do next with the downstream token."""
    chapter(
        5,
        "Use the OBO token against the downstream API",
        "Show where the token goes next and what the downstream enforces",
    )
    explain(f"""
Unlike Microsoft Graph (the Entra example's downstream), Okta does not
include a universally-present API you can call with an arbitrary custom
scope. In a real deployment you would have an API2 service — a FastAPI
app, a Spring Boot service, a Lambda, whatever — that validates the
access token against your Okta authorization server and enforces the
`{downstream_scope}` scope on its own endpoints.

The call would look exactly like any bearer-token API call:

    GET https://api2.example.com/resources
    Authorization: Bearer <downstream_token>

API2's validation logic (typically a middleware or library) would:

  1. Fetch Okta's JWKS from the auth server's discovery document.
  2. Verify the token's signature, issuer, and expiration.
  3. Confirm `aud` matches its configured audience.
  4. Confirm `scp` contains the scope required for this endpoint.
  5. Use `sub` to identify the end user for authorization / audit logging.

Since we don't have a live API2 to call here, we'll stop at validating the
token claims the downstream API would see.
""")
    pause()

    action("Claims the downstream API2 would validate on this token")
    show_claims(
        "Outbound (downstream) token claims",
        outbound_claims,
        highlight=["aud", "sub", "cid", "scp"],
    )

    checks = [
        ("aud is set", bool(outbound_claims.get("aud"))),
        ("sub is set (user identity)", bool(outbound_claims.get("sub"))),
        (
            f"scp contains {downstream_scope!r}",
            downstream_scope in (outbound_claims.get("scp") or []),
        ),
        # Parse the issuer URL and check the host suffix instead of a naive
        # substring — a URL like https://evil.example/okta.com/x would spoof
        # the substring form.
        (
            "iss is an Okta authorization server",
            _urlparse(str(outbound_claims.get("iss", ""))).netloc.lower().endswith(".okta.com"),
        ),
    ]
    for label, ok in checks:
        if ok:
            success(label)
        else:
            info(f"{label} — FAILED (this would reject at the API)")

    observe(
        "What just happened",
        "Your agent obtained a token scoped to API2, on behalf of the user, using "
        "the user's consent (not the agent's), without re-prompting. That's the "
        "complete OBO loop in Okta's flavor — same shape as Entra, different "
        "protocol details.",
    )


def main() -> None:
    load_dotenv()
    region = os.environ.get("AWS_REGION", "us-west-2")

    workload_name = must_env("WORKLOAD_NAME")
    client_provider_name = must_env("CLIENT_PROVIDER_NAME")
    actor_provider_name = must_env("ACTOR_PROVIDER_NAME")
    native_client_id = must_env("NATIVE_APP_CLIENT_ID")
    upstream_scope = must_env("UPSTREAM_SCOPE")
    downstream_scope = must_env("DOWNSTREAM_SCOPE")
    audience = must_env("OKTA_AUDIENCE")
    user_alias = must_env("USER_ALIAS")

    ac_identity = boto3.client("bedrock-agentcore", region_name=region)

    header(
        "Use Case 1 (Okta) — Interactive OBO Walkthrough",
        "User → Frontend → Agent → Downstream API (RFC 8693 token exchange)",
    )
    explain(f"""
You will walk through five chapters. Each one explains the step, runs the
API call, and points out what changed. Press Enter at the ↵ prompts; set
INTERACTIVE_NO_PAUSE=1 in your shell to skip the pauses.

  Region:              {region}
  Workload:            {workload_name}
  Audience:            {audience}
  Upstream scope:      {upstream_scope}
  Downstream scope:    {downstream_scope}
""")
    pause("Press Enter to start Chapter 1")

    try:
        user_token = chapter_1_sign_in(
            ac_identity,
            workload_name,
            client_provider_name,
            upstream_scope,
            user_alias,
        )
        inbound_claims = chapter_2_inspect_inbound(user_token, native_client_id, audience)
        downstream_token = chapter_3_obo_exchange(
            ac_identity,
            workload_name,
            actor_provider_name,
            downstream_scope,
            audience,
            user_token,
        )
        outbound_claims = chapter_4_compare_tokens(inbound_claims, downstream_token)
        chapter_5_simulate_downstream(outbound_claims, downstream_scope)

        header(
            "✓ Walkthrough complete",
            "Review what just happened in the comparison tables above.",
        )
    except ClientError as e:
        msg = str(e)
        print(f"\n✗ AWS error: {msg}", file=sys.stderr)
        if "Token exchange failed" in msg and "400" in msg:
            print(
                "\n  Okta rejected the OBO exchange with HTTP 400. AgentCore hides the\n"
                "  specific Okta error (invalid_grant / unauthorized_client / invalid_scope\n"
                "  / invalid_dpop_proof / etc.), so walk the checklist:\n"
                "\n"
                "    1. The Service App has the Token Exchange grant enabled and DPoP\n"
                "       disabled (Proof of possession = Not required). Okta admin →\n"
                "       your Service App → General → General Settings → Edit.\n"
                "    2. The custom authorization server has an access policy assigned to\n"
                "       the Service App, with a rule that has grant type = Token Exchange\n"
                "       and grants the downstream scope (oboe2e.apiC.read).\n"
                "    3. The downstream scope is marked 'Include in public metadata' AND\n"
                "       'Set as a default scope' — both required or OBO fails at exchange.\n"
                "    4. The inbound user token's aud matches OKTA_AUDIENCE.\n"
                "    5. The Service App's client secret in .env matches what's on the app\n"
                "       in Okta. If it was rotated, update .env and re-run\n"
                "       teardown.py + 01_create_providers.py.\n"
                "\n"
                "  Full troubleshooting guide: IDP_SETUP.md → Troubleshooting section.",
                file=sys.stderr,
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
