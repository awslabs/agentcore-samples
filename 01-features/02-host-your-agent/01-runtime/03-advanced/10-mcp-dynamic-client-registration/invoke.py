"""
Invoke the MCP server with Auth0 Dynamic Client Registration.

This MCP server uses OAuth authentication via Auth0. Invocation requires the
full OAuth flow (browser-based authorization), which is handled by the
mcp_auth0_client.py script.

Usage:
    # Set required environment variables
    export AGENT_ARN="arn:aws:bedrock-agentcore:<region>:<account>:runtime/<id>"
    export AUTH0_AUDIENCE="your-api-identifier"

    # Run the Auth0 MCP client directly
    python mcp_auth0_client.py
"""

import json
import sys


def main():
    try:
        with open("runtime_config.json") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found. Run deploy.py first.")
        sys.exit(1)

    print("═══ MCP Dynamic Client Registration (Auth0) ═══\n")
    print(f"Runtime ARN: {config['runtime_arn']}")
    print(f"Region: {config['region']}\n")
    print("This MCP server uses OAuth authentication via Auth0.")
    print("Standard invoke_agent_runtime calls won't work — you need")
    print("the full OAuth flow with browser-based authorization.\n")
    print("To invoke the server:\n")
    print("  1. Set environment variables:")
    print(f'     export AGENT_ARN="{config["runtime_arn"]}"')
    print('     export AUTH0_AUDIENCE="your-api-identifier"\n')
    print("  2. Run the Auth0 MCP client:")
    print("     python mcp_auth0_client.py\n")
    print("The client will:")
    print("  - Discover the Auth0 OAuth endpoints")
    print("  - Register itself as a client via Dynamic Client Registration")
    print("  - Open a browser for user authorization")
    print("  - Exchange the authorization code for an access token")
    print("  - Connect to the MCP server and call tools\n")
    print("See README.md for Auth0 tenant configuration details.")


if __name__ == "__main__":
    main()
