"""Demo: Invoke employee-data-tool through the gateway and observe PII masking.

Lists tools, then calls employee_data_tool to show how the RESPONSE interceptor
uses Bedrock Guardrails to anonymize PII (emails, addresses, financial data)
before the response reaches the client.

Requires GATEWAY_URL and COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/data-masking/invoke.py
"""

import json
import os
import sys

import boto3
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_mcp_client import GatewayMCPClient


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val


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
    load_env()

    gateway_url = get_required_env("GATEWAY_URL")
    cognito_stack = get_required_env("COGNITO_STACK_NAME")
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

    def token_fn():
        return get_token(token_endpoint, gw_client_id, gw_client_secret, gw_scope)

    mcp = GatewayMCPClient(gateway_url, token_fn, protocol_version="2025-11-25")

    print(f"Gateway URL: {gateway_url}\n")

    # --- List tools ---
    print("=" * 60)
    print("tools/list")
    print("=" * 60)
    raw = mcp.list_tools()
    if "error" in raw:
        print(f"  ERROR: {json.dumps(raw['error'], indent=2)}")
        return
    all_tools = mcp.list_all_tools()
    for t in all_tools:
        print(f"  {t['name']}: {t.get('description', '')}")
    print(f"\n  ({len(all_tools)} tools)")

    # --- Invoke employee_data_tool ---
    print("\n" + "=" * 60)
    print("Employee Data Tool (PII should be anonymized)")
    print("=" * 60)

    employee_tool = next(
        (t["name"] for t in all_tools if "employee" in t["name"].lower()),
        None,
    )
    if employee_tool:
        result = mcp.call_tool(employee_tool, {"employee_id": "EMP-98765"})
        print(json.dumps(result, indent=2))

        print("\nExpected behavior:")
        print("  - contact_info (email) -> [EMAIL]")
        print("  - mailing_info (address) -> [ADDRESS]")
        print("  - bank_account -> [US_BANK_ACCOUNT_NUMBER]")
        print("  - routing_number -> [US_BANK_ROUTING_NUMBER]")
        print("  - credit_card -> [CREDIT_DEBIT_CARD_NUMBER]")
        print("  - cvv -> [CREDIT_DEBIT_CARD_CVV]")
        print("  - card_expiry -> [CREDIT_DEBIT_CARD_EXPIRY]")
        print("  - pin -> [PIN]")
        print("  - tax_id -> [US_INDIVIDUAL_TAX_IDENTIFICATION_NUMBER]")
        print(
            "  - Non-sensitive fields (employee_id, department, status) remain unchanged"
        )
    else:
        print("  Employee data tool not found. Run deploy.py first.")


if __name__ == "__main__":
    main()
