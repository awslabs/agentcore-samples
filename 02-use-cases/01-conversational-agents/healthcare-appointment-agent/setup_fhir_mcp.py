from dotenv import load_dotenv
import os
import json
import yaml
import argparse
import time
import utils
import botocore

load_dotenv()

# setting parameters
parser = argparse.ArgumentParser(
    prog="setup_fhir_mcp",
    description="Setup MCP gateway for FHIR tools",
    epilog="Input Parameters",
)

parser.add_argument("--op_type", help="Type of operation - Create or Delete")
parser.add_argument("--gateway_name", help="The name of gateway")
parser.add_argument("--gateway_id", help="Gateway Id")

# create boto3 session and client
(boto_session, agentcore_client) = utils.create_agentcore_client()


def read_and_stringify_openapispec(yaml_file_path):
    try:
        with open(yaml_file_path, "r") as file:
            # Parse YAML to Python dictionary
            openapi_dict = yaml.safe_load(file)

            # Convert dictionary to JSON string
            openapi_string = str(json.dumps(openapi_dict))

            return openapi_string

    except FileNotFoundError:
        return f"Error: File {yaml_file_path} not found"
    except yaml.YAMLError as e:
        return f"Error parsing YAML: {str(e)}"
    except Exception as e:
        return f"An unexpected error occurred: {str(e)}"


def create_gateway(gateway_name, gateway_desc):
    auth_config = {
        "customJWTAuthorizer": {
            "allowedClients": [os.getenv("cognito_client_id")],
            "discoveryUrl": os.getenv("cognito_discovery_url"),
        }
    }

    search_config = {"mcp": {"searchType": "SEMANTIC", "supportedVersions": ["2025-03-26"]}}

    response = agentcore_client.create_gateway(
        name=gateway_name,
        roleArn=os.getenv("gateway_iam_role"),
        # kmsKeyArn="<kms key here>",
        authorizerType="CUSTOM_JWT",
        description=gateway_desc,
        protocolType="MCP",
        authorizerConfiguration=auth_config,
        protocolConfiguration=search_config,
    )

    # print(json.dumps(response, indent=2, default=str))
    return response["gatewayId"]


def create_gatewaytarget(gateway_id, cred_provider_arn):
    openapi_spec = read_and_stringify_openapispec(os.getenv("openapi_spec_file"))

    credentiaConfig = {
        "credentialProviderType": "OAUTH",
        "credentialProvider": {
            "oauthCredentialProvider": {
                "providerArn": cred_provider_arn,
                "scopes": [os.getenv("cognito_auth_scope")],
            }
        },
    }

    response = agentcore_client.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name="Target1",
        description="Target 1",
        targetConfiguration={"mcp": {"openApiSchema": {"inlinePayload": openapi_spec}}},
        credentialProviderConfigurations=[credentiaConfig],
    )

    # print(response)
    return response["targetId"]


def delete_gatewaytarget(gateway_id):
    response = agentcore_client.list_gateway_targets(gatewayIdentifier=gateway_id)

    print(f"Found {len(response['items'])} targets for the gateway")

    for target in response["items"]:
        print(f"Deleting target with Name: {target['name']} and Id: {target['targetId']}")

        response = agentcore_client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target["targetId"])


def delete_gateway(gateway_id):
    agentcore_client.delete_gateway(gatewayIdentifier=gateway_id)


def create_egress_oauth_provider(gateway_name):
    cred_provider_name = f"{gateway_name}-oauth-credential-provider"

    try:
        agentcore_client.delete_oauth2_credential_provider(name=cred_provider_name)
        print(f"Deleted existing egress credential provider with name {cred_provider_name}")
        time.sleep(15)
    except botocore.exceptions.ClientError as err:
        raise Exception(
            f"An error ocurred with code: {err.response['Error']['Code']} and message: {err.response['Error']['Message']}"
        )

    try:
        provider_config = {
            "customOauth2ProviderConfig": {
                "oauthDiscovery": {
                    "authorizationServerMetadata": {
                        "issuer": os.getenv("cognito_issuer"),
                        "authorizationEndpoint": os.getenv("cognito_auth_endpoint"),
                        "tokenEndpoint": os.getenv("cognito_token_url"),
                        "responseTypes": ["token"],
                    }
                },
                "clientId": os.getenv("cognito_client_id"),
                "clientSecret": utils.get_cognito_client_secret(boto_session),
            }
        }

        response = agentcore_client.create_oauth2_credential_provider(
            name=cred_provider_name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=provider_config,
        )

        credentialProviderArn = response["credentialProviderArn"]
        return credentialProviderArn
    except botocore.exceptions.ClientError as err:
        raise Exception(
            f"An error ocurred with code: {err.response['Error']['Code']} and message: {err.response['Error']['Message']}"
        )


if __name__ == "__main__":
    args = parser.parse_args()

    # Validations
    if args.op_type is None:
        raise Exception("Operation Type is required")
    else:
        if args.op_type.lower() == "create" or args.op_type.lower() == "delete":
            print(f"Operation Type = {args.op_type}")
        else:
            raise Exception("Operation Type must be either Create or Delete")

    if args.gateway_name is None and args.op_type.lower() == "create":
        raise Exception("Gateway name is required when operation type is Create")
    elif args.gateway_name is not None:
        print(f"Gateway Name = {args.gateway_name}")

    if args.gateway_id is None and args.op_type.lower() == "delete":
        raise Exception("Gateway Id is required when operation type is Delete")
    elif args.gateway_id is not None:
        print(f"Gateway Id = {args.gateway_id}")

    if args.op_type.lower() == "create":
        print(f"Create gateway with name: {args.gateway_name}")
        gatewayId = create_gateway(gateway_name=args.gateway_name, gateway_desc=args.gateway_name)
        print(f"Gateway created with id: {gatewayId}. Creating credential provider.")

        credProviderARN = create_egress_oauth_provider(gateway_name=args.gateway_name)
        print("Egress credential provider created. Creating gateway target.")

        targetId = create_gatewaytarget(gateway_id=gatewayId, cred_provider_arn=credProviderARN)
        print(f"Target created with id: {targetId}")
    elif args.op_type.lower() == "delete":
        print(f"Find and delete targets for gateway id: {args.gateway_id}")
        delete_gatewaytarget(gateway_id=args.gateway_id)
        print(f"Delete gateway with id: {args.gateway_id}")
        delete_gateway(gateway_id=args.gateway_id)
        print("Gateway deleted")
