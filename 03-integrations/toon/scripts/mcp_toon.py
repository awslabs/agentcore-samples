import boto3
import requests
import json
import argparse


def get_cognito_config(environment="dev"):
    """Fetch Cognito client config from Secrets Manager."""
    secrets_client = boto3.client("secretsmanager")
    secret_name = f"toon-{environment}/gateway/client-config"

    response = secrets_client.get_secret_value(SecretId=secret_name)
    return json.loads(response["SecretString"])


def get_gateway_url(environment="dev"):
    """Fetch Gateway URL from SSM Parameter Store."""
    ssm_client = boto3.client("ssm")
    param_name = f"/app/toon/{environment}/gateway/url"

    response = ssm_client.get_parameter(Name=param_name)
    return response["Parameter"]["Value"]


def fetch_access_token(client_id, client_secret, token_url):
    """Fetch OAuth access token using client credentials flow."""
    response = requests.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def list_tools(gateway_url, access_token):
    """List available tools from the MCP gateway."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    payload = {"jsonrpc": "2.0", "id": "list-tools-request", "method": "tools/list"}

    response = requests.post(gateway_url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()


def call_tool(gateway_url, access_token, tool_name, arguments=None):
    """Call a tool on the MCP gateway."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    payload = {
        "jsonrpc": "2.0",
        "id": "call-tool-request",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments or {},
        },
    }

    response = requests.post(gateway_url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(description="List or invoke tools from MCP Gateway")
    parser.add_argument(
        "--environment", "-e", default="dev", help="Environment (dev, test, prod)"
    )
    parser.add_argument(
        "--gateway-url", "-g", help="Gateway MCP endpoint URL (fetched from SSM if not provided)"
    )
    parser.add_argument(
        "--invoke", "-i", help="Tool name to invoke (e.g., customers-crud___batch_get_customers)"
    )
    parser.add_argument(
        "--args", "-a", help="JSON arguments for the tool (e.g., '{\"region\": \"Northeast\"}')"
    )
    args = parser.parse_args()

    # Get Cognito config from Secrets Manager
    print(f"Fetching Cognito config for environment: {args.environment}")
    config = get_cognito_config(args.environment)

    # Get gateway URL from SSM if not provided
    gateway_url = args.gateway_url
    if not gateway_url:
        print("Fetching gateway URL from SSM...")
        gateway_url = get_gateway_url(args.environment)

    # Fetch access token
    print("Fetching access token...")
    access_token = fetch_access_token(
        config["client_id"],
        config["client_secret"],
        config["token_endpoint"],
    )

    if args.invoke:
        # Call a specific tool
        tool_args = json.loads(args.args) if args.args else {}
        print(f"Invoking tool: {args.invoke}")
        if tool_args:
            print(f"With arguments: {tool_args}")
        result = call_tool(gateway_url, access_token, args.invoke, tool_args)
        print(json.dumps(result, indent=2))
    else:
        # List tools
        print(f"Listing tools from: {gateway_url}")
        tools = list_tools(gateway_url, access_token)
        print(json.dumps(tools, indent=2))


if __name__ == "__main__":
    main()
