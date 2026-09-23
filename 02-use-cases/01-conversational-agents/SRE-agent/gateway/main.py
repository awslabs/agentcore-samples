#!/usr/bin/env python3
"""
AgentCore Gateway Management Tool

This tool provides functionality to create and manage AWS AgentCore Gateways
with MCP protocol support and JWT authorization. It supports creating
gateways and adding OpenAPI targets from S3 or inline schemas.
"""

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# Configuration constants
GATEWAY_DELETION_PROPAGATION_DELAY = 3


# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)


def _extract_account_id_from_arn(arn: str) -> str:
    """
    Extract AWS account ID from an ARN.

    Args:
        arn: AWS ARN string

    Returns:
        Account ID extracted from ARN
    """
    try:
        # ARN format: arn:aws:service:region:account-id:resource
        parts = arn.split(":")
        if len(parts) >= 5:
            return parts[4]
        else:
            logging.error(f"Invalid ARN format: {arn}")
            return ""
    except Exception as e:
        logging.error(f"Failed to extract account ID from ARN: {e}")
        return ""


def _create_agentcore_client(region: str, endpoint_url: str) -> Any:
    """
    Create and return an AgentCore client for interacting with the AWS service with retry configuration.

    Args:
        region: AWS region name
        endpoint_url: AgentCore endpoint URL

    Returns:
        Configured boto3 client for bedrock-agentcore-control
    """
    # Validate that the region matches the endpoint URL
    import re

    endpoint_region_match = re.search(r"\.([a-z0-9-]+)\.amazonaws\.com", endpoint_url)
    if endpoint_region_match:
        endpoint_region = endpoint_region_match.group(1)
        if endpoint_region != region:
            error_msg = (
                f"Region mismatch: The --region parameter '{region}' does not match "
                f"the region in the endpoint URL '{endpoint_region}'. "
                f"Please ensure both use the same region (e.g., --region {endpoint_region})"
            )
            logging.error(error_msg)
            raise ValueError(error_msg)

    # Custom retry configuration with increased attempts and timeout
    retry_config = Config(
        retries={"max_attempts": 20, "mode": "adaptive"},
        connect_timeout=60,
        read_timeout=60,
    )

    try:
        client = boto3.client(
            "bedrock-agentcore-control",
            region_name=region,
            endpoint_url=endpoint_url,
            config=retry_config,
        )
        logging.info(f"Created AgentCore client for region {region}")
        return client
    except Exception as e:
        logging.error(f"Failed to create AgentCore client: {e}")
        raise


def _print_gateway_response(response: Dict[str, Any]) -> None:
    """
    Print formatted gateway creation response details.

    Args:
        response: Gateway creation response from AWS
    """
    print("=" * 80)
    print("GATEWAY CREATION RESPONSE")
    print("=" * 80)

    # Status and Basic Info
    print(f"\n📊 Status: {response.get('status', 'N/A')}")
    print(f"✅ HTTP Status: {response['ResponseMetadata']['HTTPStatusCode']}")

    # Gateway Details
    print(f"\n🔗 Gateway URL: {response.get('gatewayUrl', 'N/A')}")
    print(f"📌 Gateway ID: {response.get('gatewayId', 'N/A')}")
    print(f"📝 Gateway Name: {response.get('name', 'N/A')}")
    print(f"💬 Description: {response.get('description', 'N/A')}")

    # ARN Information
    print(f"\n🏷️  Gateway ARN: {response.get('gatewayArn', 'N/A')}")
    print(f"👤 Role ARN: {response.get('roleArn', 'N/A')}")

    # Protocol Configuration
    protocol_config = response.get("protocolConfiguration", {}).get("mcp", {})
    print(f"\n🔧 Protocol Type: {response.get('protocolType', 'N/A')}")
    print(
        f"📋 Supported Versions: {', '.join(protocol_config.get('supportedVersions', []))}"
    )
    print(f"🔍 Search Type: {protocol_config.get('searchType', 'N/A')}")

    # Authorizer Configuration
    auth_config = response.get("authorizerConfiguration", {}).get(
        "customJWTAuthorizer", {}
    )
    print(f"\n🔐 Authorizer Type: {response.get('authorizerType', 'N/A')}")
    print(f"🌐 Discovery URL: {auth_config.get('discoveryUrl', 'N/A')}")
    print(f"👥 Allowed Audience: {', '.join(auth_config.get('allowedAudience', []))}")

    # Timestamps
    print(f"\n📅 Created At: {response.get('createdAt', 'N/A')}")
    print(f"🔄 Updated At: {response.get('updatedAt', 'N/A')}")

    # Request Metadata
    response_metadata = response["ResponseMetadata"]
    request_id = response_metadata["RequestId"]
    timestamp = response_metadata["HTTPHeaders"]["date"]

    print(f"\n🆔 Request ID: {request_id}")
    print(f"🕐 Timestamp: {timestamp}")
    print("=" * 80)


def _save_gateway_url(gateway_url: str, output_file: str = ".gateway_uri") -> None:
    """
    Save the gateway URL to a file.

    Args:
        gateway_url: Gateway URL to save
        output_file: Output file path
    """
    # Remove trailing slash if present
    gateway_url = gateway_url.rstrip("/")

    # Remove '/mcp' from the end if present
    if gateway_url.endswith("/mcp"):
        gateway_url = gateway_url[:-4]

    Path(output_file).write_text(gateway_url)
    logging.info(f"Saved gateway URL to {output_file}")


def _check_gateway_exists(client: Any, gateway_name: str) -> str:
    """
    Check if a gateway with the given name already exists.

    Args:
        client: AgentCore client
        gateway_name: Name of the gateway to check

    Returns:
        Gateway ID if exists, empty string if not found
    """
    try:
        response = client.list_gateways()
        gateways = response.get("items", [])

        for gateway in gateways:
            if gateway.get("name") == gateway_name:
                gateway_id = gateway.get("gatewayId", "")
                logging.info(
                    f"Found existing gateway: {gateway_name} (ID: {gateway_id})"
                )
                return gateway_id

        logging.info(f"No existing gateway found with name: {gateway_name}")
        return ""
    except ClientError as e:
        logging.error(f"Failed to list gateways: {e}")
        raise


def _delete_gateway_targets(client: Any, gateway_id: str) -> None:
    """
    Delete all targets associated with a gateway.

    Args:
        client: AgentCore client
        gateway_id: Gateway ID whose targets to delete
    """
    try:
        logging.info(f"Listing targets for gateway: {gateway_id}")
        targets_response = client.list_gateway_targets(gatewayIdentifier=gateway_id)
        targets = targets_response.get("items", [])

        if not targets:
            logging.info(f"No targets found for gateway: {gateway_id}")
            return

        logging.info(f"Found {len(targets)} targets to delete")

        for target in targets:
            target_id = target.get("targetId", "")
            target_name = target.get("name", "Unknown")

            if target_id:
                logging.info(f"Deleting target: {target_name} (ID: {target_id})")
                delete_response = client.delete_gateway_target(
                    targetId=target_id, gatewayIdentifier=gateway_id
                )
                logging.info(f"Target deleted successfully: {target_name}")

                if logging.getLogger().isEnabledFor(logging.DEBUG):
                    logging.debug(f"Target delete response: {delete_response}")
            else:
                logging.warning(f"Target has no ID, skipping: {target_name}")

        logging.info(f"All targets deleted for gateway: {gateway_id}")

    except ClientError as e:
        logging.error(f"Failed to delete targets for gateway {gateway_id}: {e}")
        raise


def _delete_gateway(client: Any, gateway_id: str) -> None:
    """
    Delete a gateway by ID, including all its targets.

    Args:
        client: AgentCore client
        gateway_id: Gateway ID to delete
    """
    try:
        # First delete all targets
        _delete_gateway_targets(client, gateway_id)

        # Then delete the gateway
        logging.info(f"Deleting gateway: {gateway_id}")
        delete_response = client.delete_gateway(gatewayIdentifier=gateway_id)
        logging.info(f"Gateway deleted successfully: {gateway_id}")

        if logging.getLogger().isEnabledFor(logging.DEBUG):
            logging.debug(f"Gateway delete response: {delete_response}")

        # Wait for deletion to propagate
        logging.info(
            f"Waiting {GATEWAY_DELETION_PROPAGATION_DELAY} seconds for deletion to propagate..."
        )
        time.sleep(GATEWAY_DELETION_PROPAGATION_DELAY)
    except ClientError as e:
        logging.error(f"Failed to delete gateway {gateway_id}: {e}")
        raise


def create_gateway(
    client: Any,
    gateway_name: str,
    role_arn: str,
    discovery_url: str,
    allowed_audience: str = None,
    allowed_clients: list = None,
    description: str = "AgentCore Gateway created via SDK",
    search_type: str = "SEMANTIC",
    protocol_version: str = "2025-03-26",
) -> Dict[str, Any]:
    """
    Create a new AgentCore Gateway with JWT authorization.

    Args:
        client: AgentCore client
        gateway_name: Name for the gateway
        role_arn: IAM role ARN with necessary permissions
        discovery_url: JWT discovery URL
        allowed_audience: Allowed JWT audience (for Auth0/Okta)
        allowed_clients: Allowed JWT client IDs (for Cognito)
        description: Gateway description
        search_type: MCP search type (default: SEMANTIC)
        protocol_version: MCP protocol version (default: 2025-03-26)

    Returns:
        Gateway creation response
    """
    # Build auth config based on whether it's Cognito (clients) or Auth0/Okta (audience)
    auth_config = {"customJWTAuthorizer": {"discoveryUrl": discovery_url}}

    if allowed_clients:
        # For Cognito - use allowedClients
        auth_config["customJWTAuthorizer"]["allowedClients"] = (
            allowed_clients if isinstance(allowed_clients, list) else [allowed_clients]
        )
    elif allowed_audience:
        # For Auth0/Okta - use allowedAudience
        auth_config["customJWTAuthorizer"]["allowedAudience"] = [allowed_audience]
    else:
        raise ValueError("Either allowed_audience or allowed_clients must be specified")

    protocol_configuration = {
        "mcp": {"searchType": search_type, "supportedVersions": [protocol_version]}
    }

    try:
        response = client.create_gateway(
            name=gateway_name,
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration=auth_config,
            protocolConfiguration=protocol_configuration,
            description=description,
            exceptionLevel="DEBUG",
        )
        logging.info(f"Created gateway: {response.get('gatewayId')}")
        return response
    except ClientError as e:
        logging.error(f"Failed to create gateway: {e}")
        raise


def create_s3_target(
    client: Any,
    gateway_id: str,
    s3_uri: str,
    provider_arn: str,
    target_name_prefix: str = "open",
    description: str = "S3 target for OpenAPI schema",
) -> Dict[str, Any]:
    """
    Create a gateway target from an S3 OpenAPI schema.

    Args:
        client: AgentCore client
        gateway_id: Gateway identifier
        s3_uri: S3 URI of the OpenAPI schema
        provider_arn: OAuth credential provider ARN
        target_name_prefix: Prefix for target name
        description: Description for the target

    Returns:
        Target creation response
    """
    s3_target_config = {"mcp": {"openApiSchema": {"s3": {"uri": s3_uri}}}}

    # OAuth credential provider configuration
    # credential_config = {
    #     "credentialProviderType": "OAUTH",
    #     "credentialProvider": {
    #         "oauthCredentialProvider": {
    #             "providerArn": provider_arn,
    #             "scopes": []
    #         }
    #     }
    # }

    # API key credential provider configuration
    credential_config = {
        "credentialProviderType": "API_KEY",
        "credentialProvider": {
            "apiKeyCredentialProvider": {
                # "credentialPrefix": "",
                "providerArn": provider_arn,
                "credentialLocation": "HEADER",  # QUERY_PARAMETER
                "credentialParameterName": "X-API-KEY",
            }
        },
    }
    try:
        response = client.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=target_name_prefix,
            description=description,
            targetConfiguration=s3_target_config,
            credentialProviderConfigurations=[credential_config],
        )
        logging.info(f"Created S3 target: {response.get('targetId')}")
        return response
    except ClientError as e:
        logging.error(f"Failed to create S3 target: {e}")
        raise


def create_inline_target(
    client: Any,
    gateway_id: str,
    openapi_schema: str,
    provider_arn: str,
    target_name_prefix: str = "inline",
    description: str = "Inline target for OpenAPI schema",
) -> Dict[str, Any]:
    """
    Create a gateway target from an inline OpenAPI schema.

    Args:
        client: AgentCore client
        gateway_id: Gateway identifier
        openapi_schema: Inline OpenAPI schema as string
        provider_arn: OAuth credential provider ARN
        target_name_prefix: Prefix for target name
        description: Description for the target

    Returns:
        Target creation response
    """
    openapi_target_config = {
        "mcp": {"openApiSchema": {"inlinePayload": openapi_schema}}
    }

    credential_config = {
        "credentialProviderType": "OAUTH",
        "credentialProvider": {
            "oauthCredentialProvider": {"providerArn": provider_arn, "scopes": []}
        },
    }

    try:
        response = client.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=target_name_prefix,
            description=description,
            targetConfiguration=openapi_target_config,
            credentialProviderConfigurations=[credential_config],
        )
        logging.info(f"Created inline target: {response.get('targetId')}")
        return response
    except ClientError as e:
        logging.error(f"Failed to create inline target: {e}")
        raise


def verify_gateway(client: Any, gateway_id: str) -> Dict[str, Any]:
    """
    Verify gateway creation by fetching its details.

    Args:
        client: AgentCore client
        gateway_id: Gateway identifier

    Returns:
        Gateway details
    """
    try:
        response = client.get_gateway(gatewayIdentifier=gateway_id)
        logging.info(
            f"Verified gateway: {gateway_id}, Status: {response.get('status')}"
        )
        return response
    except ClientError as e:
        logging.error(f"Failed to verify gateway: {e}")
        raise


def list_gateway_targets(client: Any, gateway_id: str) -> Dict[str, Any]:
    """
    List all targets for a gateway.

    Args:
        client: AgentCore client
        gateway_id: Gateway identifier

    Returns:
        List of gateway targets
    """
    try:
        response = client.list_gateway_targets(gatewayIdentifier=gateway_id)
        logging.info(
            f"Found {len(response.get('items', []))} targets for gateway {gateway_id}"
        )
        return response
    except ClientError as e:
        logging.error(f"Failed to list gateway targets: {e}")
        raise


def main():
    """Main function to orchestrate gateway creation and management."""
    parser = argparse.ArgumentParser(
        description="Create and manage AWS AgentCore Gateways with MCP protocol support"
    )

    # Required arguments
    parser.add_argument("gateway_name", help="Name for the AgentCore Gateway")

    # AWS configuration
    parser.add_argument(
        "--region", default="us-east-1", help="AWS region (default: us-east-1)"
    )
    parser.add_argument(
        "--endpoint-url",
        default="https://bedrock-agentcore-control.us-east-1.amazonaws.com",
        help="AgentCore endpoint URL",
    )
    parser.add_argument(
        "--role-arn", required=True, help="IAM Role ARN with gateway permissions"
    )

    # Authorization configuration
    parser.add_argument(
        "--discovery-url", required=True, help="JWT discovery URL for authorization"
    )
    parser.add_argument(
        "--allowed-audience",
        default="MCPGateway",
        help="Allowed JWT audience (default: MCPGateway)",
    )
    parser.add_argument(
        "--allowed-clients", help="Allowed JWT client IDs (for Cognito)"
    )

    # Gateway configuration
    parser.add_argument(
        "--description-for-gateway",
        default="AgentCore Gateway created via SDK",
        help="Gateway description",
    )
    parser.add_argument(
        "--description-for-target",
        action="append",
        help="Target description (can be specified multiple times)",
    )
    parser.add_argument(
        "--search-type", default="SEMANTIC", help="MCP search type (default: SEMANTIC)"
    )
    parser.add_argument(
        "--protocol-version",
        default="2025-03-26",
        help="MCP protocol version (default: 2025-03-26)",
    )

    # Target configuration
    parser.add_argument(
        "--create-s3-target", action="store_true", help="Create an S3 OpenAPI target"
    )
    parser.add_argument(
        "--s3-uri",
        action="append",
        help="S3 URI for OpenAPI schema (can be specified multiple times)",
    )
    parser.add_argument(
        "--create-inline-target",
        action="store_true",
        help="Create an inline OpenAPI target",
    )
    parser.add_argument(
        "--openapi-schema-file", help="File containing OpenAPI schema for inline target"
    )
    parser.add_argument(
        "--provider-arn", help="OAuth credential provider ARN for targets"
    )

    # Output options
    parser.add_argument(
        "--save-gateway-url",
        action="store_true",
        help="Save gateway URL to .gateway_uri file",
    )
    parser.add_argument(
        "--delete-gateway-if-exists",
        action="store_true",
        help="Delete gateway if it already exists before creating new one",
    )
    parser.add_argument(
        "--output-json", action="store_true", help="Output responses in JSON format"
    )
    parser.add_argument(
        "--enable-observability",
        action="store_true",
        help="Enable CloudWatch logs and X-Ray tracing for the gateway",
    )

    args = parser.parse_args()

    # Create AgentCore client
    client = _create_agentcore_client(args.region, args.endpoint_url)

    # Check if gateway already exists and handle deletion if requested
    existing_gateway_id = _check_gateway_exists(client, args.gateway_name)
    if existing_gateway_id:
        if args.delete_gateway_if_exists:
            logging.info("Deleting existing gateway before creating new one")
            _delete_gateway(client, existing_gateway_id)
        else:
            logging.warning(
                f"Gateway '{args.gateway_name}' already exists (ID: {existing_gateway_id})"
            )
            logging.warning(
                "Use --delete-gateway-if-exists to delete it before creating a new one"
            )
            print(f"❌ Gateway '{args.gateway_name}' already exists")
            print(f"   Gateway ID: {existing_gateway_id}")
            print("   Use --delete-gateway-if-exists flag to delete and recreate")
            exit(1)

    # Create gateway
    logging.info(f"Creating gateway: {args.gateway_name}")
    create_response = create_gateway(
        client=client,
        gateway_name=args.gateway_name,
        role_arn=args.role_arn,
        discovery_url=args.discovery_url,
        allowed_audience=args.allowed_audience if not args.allowed_clients else None,
        allowed_clients=(
            args.allowed_clients.split(",") if args.allowed_clients else None
        ),
        description=args.description_for_gateway,
        search_type=args.search_type,
        protocol_version=args.protocol_version,
    )

    if args.output_json:
        print(json.dumps(create_response, indent=2, default=str))
    else:
        _print_gateway_response(create_response)

    gateway_id = create_response["gatewayId"]
    gateway_url = create_response.get("gatewayUrl", "")
    create_response.get("gatewayArn", "")

    # Check if observability was requested
    if args.enable_observability:
        logging.error("Observability feature is not yet supported")
        print(
            "\n❌ Error: The --enable-observability feature is currently not supported but will be available soon."
        )
        print("   Please run the command without the --enable-observability flag.")
        exit(1)

    # Save gateway URL if requested
    if args.save_gateway_url and gateway_url:
        _save_gateway_url(gateway_url)

    # Verify gateway creation
    verify_response = verify_gateway(client, gateway_id)
    if args.output_json:
        print("\nGateway Verification:")
        print(json.dumps(verify_response, indent=2, default=str))

    # Create S3 targets if requested
    if args.create_s3_target:
        if not args.provider_arn:
            logging.error("Provider ARN required for creating targets")
            parser.error("--provider-arn is required when creating targets")

        if not args.s3_uri:
            logging.error("At least one S3 URI required when creating S3 targets")
            parser.error("--s3-uri is required when creating S3 targets")

        # Handle multiple S3 URIs and descriptions
        s3_uris = args.s3_uri
        descriptions = args.description_for_target or []

        # Ensure we have descriptions for all URIs (use default if not enough provided)
        while len(descriptions) < len(s3_uris):
            descriptions.append("S3 target for OpenAPI schema")

        s3_responses = []
        for i, s3_uri in enumerate(s3_uris):
            # Extract a meaningful name from the S3 URI for the target
            target_name = (
                s3_uri.split("/")[-1].replace(".yaml", "").replace(".json", "")
            )
            if not target_name or target_name == s3_uri:
                target_name = f"target-{i + 1}"

            # Replace underscores with hyphens to meet AWS naming requirements
            # AWS requires: ([0-9a-zA-Z][-]?){1,100}
            target_name = target_name.replace("_", "-")

            logging.info(
                f"Creating S3 OpenAPI target {i + 1}/{len(s3_uris)}: {target_name}"
            )
            s3_response = create_s3_target(
                client=client,
                gateway_id=gateway_id,
                s3_uri=s3_uri,
                provider_arn=args.provider_arn,
                target_name_prefix=target_name,
                description=descriptions[i],
            )
            s3_responses.append(s3_response)

            if args.output_json:
                print(f"\nS3 Target {i + 1} Creation:")
                print(json.dumps(s3_response, indent=2, default=str))

        if not args.output_json:
            print(f"\n✅ Successfully created {len(s3_responses)} S3 targets")

    # Create inline target if requested
    if args.create_inline_target:
        if not args.provider_arn:
            logging.error("Provider ARN required for creating targets")
            parser.error("--provider-arn is required when creating targets")

        if not args.openapi_schema_file:
            logging.error("OpenAPI schema file required for inline target")
            parser.error("--openapi-schema-file is required for inline targets")

        # Read OpenAPI schema from file
        schema_content = Path(args.openapi_schema_file).read_text()

        logging.info("Creating inline OpenAPI target")
        inline_response = create_inline_target(
            client=client,
            gateway_id=gateway_id,
            openapi_schema=schema_content,
            provider_arn=args.provider_arn,
            description=args.description_for_target,
        )

        if args.output_json:
            print("\nInline Target Creation:")
            print(json.dumps(inline_response, indent=2, default=str))

    # List all targets
    if args.create_s3_target or args.create_inline_target:
        targets_response = list_gateway_targets(client, gateway_id)
        if args.output_json:
            print("\nGateway Targets:")
            print(json.dumps(targets_response, indent=2, default=str))
        else:
            targets = targets_response.get("items", [])
            print(f"\n📋 Gateway has {len(targets)} target(s):")
            for target in targets:
                print(
                    f"   • {target.get('name', 'Unknown')} (ID: {target.get('targetId', 'N/A')})"
                )
                print(f"     Description: {target.get('description', 'N/A')}")
                print(f"     Status: {target.get('status', 'N/A')}")

    print("\n🎉 Gateway creation and configuration completed successfully!")
    if gateway_url:
        print(f"🔗 Gateway URL: {gateway_url}")
    logging.info("Gateway creation and configuration completed successfully")


if __name__ == "__main__":
    main()
