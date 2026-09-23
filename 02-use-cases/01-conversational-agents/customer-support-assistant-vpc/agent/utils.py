import boto3
import json
import requests
from typing import Dict


def get_ssm_parameter(name: str, with_decryption: bool = True) -> str:
    ssm = boto3.client("ssm")

    response = ssm.get_parameter(Name=name, WithDecryption=with_decryption)

    return response["Parameter"]["Value"]


def get_secret(secret_name: str) -> Dict:
    """Get secret from AWS Secrets Manager"""
    client = boto3.client("secretsmanager")

    response = client.get_secret_value(SecretId=secret_name)

    return json.loads(response["SecretString"])


def get_gateway_access_token() -> str:
    """
    Get OAuth2 access token from Cognito for gateway access

    Returns:
        str: Access token
    """
    # Get credentials from Secrets Manager
    secret_name = "agentcore-cognito-m2m-stack/agent/client-config"
    secret = get_secret(secret_name)

    # Extract values from secret
    token_endpoint = secret["token_endpoint"]
    client_id = secret["client_id"]
    client_secret = secret["client_secret"]
    scope = secret.get("scope", "")

    # Prepare request
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }

    if scope:
        data["scope"] = scope

    # Make token request
    response = requests.post(token_endpoint, headers=headers, data=data)
    response.raise_for_status()

    token_response = response.json()

    return token_response["access_token"]
