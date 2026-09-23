#!/usr/bin/env python3
"""
Update Interceptor Lambda Environment Variables

This script updates the interceptor Lambda function's environment variables
to use the correct Cognito configuration from SSM Parameter Store.

Usage:
    python update_interceptor_env.py
"""

import boto3
import sys


def main():
    print("=" * 70)
    print("Update Interceptor Lambda Environment Variables")
    print("=" * 70)

    session = boto3.Session()
    region = session.region_name
    ssm = boto3.client("ssm", region_name=region)
    lambda_client = boto3.client("lambda", region_name=region)

    # Get correct Cognito configuration from SSM
    print("\n📋 Loading correct Cognito configuration from SSM...")
    try:
        user_pool_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-user-pool-id")["Parameter"]["Value"]
        app_client_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-app-client-id")["Parameter"]["Value"]

        print(f"   User Pool ID: {user_pool_id}")
        print(f"   App Client ID: {app_client_id}")
        print(f"   Region: {region}")
    except Exception as e:
        print(f"❌ Error loading Cognito configuration: {e}")
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

    # Update environment variables
    print("\n🔧 Updating Lambda environment variables...")
    new_env = current_env.copy()
    new_env["COGNITO_REGION"] = region
    new_env["COGNITO_USER_POOL_ID"] = user_pool_id
    new_env["COGNITO_APP_CLIENT_ID"] = app_client_id

    try:
        lambda_client.update_function_configuration(FunctionName=function_name, Environment={"Variables": new_env})

        print("✅ Lambda environment variables updated!")
        print("   New configuration:")
        print(f"      COGNITO_REGION: {region}")
        print(f"      COGNITO_USER_POOL_ID: {user_pool_id}")
        print(f"      COGNITO_APP_CLIENT_ID: {app_client_id}")

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
