"""Create a gateway target with VPC egress configuration.

Shared script used by all VPC egress labs. Creates an MCP server or HTTP
target with the specified VPC egress settings (managed or self-managed).

Usage:
    # Managed VPC resource (AgentCore manages Lattice resources)
    python scripts/create_target.py \
      --name my-mcp-target \
      --endpoint https://my-internal-service.private.example.com/mcp \
      --vpc-id vpc-0123456789abcdef0 \
      --subnet-ids subnet-aaa,subnet-bbb \
      --security-group-ids sg-123

    # Self-managed Lattice (you provide the Resource Gateway ARN)
    python scripts/create_target.py \
      --name my-mcp-target \
      --endpoint https://my-internal-service.private.example.com/mcp \
      --resource-gateway-arn arn:aws:vpc-lattice:us-west-2:123456789012:resourcegateway/rgw-xxx

    # With credential provider (OAuth outbound auth)
    python scripts/create_target.py \
      --name my-mcp-target \
      --endpoint https://my-internal-service.private.example.com/mcp \
      --vpc-id vpc-xxx \
      --subnet-ids subnet-aaa,subnet-bbb \
      --security-group-ids sg-123 \
      --credential-provider-arn arn:aws:bedrock-agentcore:...

    # Lambda target with VPC egress
    python scripts/create_target.py \
      --name my-lambda-target \
      --lambda-arn arn:aws:lambda:us-west-2:123456789012:function:my-func \
      --tool-schema-file tool-schemas/my-tools.json \
      --vpc-id vpc-xxx \
      --subnet-ids subnet-aaa,subnet-bbb \
      --security-group-ids sg-123
"""

import argparse
import json
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it first.")
        sys.exit(1)
    return val


def main():
    parser = argparse.ArgumentParser(
        description="Create a gateway target with VPC egress"
    )
    parser.add_argument("--name", required=True, help="Target name")
    parser.add_argument("--endpoint", default=None, help="MCP server endpoint URL")
    parser.add_argument(
        "--lambda-arn", default=None, help="Lambda function ARN (for Lambda targets)"
    )
    parser.add_argument(
        "--tool-schema-file",
        default=None,
        help="Path to tool schema JSON (for Lambda targets)",
    )

    # VPC egress - managed
    parser.add_argument(
        "--vpc-id", default=None, help="VPC ID for managed VPC resource"
    )
    parser.add_argument("--subnet-ids", default=None, help="Comma-separated subnet IDs")
    parser.add_argument(
        "--security-group-ids", default=None, help="Comma-separated security group IDs"
    )

    # VPC egress - self-managed
    parser.add_argument(
        "--resource-gateway-arn",
        default=None,
        help="Self-managed VPC Lattice Resource Gateway ARN",
    )

    # Auth
    parser.add_argument(
        "--credential-provider-arn", default=None, help="OAuth credential provider ARN"
    )
    parser.add_argument("--scopes", default=None, help="Comma-separated OAuth scopes")

    # Listing mode
    parser.add_argument(
        "--listing-mode", default=None, help="DYNAMIC or DEFAULT (default: DEFAULT)"
    )

    args = parser.parse_args()

    gateway_id = get_required_env("GATEWAY_ID")
    region = boto3.Session().region_name
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    # Build target configuration
    if args.lambda_arn:
        tool_schema = []
        if args.tool_schema_file:
            with open(args.tool_schema_file) as f:
                tool_schema = json.load(f)
        target_config = {
            "mcp": {
                "lambda": {
                    "lambdaArn": args.lambda_arn,
                    "toolSchema": {"inlinePayload": tool_schema},
                }
            }
        }
    else:
        if not args.endpoint:
            print("ERROR: --endpoint is required for MCP server targets")
            sys.exit(1)
        mcp_server_config = {"endpoint": args.endpoint}
        if args.listing_mode:
            mcp_server_config["listingMode"] = args.listing_mode
        target_config = {"mcp": {"mcpServer": mcp_server_config}}

    # Build VPC egress configuration
    vpc_egress_config = None
    if args.vpc_id and args.subnet_ids:
        managed = {
            "vpcIdentifier": args.vpc_id,
            "subnetIds": [s.strip() for s in args.subnet_ids.split(",")],
            "endpointIpAddressType": "IPV4",
        }
        if args.security_group_ids:
            managed["securityGroupIds"] = [
                s.strip() for s in args.security_group_ids.split(",")
            ]
        vpc_egress_config = {"managedVpcResource": managed}
    elif args.resource_gateway_arn:
        vpc_egress_config = {
            "selfManagedResources": {"resourceGatewayArn": args.resource_gateway_arn}
        }

    # Build credential provider config
    cred_config = []
    if args.credential_provider_arn:
        scopes = [s.strip() for s in args.scopes.split(",")] if args.scopes else []
        cred_config = [
            {
                "credentialProviderType": "OAUTH",
                "credentialProvider": {
                    "oauthCredentialProvider": {
                        "providerArn": args.credential_provider_arn,
                        "scopes": scopes,
                    }
                },
            }
        ]
    elif args.lambda_arn:
        cred_config = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]

    # Create target
    kwargs = {
        "gatewayIdentifier": gateway_id,
        "name": args.name,
        "targetConfiguration": target_config,
    }
    if cred_config:
        kwargs["credentialProviderConfigurations"] = cred_config
    if vpc_egress_config:
        kwargs["privateEndpoint"] = vpc_egress_config

    print(f"--- Creating target '{args.name}' ---")
    print(f"  Gateway: {gateway_id}")
    if args.endpoint:
        print(f"  Endpoint: {args.endpoint}")
    if args.lambda_arn:
        print(f"  Lambda: {args.lambda_arn}")
    if vpc_egress_config:
        print(f"  VPC Egress: {'managed' if args.vpc_id else 'self-managed'}")

    try:
        resp = control.create_gateway_target(**kwargs)
        target_id = resp["targetId"]
        print(f"  Target ID: {target_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            print(f"  Target already exists: {args.name}")
            targets = control.list_gateway_targets(
                gatewayIdentifier=gateway_id, maxResults=50
            )
            target_id = next(
                t["targetId"]
                for t in targets.get("items", [])
                if t["name"] == args.name
            )
            print(f"  Using existing target: {target_id}")
        else:
            raise

    # Wait for READY
    print("  Waiting for target to become READY...")
    for _ in range(30):
        time.sleep(10)
        tgt = control.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )
        status = tgt["status"]
        print(f"    Status: {status}")
        if status in ["READY", "FAILED", "UPDATE_UNSUCCESSFUL"]:
            break

    if status == "READY":
        print(f"\n  Target '{args.name}' is READY.")
    else:
        print(f"\n  WARNING: Target ended in status: {status}")

    print(f"  Target ID: {target_id}")


if __name__ == "__main__":
    main()
