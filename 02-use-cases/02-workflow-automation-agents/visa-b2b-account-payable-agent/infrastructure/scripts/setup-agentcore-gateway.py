#!/usr/bin/env python3
"""
Setup script for AgentCore Gateway with Visa B2B OpenAPI target.

This script:
1. Creates/uses S3 bucket for OpenAPI specs
2. Uploads modified Visa B2B OpenAPI spec to S3
3. Creates AgentCore Gateway with IAM authorizer
4. Creates OpenAPI target pointing to stub API
5. Verifies tool generation

Based on: amazon-bedrock-agentcore-samples/06-workshops/02-AgentCore-gateway/04-integration/01-runtime-gateway
"""

import boto3
import json
import sys
import time

# import uuid
from pathlib import Path

# Configuration
GATEWAY_NAME = "visa-b2b-payment-gateway"
OPENAPI_SPEC_PATH = "../visa-b2b-spec/gateway/visa-b2b-stub-openapi.json"
TARGET_NAME = "visa-b2b-stub-api-target"


def get_account_id():
    """Get AWS account ID"""
    sts = boto3.client("sts")
    return sts.get_caller_identity()["Account"]


def get_or_create_bucket(s3_client, region):
    """Create or use existing S3 bucket for OpenAPI specs"""
    from botocore.exceptions import ClientError

    account_id = get_account_id()
    bucket_name = f"agentcore-gateway-specs-{account_id}"

    try:
        # Check if bucket exists
        s3_client.head_bucket(Bucket=bucket_name)
        print(f"✓ Using existing S3 bucket: {bucket_name}")
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "404" or error_code == "NoSuchBucket":
            # Create bucket
            print(f"Creating S3 bucket: {bucket_name}")
            try:
                if region == "us-east-1":
                    s3_client.create_bucket(Bucket=bucket_name)
                else:
                    s3_client.create_bucket(
                        Bucket=bucket_name,
                        CreateBucketConfiguration={"LocationConstraint": region},
                    )
                print(f"✓ Created S3 bucket: {bucket_name}")
            except Exception as create_error:
                print(f"✗ Failed to create S3 bucket: {create_error}")
                raise
        else:
            print(f"✗ Error checking bucket: {e}")
            raise

    return bucket_name


def upload_openapi_spec(s3_client, bucket_name, spec_path):
    """Upload OpenAPI spec to S3"""
    object_key = "visa-b2b-stub-openapi.json"

    print("Uploading OpenAPI spec to S3...")
    with open(spec_path, "rb") as f:
        s3_client.put_object(Bucket=bucket_name, Key=object_key, Body=f)

    s3_uri = f"s3://{bucket_name}/{object_key}"
    print(f"✓ Uploaded OpenAPI spec to: {s3_uri}")
    return s3_uri


def create_gateway_role(iam_client, account_id, region):
    """Create IAM role for AgentCore Gateway"""
    role_name = "AgentCoreGatewayRole"

    # Trust policy for AgentCore Gateway
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*"},
                },
            }
        ],
    }

    # Permissions policy
    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::agentcore-gateway-specs-{account_id}/*",
                    f"arn:aws:s3:::agentcore-gateway-specs-{account_id}",
                ],
            },
            {
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": f"arn:aws:secretsmanager:{region}:{account_id}:secret:*",
            },
            {"Effect": "Allow", "Action": ["bedrock-agentcore:*"], "Resource": "*"},
        ],
    }

    try:
        # Check if role exists
        role = iam_client.get_role(RoleName=role_name)
        role_arn = role["Role"]["Arn"]
        print(f"✓ Using existing IAM role: {role_arn}")
    except iam_client.exceptions.NoSuchEntityException:
        # Create role
        print(f"Creating IAM role: {role_name}")
        role = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Role for AgentCore Gateway to access S3 and Secrets Manager",
        )
        role_arn = role["Role"]["Arn"]

        # Attach inline policy
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName="AgentCoreGatewayPermissions",
            PolicyDocument=json.dumps(permissions_policy),
        )
        print(f"✓ Created IAM role: {role_arn}")
        print("  Waiting 10 seconds for IAM role to propagate...")
        import time

        time.sleep(10)

    return role_arn


def create_gateway(agentcore_client, role_arn):
    """Create AgentCore Gateway with IAM authorizer"""
    print(f"Creating AgentCore Gateway: {GATEWAY_NAME}")

    try:
        # Try to create gateway
        response = agentcore_client.create_gateway(
            name=GATEWAY_NAME,
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="AWS_IAM",  # Key: IAM instead of OAuth
            description="Visa B2B Virtual Account Payment APIs - Stub API Gateway",
        )

        gateway_id = response["gatewayId"]
        gateway_url = response["gatewayUrl"]

        print("✓ Created Gateway:")
        print(f"  Gateway ID: {gateway_id}")
        print(f"  Gateway URL: {gateway_url}")

    except agentcore_client.exceptions.ConflictException:
        # Gateway already exists, get its details
        print("✓ Gateway already exists, retrieving details...")

        # List gateways to find ours
        list_response = agentcore_client.list_gateways()
        gateway_id = None
        for gw in list_response.get("items", []):
            if gw["name"] == GATEWAY_NAME:
                gateway_id = gw["gatewayId"]
                break

        if not gateway_id:
            raise Exception(f"Gateway {GATEWAY_NAME} exists but couldn't be found in list")

        # Get full gateway details including URL
        gateway_details = agentcore_client.get_gateway(gatewayIdentifier=gateway_id)
        gateway_url = gateway_details["gatewayUrl"]

        print(f"  Gateway ID: {gateway_id}")
        print(f"  Gateway URL: {gateway_url}")

    return gateway_id, gateway_url


def create_api_key_credential_provider(identity_client):
    """Create API key credential provider (dummy for stub API)"""
    provider_name = "visa-stub-api-key"

    try:
        # Try to create credential provider
        print(f"Creating API key credential provider: {provider_name}")
        response = identity_client.create_api_key_credential_provider(
            name=provider_name,
            apiKey="dummy-key-for-stub-api",  # Not used by stub, but required
        )
        provider_arn = response["credentialProviderArn"]
        print(f"✓ Created credential provider: {provider_arn}")
    except (
        identity_client.exceptions.ConflictException,
        identity_client.exceptions.ValidationException,
    ):
        # Provider already exists, get its ARN
        print("✓ Credential provider already exists")
        response = identity_client.get_api_key_credential_provider(name=provider_name)
        provider_arn = response["credentialProviderArn"]
        print(f"  Provider ARN: {provider_arn}")

    return provider_arn


def create_or_update_openapi_target(agentcore_client, gateway_id, s3_uri, credential_provider_arn):
    """Create or update OpenAPI target for Visa B2B stub API"""

    # Target configuration pointing to S3 OpenAPI spec
    target_config = {"mcp": {"openApiSchema": {"s3": {"uri": s3_uri}}}}

    # Credential configuration - API key (required for OpenAPI targets)
    # Note: Stub API doesn't actually validate this, but it's required by the gateway
    credential_config = [
        {
            "credentialProviderType": "API_KEY",
            "credentialProvider": {
                "apiKeyCredentialProvider": {
                    "credentialParameterName": "x-api-key",
                    "providerArn": credential_provider_arn,
                    "credentialLocation": "HEADER",
                }
            },
        }
    ]

    # Check if target already exists
    existing_target_id = None
    try:
        targets = agentcore_client.list_gateway_targets(gatewayIdentifier=gateway_id)
        for target in targets.get("items", []):
            if target["name"] == TARGET_NAME:
                existing_target_id = target["targetId"]
                break
    except Exception as e:
        print(f"  Warning: Could not list existing targets: {e}")

    if existing_target_id:
        # Update existing target
        print(f"Updating existing OpenAPI target: {TARGET_NAME}")
        try:
            response = agentcore_client.update_gateway_target(
                gatewayIdentifier=gateway_id,
                targetId=existing_target_id,
                name=TARGET_NAME,
                description="Visa B2B stub API endpoints (VirtualCardRequisition, ProcessPayments, GetPaymentDetails, GetSecurityCode)",
                targetConfiguration=target_config,
                credentialProviderConfigurations=credential_config,
            )
            print(f"✓ Updated OpenAPI target: {TARGET_NAME}")
            print(f"  Target ID: {existing_target_id}")
            return response
        except Exception as e:
            print(f"❌ Error updating target: {e}")
            raise
    else:
        # Create new target
        print(f"Creating OpenAPI target: {TARGET_NAME}")
        try:
            response = agentcore_client.create_gateway_target(
                gatewayIdentifier=gateway_id,
                name=TARGET_NAME,
                description="Visa B2B stub API endpoints (VirtualCardRequisition, ProcessPayments, GetPaymentDetails, GetSecurityCode)",
                targetConfiguration=target_config,
                credentialProviderConfigurations=credential_config,
            )
            print(f"✓ Created OpenAPI target: {TARGET_NAME}")
            return response
        except Exception as e:
            print(f"❌ Error creating target: {e}")
            raise


def list_gateway_targets(agentcore_client, gateway_id):
    """List all targets for the gateway"""
    print("\nListing gateway targets...")

    response = agentcore_client.list_gateway_targets(gatewayIdentifier=gateway_id)

    if "items" in response and len(response["items"]) > 0:
        print(f"✓ Found {len(response['items'])} target(s):")
        for target in response["items"]:
            print(f"  - {target['name']} (ID: {target['targetId']})")
    else:
        print("  No targets found")

    return response


def main():
    """Main setup function"""
    # Check for --force-recreate flag
    force_recreate = "--force-recreate" in sys.argv

    print("=" * 70)
    print("AgentCore Gateway Setup for Visa B2B Payment Integration")
    if force_recreate:
        print("(Force Recreate Mode - will delete and recreate target)")
    print("=" * 70)
    print()

    # Initialize AWS clients
    session = boto3.Session()
    region = session.region_name or "us-east-1"
    account_id = get_account_id()

    print(f"AWS Account: {account_id}")
    print(f"AWS Region: {region}")
    print()

    s3_client = session.client("s3")
    iam_client = session.client("iam")
    agentcore_client = session.client("bedrock-agentcore-control", region_name=region)
    # Identity operations use the same client
    identity_client = agentcore_client

    try:
        # Step 1: Create/use S3 bucket
        print("Step 1: S3 Bucket Setup")
        print("-" * 70)
        bucket_name = get_or_create_bucket(s3_client, region)
        print()

        # Step 2: Upload OpenAPI spec
        print("Step 2: Upload OpenAPI Spec")
        print("-" * 70)
        s3_uri = upload_openapi_spec(s3_client, bucket_name, OPENAPI_SPEC_PATH)
        print("  Waiting 5 seconds for S3 eventual consistency...")
        time.sleep(5)
        print()

        # Step 3: Create IAM role
        print("Step 3: IAM Role Setup")
        print("-" * 70)
        role_arn = create_gateway_role(iam_client, account_id, region)
        print()

        # Step 4: Create Gateway
        print("Step 4: Create AgentCore Gateway")
        print("-" * 70)
        gateway_id, gateway_url = create_gateway(agentcore_client, role_arn)
        print("  Waiting 5 seconds for gateway initialization...")
        time.sleep(5)
        print()

        # Step 5: Create API key credential provider
        print("Step 5: Create API Key Credential Provider")
        print("-" * 70)
        credential_provider_arn = create_api_key_credential_provider(identity_client)
        print()

        # Step 6: Create or update OpenAPI target
        print("Step 6: Create/Update OpenAPI Target")
        print("-" * 70)
        create_or_update_openapi_target(agentcore_client, gateway_id, s3_uri, credential_provider_arn)
        print()

        # Step 7: List targets to verify
        print("Step 7: Verify Target Creation")
        print("-" * 70)
        list_gateway_targets(agentcore_client, gateway_id)
        print()

        # Success summary
        print("=" * 70)
        print("✓ AgentCore Gateway Setup Complete!")
        print("=" * 70)
        print()
        print("Gateway Details:")
        print(f"  Gateway ID:  {gateway_id}")
        print(f"  Gateway URL: {gateway_url}")
        print(f"  S3 Spec URI: {s3_uri}")
        print()
        print("Next Steps:")
        print("  1. Wait 1-2 minutes for gateway to fully initialize")
        print("  2. Test tool generation with: python infrastructure/scripts/test-gateway-tools.py")
        print("  3. Implement Payment Agent to use these MCP tools")
        print()
        print("Environment Variables for Agent:")
        print(f"  export GATEWAY_URL='{gateway_url}'")
        print(f"  export GATEWAY_ID='{gateway_id}'")
        print(f"  export GATEWAY_REGION='{region}'")
        print()

        # Save configuration
        config = {
            "gateway_id": gateway_id,
            "gateway_url": gateway_url,
            "gateway_region": region,
            "s3_uri": s3_uri,
            "role_arn": role_arn,
        }

        config_path = Path(".agentcore-gateway-config.json")
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        print(f"Configuration saved to: {config_path}")
        print()

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
