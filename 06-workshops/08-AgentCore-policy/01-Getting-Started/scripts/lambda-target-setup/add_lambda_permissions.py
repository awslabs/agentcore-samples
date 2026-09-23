"""
Add resource-based permissions to Lambda functions to allow gateway invocation
This is the most common fix for gateway invocation issues
"""

import boto3
import json


def add_lambda_permissions():
    """Add permissions for gateway to invoke Lambda functions"""

    print("🔧 Adding Lambda Permissions for Gateway\n")
    print("=" * 70)

    # Load gateway configuration
    with open("gateway_config.json", "r") as f:
        gateway_config = json.load(f)

    region = gateway_config["region"]
    gateway_arn = gateway_config["gateway_arn"]
    gateway_account = gateway_arn.split(":")[4]

    print(f"Gateway ARN: {gateway_arn}\n")

    # Initialize Lambda client
    lambda_client = boto3.client("lambda", region_name=region)

    # Lambda functions to update
    functions = ["ApplicationTool", "RiskModelTool", "ApprovalTool"]

    for function_name in functions:
        print(f"🔧 {function_name}:")

        try:
            # Check if function exists
            lambda_client.get_function(FunctionName=function_name)

            # Try to add permission
            try:
                lambda_client.add_permission(
                    FunctionName=function_name,
                    StatementId="AllowAgentCoreGateway",
                    Action="lambda:InvokeFunction",
                    Principal="bedrock-agentcore.amazonaws.com",
                    SourceArn=gateway_arn,
                )
                print("   ✅ Permission added successfully")

            except lambda_client.exceptions.ResourceConflictException:
                print("   ℹ️  Permission already exists")

                # Try to update by removing and re-adding
                try:
                    lambda_client.remove_permission(FunctionName=function_name, StatementId="AllowAgentCoreGateway")

                    lambda_client.add_permission(
                        FunctionName=function_name,
                        StatementId="AllowAgentCoreGateway",
                        Action="lambda:InvokeFunction",
                        Principal="bedrock-agentcore.amazonaws.com",
                        SourceArn=gateway_arn,
                    )
                    print("   ✅ Permission updated successfully")

                except Exception as update_error:
                    print(f"   ⚠️  Could not update permission: {update_error}")

        except lambda_client.exceptions.ResourceNotFoundException:
            print(f"   ❌ Function not found in account {gateway_account}")
            print("   → Deploy Lambda first")

        except Exception as e:
            print(f"   ❌ Error: {e}")

        print()

    print("=" * 70)
    print("\n✅ Permission update complete!")
    print("\nNext steps:")
    print("1. Test gateway invocation")
    print("2. If still failing, check CloudWatch logs for the Lambda functions")
    print("3. Verify gateway IAM role has lambda:InvokeFunction permission")
    print("\n" + "=" * 70)


if __name__ == "__main__":
    add_lambda_permissions()
