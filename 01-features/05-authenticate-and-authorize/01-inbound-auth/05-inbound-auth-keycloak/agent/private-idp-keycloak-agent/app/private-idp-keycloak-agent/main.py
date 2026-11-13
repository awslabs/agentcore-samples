# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""AgentCore runtime that gets an OAuth token from a private Keycloak IdP.

AgentCore Identity requests the token from Keycloak in a VPC through VPC Lattice.

The agent returns the token claims and the gateway response. It doesn't return the token.
"""

import base64
import json
import os
import urllib.request

from bedrock_agentcore.identity import requires_access_token
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()
log = app.logger

CREDENTIAL_PROVIDER_NAME = os.environ.get("CREDENTIAL_PROVIDER_NAME", "keycloak-private-idp")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")


@requires_access_token(
    provider_name=CREDENTIAL_PROVIDER_NAME,
    scopes=["openid"],
    auth_flow="M2M",
)
def fetch_token_from_private_idp(*, access_token: str) -> dict:
    """Acquire an OAuth token from the private Keycloak IdP via AgentCore Identity.

    The @requires_access_token decorator handles:
    1. Obtaining a workload identity token for this runtime
    2. Exchanging it for an OAuth access token via the credential provider
    3. The credential provider reaches Keycloak over VPC Lattice (private connectivity)
    4. Injecting the resulting access_token into this function

    Returns the token claims and, if GATEWAY_URL is set, the gateway response.
    In a real application, you would use the token to call a downstream API.
    """
    result = {"success": True}

    parts = access_token.split(".")
    if len(parts) == 3:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        result["claims"] = payload
    else:
        result["token_type"] = "opaque"

    if GATEWAY_URL:
        gateway_result = call_gateway(access_token)
        result["gateway"] = gateway_result

    return result


def call_gateway(access_token: str) -> dict:
    """Call AgentCore Gateway's tools/list with the Keycloak token as a Bearer token."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }
    ).encode()

    req = urllib.request.Request(GATEWAY_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {access_token}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            return json.loads(resp.read())
    except Exception as e:  # noqa: BLE001 - report gateway errors in the agent response
        return {"error": str(e)}


@app.entrypoint
async def invoke(payload, context):
    """Entrypoint for the AgentCore runtime.

    Demonstrates acquiring an OAuth token from a private Keycloak IdP.
    Returns the token claims, not the token.
    """
    log.info("Acquiring OAuth token from private Keycloak IdP...")

    try:
        token_info = fetch_token_from_private_idp()
        log.info("Token acquired successfully: client_id=%s", token_info.get("client_id"))
        return json.dumps(token_info, indent=2)
    except Exception as e:  # noqa: BLE001 - report token errors in the agent response
        log.error("Failed to acquire token: %s", e)
        return json.dumps({"success": False, "error": str(e)})


if __name__ == "__main__":
    app.run()
