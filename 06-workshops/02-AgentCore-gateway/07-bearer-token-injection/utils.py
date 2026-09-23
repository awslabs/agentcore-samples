"""
Utility functions for Asana Integration Demo AgentCore setup and management.

This module provides helper functions for:
- AWS SSM parameter management
- Cognito user pool setup and authentication
- IAM role and policy creation for AgentCore
- DynamoDB operations
- AWS Secrets Manager operations
- Resource cleanup functions
"""

import json
import os

import boto3
import requests
import time

STS_CLIENT = boto3.client("sts")

# Get AWS account details
REGION = boto3.session.Session().region_name

# Configuration constants - use environment variables in production
USERNAME = os.environ.get("DEMO_USERNAME", "testuser")
SECRET_NAME = os.environ.get("DEMO_SECRET_NAME", "asana_integration_demo_agent")

ROLE_NAME = os.environ.get("ROLE_NAME", "AgentCoreGwyAsanaIntegrationRole")
POLICY_NAME = os.environ.get("POLICY_NAME", "AgentCoreGwyAsanaIntegrationPolicy")


def load_api_spec(file_path: str) -> list:
    """Load API specification from JSON file.

    Args:
        file_path: Path to the JSON file containing API specification

    Returns:
        List containing the API specification data

    Raises:
        ValueError: If the JSON file doesn't contain a list or is invalid
        FileNotFoundError: If the file doesn't exist
        json.JSONDecodeError: If the file contains invalid JSON
    """
    # Validate file path
    if not file_path or not isinstance(file_path, str):
        raise ValueError("file_path must be a non-empty string")

    # Check if file exists and is readable
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"API specification file not found: {file_path}")

    if not os.access(file_path, os.R_OK):
        raise PermissionError(f"Cannot read API specification file: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"Invalid JSON in API specification file: {e}", e.doc, e.pos)

    if not isinstance(data, list):
        raise ValueError("Expected a list in the JSON file")

    # Basic validation of API spec structure
    if not data:
        raise ValueError("API specification list cannot be empty")

    return data


def get_ssm_parameter(name: str, with_decryption: bool = True) -> str:
    """Get parameter value from AWS Systems Manager Parameter Store.

    Args:
        name: Parameter name to retrieve
        with_decryption: Whether to decrypt secure string parameters

    Returns:
        Parameter value as string
    """
    ssm = boto3.client("ssm")
    response = ssm.get_parameter(Name=name, WithDecryption=with_decryption)
    return response["Parameter"]["Value"]


def put_ssm_parameter(name: str, value: str, parameter_type: str = "String", with_encryption: bool = False) -> None:
    """Store parameter value in AWS Systems Manager Parameter Store.

    Args:
        name: Parameter name to store
        value: Parameter value to store
        parameter_type: Type of parameter (String, StringList, SecureString)
        with_encryption: Whether to encrypt the parameter as SecureString
    """
    ssm = boto3.client("ssm")

    put_params = {
        "Name": name,
        "Value": value,
        "Type": parameter_type,
        "Overwrite": True,
    }

    if with_encryption:
        put_params["Type"] = "SecureString"

    ssm.put_parameter(**put_params)


def get_cognito_client_secret() -> str:
    """Get Cognito user pool client secret.

    Returns:
        Client secret string from Cognito user pool client
    """
    client = boto3.client("cognito-idp")
    response = client.describe_user_pool_client(
        UserPoolId=get_ssm_parameter("/app/asana/demo/agentcoregwy/userpool_id"),
        ClientId=get_ssm_parameter("/app/asana/demo/agentcoregwy/machine_client_id"),
    )
    return response["UserPoolClient"]["ClientSecret"]


def fetch_access_token(client_id, client_secret, token_url):
    """Fetch OAuth access token using client credentials flow.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        token_url: OAuth token endpoint URL

    Returns:
        Access token string

    Raises:
        ValueError: If required parameters are missing or invalid
        requests.RequestException: If the HTTP request fails
        KeyError: If the response doesn't contain an access token
    """
    # Input validation
    if not all([client_id, client_secret, token_url]):
        raise ValueError("client_id, client_secret, and token_url are required")

    if not token_url.startswith(("https://", "http://")):
        raise ValueError("token_url must be a valid HTTP/HTTPS URL")

    data = f"grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}"

    try:
        response = requests.post(
            token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
            verify=True,  # Ensure SSL verification is enabled
        )
        response.raise_for_status()  # Raise an exception for bad status codes

        response_data = response.json()

        if "access_token" not in response_data:
            raise KeyError("Response does not contain 'access_token' field")

        return response_data["access_token"]

    except requests.exceptions.Timeout:
        raise requests.RequestException("Request timed out while fetching access token")
    except requests.exceptions.ConnectionError:
        raise requests.RequestException("Connection error while fetching access token")
    except requests.exceptions.HTTPError as e:
        raise requests.RequestException(f"HTTP error while fetching access token: {e}")
    except json.JSONDecodeError:
        raise requests.RequestException("Invalid JSON response from token endpoint")


def delete_gateway(gateway_client, gateway_name):
    """Delete AgentCore gateway and all its targets.

    Args:
        gateway_client: Boto3 client for bedrock-agentcore-control
        gateway_id: ID of the gateway to delete
    """
    gateway_id = get_ssm_parameter("/app/asana/demo/agentcoregwy/gateway_id")

    print("Deleting all targets for gateway", gateway_id)
    list_response = gateway_client.list_gateway_targets(gatewayIdentifier=gateway_id, maxResults=100)
    for item in list_response["items"]:
        target_id = item["targetId"]
        print("Deleting target ", target_id)
        gateway_client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
    # wait for 30 secs
    time.sleep(30)

    list_response = gateway_client.list_gateway_targets(gatewayIdentifier=gateway_id, maxResults=100)
    if len(list_response["items"]) > 0:
        print(f"{len(list_response['items'])} targets not deleted successfully)")
    else:
        print("All targets deleted successfully)")

    print("Deleting gateway ", gateway_id)
    gateway_client.delete_gateway(gatewayIdentifier=gateway_id)
