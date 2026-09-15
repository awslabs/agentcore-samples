"""
Validate the full authentication and tool invocation chain.

Authenticates as a real user via MSAL device code flow, sends the token
to the gateway, and confirms that a customer service tool returns data.

Usage:
    python validate_end_to_end.py --interactive
    python validate_end_to_end.py --token "eyJ..."
"""

import argparse
import json
import os
import urllib.error
import urllib.request


GATEWAY_URL = os.environ.get(
    "GATEWAY_URL",
    "https://<your-gateway-id>.gateway.bedrock-agentcore.<your-region>.amazonaws.com/mcp",
)
CLIENT_ID = os.environ.get("ENTRA_AGENT_CLIENT_ID", "<your-gateway-client-id>")
TENANT = os.environ.get("ENTRA_TENANT_ID", "<your-tenant-id>")


def authenticate_user():
    """Run MSAL device code flow to get a user-scoped access token."""
    import msal

    app = msal.PublicClientApplication(CLIENT_ID, authority=f"https://login.microsoftonline.com/{TENANT}")
    flow = app.initiate_device_flow(scopes=[f"{CLIENT_ID}/.default"])
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description", "Authentication failed"))
    return result["access_token"]


def invoke_tool(token: str, tool: str, arguments: dict) -> dict:
    """Send a tools/call request to the gateway."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    })
    req = urllib.request.Request(
        GATEWAY_URL,
        data=payload.encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "detail": e.read().decode()[:300]}


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--interactive", action="store_true", help="Sign in via device code")
    group.add_argument("--token", help="Pre-obtained user JWT")
    parser.add_argument("--customer", default="CUST-001")
    args = parser.parse_args()

    token = authenticate_user() if args.interactive else args.token.removeprefix("Bearer ")

    print(f"\nGateway: {GATEWAY_URL}")
    print(f"Tool:    cx-private-mcp-tools___lookup_customer")
    print(f"Input:   customer_id={args.customer}\n")

    result = invoke_tool(token, "cx-private-mcp-tools___lookup_customer", {"customer_id": args.customer})
    print(json.dumps(result, indent=2))

    if result.get("result", {}).get("isError") is False:
        print("\n✅ End-to-end validation passed — user identity preserved through delegation chain")
    else:
        print("\n❌ Validation failed")


if __name__ == "__main__":
    main()
