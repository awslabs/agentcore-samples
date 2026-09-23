"""
AgentCore Gateway Creation Module.

This module handles the creation and configuration of AWS Bedrock AgentCore gateways
for Asana integration, including target configuration and credential management.
"""

import json
import os
import sys

import boto3

# Add parent directory to path to import utils
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.join(current_dir, "..", "..")
sys.path.insert(0, parent_dir)

try:
    from utils import get_ssm_parameter, put_ssm_parameter
except ImportError as e:
    print(f"Error importing utils: {e}")
    print(f"Current directory: {current_dir}")
    print(f"Parent directory: {parent_dir}")
    print(f"Python path: {sys.path}")
    raise

STS_CLIENT = boto3.client("sts")

# Get AWS account details
REGION = boto3.session.Session().region_name

GATEWAY_CLIENT = boto3.client(
    "bedrock-agentcore-control",
    region_name=REGION,
)

print("✅ Fetching AgentCore gateway!")

GATEWAY_NAME = "agentcore-gw-asana-integration"


def create_agentcore_gateway():
    """Create or retrieve existing AgentCore gateway.

    Returns:
        Dictionary containing gateway information (id, name, url, arn)

    Raises:
        ValueError: If required SSM parameters are missing
        Exception: If gateway creation or retrieval fails
    """
    try:
        # Validate required SSM parameters exist
        machine_client_id = get_ssm_parameter("/app/asana/demo/agentcoregwy/machine_client_id")
        cognito_discovery_url = get_ssm_parameter("/app/asana/demo/agentcoregwy/cognito_discovery_url")
        gateway_iam_role = get_ssm_parameter("/app/asana/demo/agentcoregwy/gateway_iam_role")

        if not all([machine_client_id, cognito_discovery_url, gateway_iam_role]):
            raise ValueError("Required SSM parameters are missing or empty")

        auth_config = {
            "customJWTAuthorizer": {
                "allowedClients": [machine_client_id],
                "discoveryUrl": cognito_discovery_url,
            }
        }

        # create new gateway
        print(f"Creating gateway in region {REGION} with name: {GATEWAY_NAME}")

        create_response = GATEWAY_CLIENT.create_gateway(
            name=GATEWAY_NAME,
            roleArn=gateway_iam_role,
            protocolType="MCP",
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration=auth_config,
            description="Asana Integration Demo AgentCore Gateway",
        )

        gateway_id = create_response["gatewayId"]

        gateway_info = {
            "id": gateway_id,
            "name": GATEWAY_NAME,
            "gateway_url": create_response["gatewayUrl"],
            "gateway_arn": create_response["gatewayArn"],
        }
        put_ssm_parameter("/app/asana/demo/agentcoregwy/gateway_id", gateway_id)

        print(f"✅ Gateway created successfully with ID: {gateway_id}")

        return gateway_info

    except (
        GATEWAY_CLIENT.exceptions.ConflictException,
        GATEWAY_CLIENT.exceptions.ValidationException,
    ) as exc:
        # If gateway exists, collect existing gateway ID from SSM
        print(f"Gateway creation failed: {exc}")
        try:
            existing_gateway_id = get_ssm_parameter("/app/asana/demo/agentcoregwy/gateway_id")
            if not existing_gateway_id:
                raise ValueError("Gateway ID parameter exists but is empty") from exc

            print(f"Found existing gateway with ID: {existing_gateway_id}")

            # Get existing gateway details
            gateway_response = GATEWAY_CLIENT.get_gateway(gatewayIdentifier=existing_gateway_id)
            gateway_info = {
                "id": existing_gateway_id,
                "name": gateway_response["name"],
                "gateway_url": gateway_response["gatewayUrl"],
                "gateway_arn": gateway_response["gatewayArn"],
            }
            return gateway_info
        except ValueError as ve:
            raise ve
        except Exception as e:
            raise RuntimeError(f"Failed to retrieve existing gateway: {str(e)}") from e
    except ValueError as ve:
        raise ve
    except Exception as e:
        raise RuntimeError(f"Unexpected error in gateway creation: {str(e)}") from e


def load_api_spec(file_path: str) -> list:
    """Load API specification from JSON file.

    Args:
        file_path: Path to the JSON file containing API specification

    Returns:
        List containing the API specification data

    Raises:
        ValueError: If the JSON file doesn't contain a list
    """
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Expected a list in the JSON file")
    return data


def add_gateway_target(gateway_id):
    """Add gateway target with API specification and credential configuration.

    Args:
        gateway_id: ID of the gateway to add target to
    """
    try:
        api_spec_file = "../openapi-spec/openapi_simple.json"

        # Validate API spec file exists
        if not os.path.exists(api_spec_file):
            print(f"❌ API specification file not found: {api_spec_file}")
            sys.exit(1)

        api_spec = load_api_spec(api_spec_file)
        print(f"✅ Loaded API specification file: {api_spec}")

        # Validate API spec structure
        if not api_spec or not isinstance(api_spec[0], dict):
            raise ValueError("Invalid API specification structure")

        if "servers" not in api_spec[0] or not api_spec[0]["servers"]:
            raise ValueError("API specification missing servers configuration")

        api_gateway_url = get_ssm_parameter("/app/asana/demo/agentcoregwy/apigateway_url")

        # Validate API Gateway URL
        if not api_gateway_url or not api_gateway_url.startswith("https://"):
            raise ValueError("Invalid API Gateway URL - must be HTTPS")

        api_spec[0]["servers"][0]["url"] = api_gateway_url

        print(f"✅ Replaced API Gateway URL: {api_gateway_url}")

        print("✅ Creating credential provider...")
        acps = boto3.client(service_name="bedrock-agentcore-control")

        credential_provider_name = "AgentCoreAPIGatewayAPIKey"

        existing_credential_provider_response = acps.get_api_key_credential_provider(name=credential_provider_name)
        provider_arn = existing_credential_provider_response["credentialProviderArn"]
        print(f"Found existing credential provider with ARN: {provider_arn}")

        if provider_arn is None:
            print(f"❌ Credential provider not found, creating new: {credential_provider_name}")
            response = acps.create_api_key_credential_provider(
                name=credential_provider_name,
                apiKey=get_ssm_parameter("/app/asana/demo/agentcoregwy/api_key"),
            )

            print(response)
            credential_provider_arn = response["credentialProviderArn"]
            print(f"Outbound Credentials provider ARN, {credential_provider_arn}")
        else:
            credential_provider_arn = provider_arn

        # API Key credentials provider configuration
        api_key_credential_config = [
            {
                "credentialProviderType": "API_KEY",
                "credentialProvider": {
                    "apiKeyCredentialProvider": {
                        # API key name expected by the API Gateway authorizer
                        "credentialParameterName": "x-api-key",
                        "providerArn": credential_provider_arn,
                        # Location of api key - must match API Gateway expectation
                        "credentialLocation": "HEADER",
                        # "credentialPrefix": " "  # Prefix for token, e.g., "Basic"
                    }
                },
            }
        ]

        inline_spec = json.dumps(api_spec[0])
        print(f"✅ Created inline_spec: {inline_spec}")
        # S3 Uri for OpenAPI spec file
        agentcoregwy_openapi_target_config = {"mcp": {"openApiSchema": {"inlinePayload": inline_spec}}}
        print("✅ Creating gateway target...")
        create_target_response = GATEWAY_CLIENT.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name="AgentCoreGwyAPIGatewayTarget",
            description="APIGateway Target for Asana and other 3P APIs",
            targetConfiguration=agentcoregwy_openapi_target_config,
            credentialProviderConfigurations=api_key_credential_config,
        )

        print(f"✅ Gateway target created: {create_target_response['targetId']}")

    except GATEWAY_CLIENT.exceptions.ConflictException as exc:
        print(f"❌ Gateway target already exists: {str(exc)}")
        # Could implement logic to update existing target if needed
    except GATEWAY_CLIENT.exceptions.ValidationException as exc:
        print(f"❌ Validation error creating gateway target: {str(exc)}")
        raise
    except FileNotFoundError as exc:
        print(f"❌ API specification file not found: {str(exc)}")
        raise
    except ValueError as exc:
        print(f"❌ Invalid configuration: {str(exc)}")
        raise
    except Exception as exc:
        print(f"❌ Unexpected error creating gateway target: {str(exc)}")
        raise


if __name__ == "__main__":
    gateway = create_agentcore_gateway()
    add_gateway_target(gateway["id"])
