"""
Interactive, guided walkthrough of Use Case 1 (Entra flavor).

This script is not just a runnable example — it's a teaching tool. It pauses
at each chapter, explains what is about to happen, runs the API call, then
highlights what changed in the returned tokens so you can see OBO doing its job.

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

import boto3
import requests
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
    agent_scope: str,
    user_alias: str,
) -> str:
    """Obtain a user JWT — either from AgentCore's cache or via a fresh 3LO."""
    chapter(
        1,
        "Sign the user in (simulating the frontend)",
        "Get a JWT that says 'Alice is signed in and has consented to the agent'",
    )
    explain("""
In production, your frontend handles this. It redirects the user to your IdP,
the user signs in, and the frontend ends up with a JWT whose `aud` claim points
at the agent. The frontend then hands that JWT to the agent.

For this local example, we use AgentCore Identity's USER_FEDERATION flow to
simulate exactly what the frontend would produce. The result is identical:
a standard Entra-issued JWT you can feed into the next chapter.
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
        scopes=[agent_scope],
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
                scopes=[agent_scope],
                oauth2Flow="USER_FEDERATION",
                resourceOauth2ReturnUrl=CALLBACK_URL,
            )
            user_token = token_resp["accessToken"]
        finally:
            server.shutdown()

    success("User JWT obtained")
    return user_token


def chapter_2_inspect_inbound(user_token: str, agent_client_id: str) -> dict:
    """Decode the inbound user JWT and point out the claims that matter."""
    chapter(
        2,
        "Inspect the inbound user JWT",
        "Confirm this token really is 'for the agent, about the user'",
    )
    explain("""
This is the JWT the agent handler would receive as the bearer token on an
incoming request. Before calling any downstream API, we decode it to confirm:

  • `aud` (audience)  — who this token is FOR. Must match the agent's client ID.
  • `oid` (object id) — stable identifier of the signed-in user. This is what
                         we want to preserve across the OBO exchange.
  • `scp` (scope)      — what the user consented the token-bearer to do.
  • `appid`            — the client that requested the token. In our case, the
                         credential provider acting as the "frontend".

The IdP will only agree to OBO if `aud` matches our agent app. If it doesn't,
the token was never meant for us — we can't use it.
""")
    pause()

    claims = decode_jwt_claims(user_token)
    show_claims("Inbound user JWT claims", claims, highlight=["aud", "oid", "scp", "appid"])

    if claims.get("aud") == agent_client_id:
        success(f"aud matches AGENT_CLIENT_ID ({agent_client_id}) — token is for us")
    else:
        info(f"aud is {claims.get('aud')!r}, AGENT_CLIENT_ID is {agent_client_id!r} — would fail OBO")

    observe(
        "Key property: user identity is encoded here",
        f"The `oid` claim ({claims.get('oid', '?')}) identifies this user stably. "
        "Remember this value — we will look for it again in the OBO'd token.",
    )
    pause()
    return claims


def chapter_3_obo_exchange(
    ac_identity,
    workload_name: str,
    actor_provider_name: str,
    graph_scope: str,
    user_token: str,
) -> str:
    """Perform the on-behalf-of exchange and return the Graph token."""
    chapter(
        3,
        "Perform the OBO token exchange",
        "Swap the user JWT for a Graph-scoped token — all without user interaction",
    )
    explain("""
This is where OBO actually happens. Two AgentCore Identity API calls:

  1. GetWorkloadAccessTokenForJWT  — wraps the inbound user JWT into an
     AgentCore-internal token. AgentCore will unwrap it to get the user JWT
     back when it talks to the IdP.

  2. GetResourceOauth2Token with oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE —
     AgentCore POSTs to Entra's token endpoint with:
         grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
         assertion=<user JWT>
         scope=<graph scope>
         requested_token_use=on_behalf_of
     Entra validates everything, confirms the user has consented, and mints
     a new token with aud=graph.microsoft.com.

We never handle the client secret, never build the POST body ourselves, and
the user is NOT re-prompted to consent (they consented once, at sign-in).
""")
    pause()

    action("Calling GetWorkloadAccessTokenForJWT to wrap the user token")
    obo_wl_resp = ac_identity.get_workload_access_token_for_jwt(
        workloadName=workload_name,
        userToken=user_token,
    )
    obo_workload_token = obo_wl_resp["workloadAccessToken"]
    success("User JWT wrapped into an AgentCore workload token")

    action("Calling GetResourceOauth2Token with oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE")
    obo_resp = ac_identity.get_resource_oauth2_token(
        workloadIdentityToken=obo_workload_token,
        resourceCredentialProviderName=actor_provider_name,
        scopes=[graph_scope],
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
    )
    success("OBO exchange complete — we have a Graph-scoped access token")
    return obo_resp["accessToken"]


def chapter_4_compare_tokens(inbound_claims: dict, graph_token: str) -> dict:
    """Side-by-side comparison that makes the OBO guarantee obvious."""
    chapter(
        4,
        "Compare the inbound and outbound tokens",
        "See what changed (audience, scope, actor) and what stayed the same (user)",
    )
    explain("""
This is the moment of truth. Look for three things:

  1. `aud` MUST change — it used to point at the agent app, now it points
     at Microsoft Graph. Tokens are audience-scoped for a reason: if the
     new token still pointed at the agent, Graph would refuse it.

  2. `scp` MUST change — the original token had our custom `access_as_user`
     scope; the new one has `User.Read`, the permission against Graph.

  3. `oid` MUST stay the same — this is the OBO guarantee. The user is the
     same human across the exchange.

Entra's flavor does NOT include a nested `act` claim (that's Okta). Instead,
`appid`/`azp` on the new token identifies the agent as the actor.
""")
    pause()

    graph_claims = decode_jwt_claims(graph_token)
    compare_claims(
        "Inbound (user → agent)",
        inbound_claims,
        "Outbound (agent → Graph)",
        graph_claims,
        keys=["aud", "oid", "scp", "appid", "iss"],
    )

    # Verify the OBO invariants
    if inbound_claims.get("oid") == graph_claims.get("oid"):
        success("USER IDENTITY PRESERVED — oid matches on both tokens")
    else:
        info("oid changed — this would mean the user identity was lost. Check your setup.")

    if inbound_claims.get("aud") != graph_claims.get("aud"):
        success("AUDIENCE ROTATED — new token is for Graph, not the agent")
    else:
        info("aud did not change — the OBO exchange may not have happened")

    observe(
        "This is the OBO guarantee in action",
        "Same user. Different audience. Different scope. No extra consent. "
        "The agent now holds a token it can send to Graph, and Graph will accept "
        "it and run as the user, not as the agent.",
    )
    pause()
    return graph_claims


def chapter_5_call_graph(graph_token: str) -> None:
    """Actually call Graph to prove the token works end-to-end."""
    chapter(
        5,
        "Use the OBO token against Microsoft Graph",
        "Prove end-to-end that the token works by calling /me",
    )
    explain("""
The OBO token goes in the Authorization header of an ordinary HTTPS request
to Graph. Graph validates the token's signature, issuer, audience, and scopes,
then runs the /me endpoint as the user who was identified in `oid`.
""")
    pause()

    action("GET https://graph.microsoft.com/v1.0/me with the OBO token")
    r = requests.get(
        "https://graph.microsoft.com/v1.0/me",
        headers={"Authorization": f"Bearer {graph_token}"},
        timeout=30,
    )
    r.raise_for_status()
    profile = r.json()

    success(f"HTTP {r.status_code} — Graph returned the user's profile")
    print()
    for key in (
        "displayName",
        "mail",
        "userPrincipalName",
        "jobTitle",
        "officeLocation",
    ):
        if key in profile and profile[key]:
            print(f"    {key:<22} = {profile[key]}")

    observe(
        "What just happened",
        "Your agent called Microsoft Graph on behalf of the user, using the user's "
        "permissions (not the agent's), without re-prompting for consent. "
        "That's the complete OBO loop.",
    )


def main() -> None:
    load_dotenv()
    region = os.environ.get("AWS_REGION", "us-west-2")

    workload_name = must_env("WORKLOAD_NAME")
    client_provider_name = must_env("CLIENT_PROVIDER_NAME")
    actor_provider_name = must_env("ACTOR_PROVIDER_NAME")
    agent_client_id = must_env("AGENT_CLIENT_ID")
    agent_scope = must_env("AGENT_SCOPE")
    graph_scope = must_env("GRAPH_SCOPE")
    user_alias = must_env("USER_ALIAS")

    ac_identity = boto3.client("bedrock-agentcore", region_name=region)

    header(
        "Use Case 1 (Entra) — Interactive OBO Walkthrough",
        "User → Frontend → Agent → Microsoft Graph, with OBO at the last hop",
    )
    explain(f"""
You will walk through five chapters. Each one explains the step, runs the
API call, and points out what changed. Press Enter at the ↵ prompts; set
INTERACTIVE_NO_PAUSE=1 in your shell to skip the pauses.

  Region:              {region}
  Workload:            {workload_name}
  Upstream scope:      {agent_scope}
  Downstream scope:    {graph_scope}
""")
    pause("Press Enter to start Chapter 1")

    try:
        user_token = chapter_1_sign_in(
            ac_identity,
            workload_name,
            client_provider_name,
            agent_scope,
            user_alias,
        )
        inbound_claims = chapter_2_inspect_inbound(user_token, agent_client_id)
        graph_token = chapter_3_obo_exchange(
            ac_identity,
            workload_name,
            actor_provider_name,
            graph_scope,
            user_token,
        )
        chapter_4_compare_tokens(inbound_claims, graph_token)
        chapter_5_call_graph(graph_token)

        header(
            "✓ Walkthrough complete",
            "Review what just happened in the comparison tables above.",
        )
    except ClientError as e:
        print(f"\nAWS error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.HTTPError as e:
        print(
            f"\nGraph call failed: {e}\n{e.response.text if e.response else ''}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
