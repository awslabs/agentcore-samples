import subprocess
import sys

# Upgrade boto3 to latest version for bedrock-agentcore support
subprocess.check_call(
    [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "boto3",
        "botocore",
        "-t",
        "/tmp/python",
    ]
)
sys.path.insert(0, "/tmp/python")

import boto3
import time


def lambda_handler(event, context):
    """Custom Resource handler for Bedrock AgentCore Gateway."""
    request_type = event["RequestType"]
    properties = event["ResourceProperties"]

    client = boto3.client("bedrock-agentcore-control")

    try:
        if request_type == "Create":
            return on_create(client, properties)
        elif request_type == "Update":
            return on_update(client, properties, event.get("PhysicalResourceId"))
        elif request_type == "Delete":
            return on_delete(client, event.get("PhysicalResourceId"))
    except Exception as e:
        print(f"Error: {e}")
        raise e


def on_create(client, properties):
    """Create the gateway and gateway target."""
    # Wait for IAM role to propagate
    print("Waiting for IAM role to propagate...")
    time.sleep(10)

    # Create the gateway
    gateway = client.create_gateway(
        name=properties["GatewayName"],
        roleArn=properties["RoleArn"],
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": properties["DiscoveryUrl"],
                "allowedClients": properties["AllowedClients"],
            }
        },
        interceptorConfigurations=[
            {
                "interceptor": {"lambda": {"arn": properties["InterceptorLambdaArn"]}},
                "interceptionPoints": ["RESPONSE"],
                "inputConfiguration": {"passRequestHeaders": True},
            }
        ],
    )

    gateway_id = gateway["gatewayId"]
    print(f"Created gateway: {gateway_id}")

    # Wait for gateway to be ready
    time.sleep(20)

    # Create the gateway target
    gateway_target = client.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=properties["TargetName"],
        description=properties["TargetDescription"],
        targetConfiguration={
            "mcp": {
                "lambda": {
                    "lambdaArn": properties["CrudLambdaArn"],
                    "toolSchema": {"inlinePayload": properties["ToolSchema"]},
                }
            }
        },
        credentialProviderConfigurations=[
            {"credentialProviderType": "GATEWAY_IAM_ROLE"}
        ],
    )

    target_id = gateway_target["targetId"]
    print(f"Created gateway target: {target_id}")

    return {
        "PhysicalResourceId": gateway_id,
        "Data": {
            "GatewayId": gateway_id,
            "GatewayArn": gateway["gatewayArn"],
            "GatewayUrl": gateway["gatewayUrl"],
            "TargetId": target_id,
        },
    }


def on_update(client, properties, gateway_id):
    """Update the gateway (delete and recreate for simplicity)."""
    on_delete(client, gateway_id)
    return on_create(client, properties)


def on_delete(client, gateway_id):
    """Delete the gateway and its targets."""
    if not gateway_id:
        return {"PhysicalResourceId": gateway_id}

    try:
        # List and delete all targets first
        targets = client.list_gateway_targets(gatewayIdentifier=gateway_id)
        for target in targets.get("items", []):
            client.delete_gateway_target(
                gatewayIdentifier=gateway_id,
                targetId=target["targetId"],
            )
            print(f"Deleted target: {target['targetId']}")

        time.sleep(5)

        # Delete the gateway
        client.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"Deleted gateway: {gateway_id}")

    except client.exceptions.ResourceNotFoundException:
        print(f"Gateway {gateway_id} not found, skipping deletion")

    return {"PhysicalResourceId": gateway_id}
