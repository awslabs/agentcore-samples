"""
Invoke AgentCore Gateway MCP tools with a JWT from a private Keycloak instance.
"""

import argparse
import json
import urllib.request
import urllib.parse


def get_keycloak_token(keycloak_url: str, client_id: str, client_secret: str,
                       realm: str = "orion") -> str:
    """Get a client_credentials JWT from Keycloak."""
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(
        f"{keycloak_url}/realms/{realm}/protocol/openid-connect/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["access_token"]


def mcp_call(gateway_url: str, token: str, method: str, params: dict = None, req_id: int = 1):
    """Make an MCP JSON-RPC call to the gateway."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "id": req_id,
        **({"params": params} if params else {}),
    }).encode()
    req = urllib.request.Request(gateway_url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Invoke AgentCore Gateway with Keycloak JWT")
    parser.add_argument("--keycloak-url", required=True, help="Keycloak base URL")
    parser.add_argument("--gateway-url", required=True, help="Gateway MCP endpoint URL")
    parser.add_argument("--client-id", default="content-export-adapter")
    parser.add_argument("--client-secret", default="test-secret-12345")
    args = parser.parse_args()

    print("Getting Keycloak token...")
    token = get_keycloak_token(args.keycloak_url, args.client_id, args.client_secret)
    print(f"✅ Token obtained ({len(token)} chars)\n")

    print("=== tools/list ===")
    result = mcp_call(args.gateway_url, token, "tools/list")
    tools = result.get("result", {}).get("tools", [])
    for t in tools:
        print(f"  - {t['name']}: {t['description']}")

    print("\n=== tools/call: check_enforcement_status ===")
    result = mcp_call(args.gateway_url, token, "tools/call", {
        "name": "ban-appeal-tools___check_enforcement_status",
        "arguments": {"player_id": "1004942767660"},
    }, req_id=2)
    print(f"  {json.dumps(result, indent=2)}")

    print("\n=== tools/call: submit_appeal ===")
    result = mcp_call(args.gateway_url, token, "tools/call", {
        "name": "ban-appeal-tools___submit_appeal",
        "arguments": {"player_id": "1004942767660", "reason": "First offense"},
    }, req_id=3)
    print(f"  {json.dumps(result, indent=2)}")
