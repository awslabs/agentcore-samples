"""
Strands agent for the OBO Use Case 1 real-world example (Okta flavor).

Runs on AgentCore Runtime. Inbound auth (configured outside this file via
agentcore/agentcore.json) uses Okta's OIDC discovery — the Runtime validates
the caller's JWT before the handler runs.

Inside the handler we:
  1. Read the inbound user JWT from the Authorization header.
  2. Perform the OBO exchange via AgentCore Identity using Okta's RFC 8693
     token-exchange grant, passing `subject_token_type` and `audiences`.
  3. Call Okta's /v1/userinfo endpoint with the OBO'd token.
  4. Let the LLM compose a natural-language response from the profile.

Deploy:
    After running `agentcore create --defaults` and
    `python deploy/02_patch_agentcore_json.py`, copy this file as:
        cp agent/agent.py app/<AGENT_RUNTIME_NAME>/main.py
    then run `agentcore deploy`.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
import requests
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from botocore.exceptions import ClientError
from strands import Agent, tool


WORKLOAD_NAME = os.environ.get("WORKLOAD_NAME", "obo-usecase1-okta-realworld")
ACTOR_PROVIDER_NAME = os.environ.get("ACTOR_PROVIDER_NAME", "obo-uc1-okta-realworld-actor")
DOWNSTREAM_SCOPE = os.environ.get("DOWNSTREAM_SCOPE", "openid profile email")
OKTA_AUDIENCE = os.environ.get("OKTA_AUDIENCE", "api://default")
OKTA_DOMAIN = os.environ.get("OKTA_DOMAIN", "")
OKTA_AUTH_SERVER_ID = os.environ.get("OKTA_AUTH_SERVER_ID", "default")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

USERINFO_URL = f"https://{OKTA_DOMAIN}/oauth2/{OKTA_AUTH_SERVER_ID}/v1/userinfo" if OKTA_DOMAIN else ""

app = BedrockAgentCoreApp()
log = app.logger
_ac_identity = boto3.client("bedrock-agentcore", region_name=AWS_REGION)


# Per-request state for passing the inbound JWT into the tool. The @tool
# decorator doesn't let us inject the JWT at invocation time, so we set it
# on module state before calling agent().
_current_user_jwt: dict[str, str] = {}


def _obo_exchange(user_token: str) -> str:
    """Swap an inbound user JWT for a downstream-scoped OBO access token.

    Okta's OBO uses RFC 8693 token exchange. Three things the SDK needs:
      - customParameters: {"subject_token_type": "...:token-type:access_token"}
      - audiences: [<Okta auth server audience>]
      - scopes: the downstream scope set the user consented to

    AgentCore Identity handles client authentication to Okta using the
    Service App credentials stored on the credential provider — we don't see
    or handle the client secret from this code.
    """
    workload_token = _ac_identity.get_workload_access_token_for_jwt(
        workloadName=WORKLOAD_NAME,
        userToken=user_token,
    )["workloadAccessToken"]

    # DOWNSTREAM_SCOPE is a space-separated string (e.g. "openid profile email");
    # the SDK takes a list.
    scopes = DOWNSTREAM_SCOPE.split()

    return _ac_identity.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=ACTOR_PROVIDER_NAME,
        scopes=scopes,
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        customParameters={
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        },
        audiences=[OKTA_AUDIENCE],
    )["accessToken"]


@tool
def get_my_profile() -> dict[str, Any]:
    """Look up the caller's Okta profile using On-Behalf-Of.

    Flow (two independent calls, each illustrating one aspect of OBO):

      1. **OBO exchange.** The agent swaps the inbound user token for a new
         token audienced at the downstream API with a custom scope. This is
         the real OBO pattern you'd use in production to call your own
         resource server.

      2. **Userinfo call.** In this example the downstream target is Okta's
         `/v1/userinfo`, which requires the `openid` scope. Okta refuses to
         issue `openid` on Token Exchange, so the userinfo call uses the
         *inbound* user token (which already has `openid profile email`).
         In a real deployment with a custom downstream API accepting the
         custom scope, you'd call that API with the OBO'd token instead.

    The tool returns the userinfo response plus a small set of claims from
    the OBO'd token to prove the exchange happened. The LLM never sees
    either token.
    """
    user_jwt = _current_user_jwt.get("token")
    if not user_jwt:
        return {"error": "No user JWT available on the request context."}

    if not USERINFO_URL:
        return {
            "error": (
                "Agent deployment missing OKTA_DOMAIN environment variable. "
                "Re-run `python ../deploy/02_patch_agentcore_json.py` followed by "
                "`agentcore deploy -y -v` to push the Okta coordinates into the "
                "deployed container, then try again."
            ),
            "_hint_for_llm": "Report this error verbatim to the user. Do not paraphrase.",
        }

    # OBO: exchange the inbound user token for a custom-scope downstream token.
    # In production, you'd present `downstream_token` to your own resource server;
    # here we just decode its claims to prove the exchange succeeded.
    try:
        downstream_token = _obo_exchange(user_jwt)
    except ClientError as e:
        log.error("OBO exchange failed: %s", e)
        return {"error": f"OBO exchange failed: {e}"}

    obo_proof = _decode_token_claims(downstream_token)

    # Downstream call: /v1/userinfo requires `openid`, which Okta does not
    # allow on Token Exchange. So we call userinfo with the *inbound* user
    # token — it still has the scopes the user granted on sign-in.
    try:
        r = requests.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {user_jwt}"},
            timeout=30,
        )
        r.raise_for_status()
        profile = r.json()
    except requests.HTTPError as e:
        log.error("Userinfo call failed: %s", e)
        return {
            "error": f"Userinfo call failed: {e.response.status_code if e.response else 'unknown'}",
            "detail": e.response.text if e.response else "",
        }

    return {
        "profile": profile,
        "obo_proof": {
            "downstream_token_sub": obo_proof.get("sub"),
            "downstream_token_cid": obo_proof.get("cid"),
            "downstream_token_scp": obo_proof.get("scp"),
            "downstream_token_aud": obo_proof.get("aud"),
        },
    }


def _decode_token_claims(token: str) -> dict[str, Any]:
    """Decode a JWT's payload without verifying the signature.

    Used only to surface OBO-proof claims in the tool response. Signature
    verification was already done by the Runtime on inbound and by AgentCore
    Identity implicitly on the exchange response.
    """
    import base64 as _b64
    import json as _json

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return _json.loads(_b64.urlsafe_b64decode(payload))
    except Exception:
        return {}


SYSTEM_PROMPT = """
You are an assistant that answers the user's questions about their own Okta
profile (name, email, preferred username, zoneinfo, etc.).

To answer:
  1. Call the get_my_profile tool (it takes no arguments — it already knows who the caller is).
  2. The tool returns a dict with a `profile` field containing the user's
     profile data. Read fields from `profile` to answer the user's question.
     There is also an `obo_proof` field with token claims proving the OBO
     exchange happened — do NOT expose or discuss that field to the user
     unless they explicitly ask about OBO or tokens.
  3. Answer in one or two sentences.

Do not fabricate information not present in the profile.
"""


_agent = None


def _get_or_create_agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = Agent(
            model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            system_prompt=SYSTEM_PROMPT,
            tools=[get_my_profile],
        )
    return _agent


@app.entrypoint
async def invoke(payload, context):
    """Runtime entrypoint.

    Async generator — errors are yielded as plain strings rather than returned.
    """
    log.info("Invoking agent for OBO use case 1 (Okta)")

    # Extract the JWT from the incoming request. The Runtime has already
    # validated signature, issuer, and audience — we just need the raw token
    # to pass to AgentCore Identity as the OBO subject.
    headers = context.request_headers or {}
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if not auth.startswith("Bearer "):
        yield "ERROR: Missing or malformed Authorization header"
        return
    user_jwt = auth.split(" ", 1)[1]

    _current_user_jwt["token"] = user_jwt
    try:
        prompt = payload.get("prompt", "What is my name?")
        agent = _get_or_create_agent()
        stream = agent.stream_async(prompt)
        async for event in stream:
            if "data" in event and isinstance(event["data"], str):
                yield event["data"]
    finally:
        _current_user_jwt.pop("token", None)


if __name__ == "__main__":
    app.run()
