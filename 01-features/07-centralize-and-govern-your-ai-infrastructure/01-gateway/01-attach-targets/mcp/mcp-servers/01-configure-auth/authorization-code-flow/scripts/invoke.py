"""Demo: invoke the configured MCP server's tools through AgentCore Gateway.

Lists tools, invokes the profile's demo tool, and handles URL-mode elicitation
by printing the callback-server command that completes session binding.

The demo tool and its arguments come from the profile named by the required
flag -- see mcp_config.py.

Requires GATEWAY_URL, COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/invoke.py --github
"""

import json
import os
import sys

import boto3
import requests
from gateway_mcp_client import GatewayMCPClient
from mcp_config import load_env, select_profile


def get_token(token_endpoint, client_id, client_secret, scope):
    response = requests.post(
        token_endpoint,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def main():
    profile = select_profile(__doc__)
    load_env()

    gateway_url = os.environ.get("GATEWAY_URL")
    cognito_stack = os.environ.get("COGNITO_STACK_NAME", "agentcore-gateway-lab")
    if not gateway_url:
        print("ERROR: GATEWAY_URL not set. Export it or add to the script .env")
        sys.exit(1)

    region = boto3.Session().region_name
    cfn = boto3.client("cloudformation", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)

    outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=cognito_stack)["Stacks"][0]["Outputs"]
    }
    gw_client_id = outputs["GatewayClientId"]
    gw_scope = outputs["GatewayScope"]
    gw_client_secret = cognito.describe_user_pool_client(
        UserPoolId=outputs["UserPoolId"], ClientId=gw_client_id
    )["UserPoolClient"]["ClientSecret"]
    token_endpoint = outputs["TokenEndpoint"]

    access_token = get_token(token_endpoint, gw_client_id, gw_client_secret, gw_scope)

    mcp = GatewayMCPClient(
        gateway_url, lambda: access_token, protocol_version="2025-11-25"
    )

    print(f"Gateway URL: {gateway_url}\n")

    # The 2025-11-25 gateway is session-enabled: it issues an Mcp-Session-Id on
    # initialize and rejects every later request that does not echo it back
    # ("Missing required Mcp-Session-Id header"). initialize() captures that id
    # so list_tools/call_tool carry it automatically.
    init = mcp.initialize()
    if init["http_status"] != 200:
        print(f"  ERROR: initialize failed ({init['http_status']})")
        print(json.dumps(init["result"], indent=2)[:2000])
        return

    print("=" * 60)
    print("tools/list")
    print("=" * 60)
    raw = mcp.list_tools()
    if "error" in raw:
        print(f"  ERROR: {json.dumps(raw['error'], indent=2)}")
        return
    all_tools = mcp.list_all_tools()
    for t in all_tools:
        print(f"  {t['name']}")
    print(f"\n  ({len(all_tools)} tools)")

    demo_tool = profile["demoTool"]
    print("\n" + "=" * 60)
    print(f"tools/call — {demo_tool}")
    print("=" * 60)
    # Substring match, not equality: the gateway prefixes tool names with the
    # target name, so the advertised name is <target>___<tool>.
    resolved_tool = next(
        (t["name"] for t in all_tools if demo_tool in t["name"]),
        None,
    )
    if not resolved_tool:
        print(f"  {demo_tool} tool not found")
        return

    # This gateway has response streaming enabled, so a plain tools/call opens
    # an SSE channel (text/event-stream) that .json() cannot parse. Force
    # Accept: application/json to get a single JSON document back -- the
    # URL-mode auth elicitation (-32042) arrives in that one document too.
    call = mcp.call_tool_json_only(resolved_tool, profile["demoArgs"])
    try:
        result = json.loads(call["body"])
    except json.JSONDecodeError:
        print(
            f"  ERROR: non-JSON response ({call['http_status']}, {call['content_type']})"
        )
        print(call["body"][:2000])
        return

    # Check for URL elicitation
    error = result.get("error", {})
    if error.get("code") == -32042:
        elicitations = error.get("data", {}).get("elicitations", [])
        if elicitations and elicitations[0].get("mode") == "url":
            auth_url = elicitations[0]["url"]
            print(f"\n  {profile['displayName']} authorization required.")
            print(f"  Authorization URL: {auth_url}")
            print("\n  Start the callback server in another terminal:")
            print(
                f"  uv run python scripts/callback_server.py"
                f' --user-token "{access_token}"'
                f' --auth-url "{auth_url}"'
            )
            print("\n  After authorizing, run this script again.")
            return

    print(json.dumps(result, indent=2)[:2000])


if __name__ == "__main__":
    main()
