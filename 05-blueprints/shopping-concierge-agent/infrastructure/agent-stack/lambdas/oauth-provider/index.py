"""
Custom Resource Lambda for OAuth2 Credential Provider Management

This Lambda creates and manages OAuth2 credential providers for AgentCore Gateway.
Since CDK doesn't support CfnOAuthProvider yet, we use boto3 API directly.

Version: 1.1 - Fixed OAuth provider configuration
"""

import json
import logging
import boto3
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

agentcore_client = boto3.client("bedrock-agentcore-control")


def handler(event, context):
    """Handle CloudFormation custom resource events"""
    logger.info(f"Received event: {json.dumps(event)}")

    request_type = event["RequestType"]
    props = event["ResourceProperties"]

    try:
        if request_type == "Create":
            return create_oauth_provider(event, props)
        elif request_type == "Update":
            return update_oauth_provider(event, props)
        elif request_type == "Delete":
            return delete_oauth_provider(event)
        else:
            raise ValueError(f"Unknown request type: {request_type}")
    except Exception as e:
        logger.error(f"Error handling {request_type}: {str(e)}")
        return send_response(event, "FAILED", str(e))


def create_oauth_provider(event, props):
    """Create OAuth2 credential provider"""
    logger.info("Creating OAuth2 credential provider...")

    provider_name = props["ProviderName"]
    user_pool_id = props["UserPoolId"]
    client_id = props["ClientId"]
    discovery_url = props["DiscoveryUrl"]

    # Fetch client secret from Cognito
    cognito_client = boto3.client("cognito-idp")
    try:
        response = cognito_client.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)
        client_secret = response["UserPoolClient"]["ClientSecret"]
        logger.info(f"Retrieved client secret for client {client_id}")
    except Exception as e:
        logger.error(f"Failed to retrieve client secret: {e}")
        raise

    # Check if provider already exists
    try:
        providers = agentcore_client.list_oauth2_credential_providers()
        for provider in providers.get("items", []):
            if provider["name"] == provider_name:
                logger.info(f"Provider already exists: {provider['credentialProviderArn']}")
                return send_response(
                    event,
                    "SUCCESS",
                    data={"ProviderArn": provider["credentialProviderArn"]},
                    physical_resource_id=provider_name,
                )
    except Exception as e:
        logger.warning(f"Error checking existing providers: {e}")

    # Create new provider using CustomOauth2 with discovery URL (matches Tutorial 05)
    response = agentcore_client.create_oauth2_credential_provider(
        name=provider_name,
        credentialProviderVendor="CustomOauth2",
        oauth2ProviderConfigInput={
            "customOauth2ProviderConfig": {
                "oauthDiscovery": {"discoveryUrl": discovery_url},
                "clientId": client_id,
                "clientSecret": client_secret,
            }
        },
    )

    provider_arn = response["credentialProviderArn"]
    logger.info(f"OAuth provider created: {provider_arn}")

    return send_response(
        event,
        "SUCCESS",
        data={"ProviderArn": provider_arn},
        physical_resource_id=provider_name,
    )


def update_oauth_provider(event, props):
    """Update OAuth2 credential provider"""
    provider_name = event["PhysicalResourceId"]
    old_props = event.get("OldResourceProperties", {})

    # If name changed, delete old and create new
    if old_props.get("ProviderName") != props.get("ProviderName"):
        logger.info("Provider name changed, recreating...")
        delete_oauth_provider(event)
        return create_oauth_provider(event, props)

    # For now, OAuth providers don't support updates
    # Return existing provider ARN
    try:
        providers = agentcore_client.list_oauth2_credential_providers()
        for provider in providers.get("items", []):
            if provider["name"] == provider_name:
                return send_response(
                    event,
                    "SUCCESS",
                    data={"ProviderArn": provider["credentialProviderArn"]},
                    physical_resource_id=provider_name,
                )
    except Exception as e:
        logger.error(f"Error finding provider: {e}")

    return send_response(event, "FAILED", "Provider not found")


def delete_oauth_provider(event):
    """Delete OAuth2 credential provider"""
    provider_name = event["PhysicalResourceId"]

    try:
        agentcore_client.delete_oauth2_credential_provider(name=provider_name)
        logger.info(f"OAuth provider deleted: {provider_name}")
    except Exception as e:
        logger.warning(f"Error deleting provider: {e}")

    return send_response(event, "SUCCESS", physical_resource_id=provider_name)


def send_response(event, status, reason=None, data=None, physical_resource_id=None):
    """Send response to CloudFormation"""
    response_body = {
        "Status": status,
        "Reason": reason or f"{status}: See CloudWatch logs",
        "PhysicalResourceId": physical_resource_id or event.get("PhysicalResourceId", "NONE"),
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": data or {},
    }

    logger.info(f"Sending response: {json.dumps(response_body)}")

    http = urllib3.PoolManager()
    http.request(
        "PUT",
        event["ResponseURL"],
        body=json.dumps(response_body).encode("utf-8"),
        headers={"Content-Type": ""},
    )

    return response_body
