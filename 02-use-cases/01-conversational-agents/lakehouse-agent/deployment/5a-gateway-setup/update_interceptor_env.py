#!/usr/bin/env python3
"""
Update Interceptor Lambda Environment Variables

This script updates the interceptor Lambda function's environment variables
to use the correct Cognito configuration from SSM Parameter Store.

Usage:
    python update_interceptor_env.py
"""

import os
import sys

import boto3

# Make the repo's utils/ importable (idp_config lives there).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.idp_config import get_idp_provider


def main():
    print("=" * 70)
    print("Update Interceptor Lambda Environment Variables")
    print("=" * 70)

    session = boto3.Session()
    region = session.region_name
    ssm = boto3.client("ssm", region_name=region)
    lambda_client = boto3.client("lambda", region_name=region)

    # IdP selector (DR-8) + config load. `new_vars` are the env keys to set on
    # the Lambda (always includes IDP_PROVIDER so the Lambda code branches).
    idp_provider = get_idp_provider(ssm)
    print(f"\n📋 Loading correct {idp_provider} configuration from SSM...")
    new_vars = {"IDP_PROVIDER": idp_provider}
    try:
        if idp_provider == "cognito":
            # [COGNITO] upstream verbatim keys
            user_pool_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-user-pool-id")["Parameter"]["Value"]
            app_client_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-app-client-id")["Parameter"]["Value"]
            new_vars.update(
                {
                    "COGNITO_REGION": region,
                    "COGNITO_USER_POOL_ID": user_pool_id,
                    "COGNITO_APP_CLIENT_ID": app_client_id,
                }
            )
            print(f"   User Pool ID: {user_pool_id}")
            print(f"   App Client ID: {app_client_id}")
        else:  # okta
            # [OKTA] canonical §6 keys
            okta_org_url = ssm.get_parameter(Name="/app/lakehouse-agent/okta-org-url")["Parameter"]["Value"]
            okta_auth_server_id = ssm.get_parameter(Name="/app/lakehouse-agent/okta-auth-server-id")["Parameter"][
                "Value"
            ]
            okta_audience = ssm.get_parameter(Name="/app/lakehouse-agent/okta-resource-server-audience")["Parameter"][
                "Value"
            ]
            new_vars.update(
                {
                    "OKTA_ORG_URL": okta_org_url,
                    "OKTA_AUTH_SERVER_ID": okta_auth_server_id,
                    "OKTA_RESOURCE_SERVER_AUDIENCE": okta_audience,
                }
            )
            print(f"   Okta Org URL: {okta_org_url}")
            print(f"   Okta Auth Server ID: {okta_auth_server_id}")
            print(f"   Okta Resource Server Audience: {okta_audience}")
        print(f"   Region: {region}")
    except Exception as e:
        print(f"❌ Error loading {idp_provider} configuration: {e}")
        sys.exit(1)

    # Get interceptor Lambda ARN
    print("\n🔍 Finding interceptor Lambda function...")
    try:
        interceptor_arn = ssm.get_parameter(Name="/app/lakehouse-agent/interceptor-lambda-arn")["Parameter"]["Value"]
        # Extract function name from ARN
        function_name = interceptor_arn.split(":")[-1]
        print(f"   Lambda ARN: {interceptor_arn}")
        print(f"   Function Name: {function_name}")
    except ssm.exceptions.ParameterNotFound:
        print("   ⚠️  Interceptor Lambda ARN not found in SSM")
        print("   Please enter the Lambda function name manually:")
        function_name = input("   Lambda function name: ").strip()
        if not function_name:
            print("❌ No function name provided")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

    # Get current Lambda configuration
    print("\n🔍 Getting current Lambda configuration...")
    try:
        response = lambda_client.get_function_configuration(FunctionName=function_name)
        current_env = response.get("Environment", {}).get("Variables", {})

        print("   Current environment variables:")
        for key, value in current_env.items():
            print(f"      {key}: {value}")
    except Exception as e:
        print(f"❌ Error getting Lambda configuration: {e}")
        sys.exit(1)

    # Update environment variables (merge the IdP-specific keys built above).
    print("\n🔧 Updating Lambda environment variables...")
    new_env = current_env.copy()
    new_env.update(new_vars)

    try:
        lambda_client.update_function_configuration(FunctionName=function_name, Environment={"Variables": new_env})

        print("✅ Lambda environment variables updated!")
        print("   New configuration:")
        for key, value in new_vars.items():
            print(f"      {key}: {value}")

    except Exception as e:
        print(f"❌ Error updating Lambda: {e}")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("✅ Update Complete")
    print("=" * 70)
    print("\nYou can now test the Gateway:")
    print("   python test_gateway.py --username <username> --password <password>")
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
