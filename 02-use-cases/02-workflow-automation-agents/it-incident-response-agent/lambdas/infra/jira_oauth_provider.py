"""Custom resource: create/update/delete an AgentCore AtlassianOauth2 credential provider.

STEP: IDENTITY — Registers the Atlassian 3LO OAuth2 provider with AgentCore Identity.
The agent uses @requires_access_token(auth_flow="USER_FEDERATION") at runtime to obtain
Jira access tokens. AgentCore handles the OAuth dance internally — the agent never sees
the client_secret.

This Lambda is invoked by the CDK Provider framework (cr.Provider) which guarantees
that a CloudFormation response is always sent, even on unhandled exceptions.
"""

import json
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _get_client_secret(secret_arn: str) -> str:
    """Read the client_secret from Secrets Manager."""
    secrets = boto3.client("secretsmanager")
    raw = secrets.get_secret_value(SecretId=secret_arn)["SecretString"]
    return json.loads(raw)["client_secret"]


def handler(event, context):
    """CDK Provider onEvent handler for AtlassianOauth2 credential provider."""
    request_type = event["RequestType"]
    props = event["ResourceProperties"]
    provider_name = props["ProviderName"]
    logger.info("jira_oauth_provider %s for %s", request_type, provider_name)

    control = boto3.client("bedrock-agentcore-control")

    if request_type == "Delete":
        try:
            control.delete_oauth2_credential_provider(name=provider_name)
            logger.info("Deleted provider %s", provider_name)
        except control.exceptions.ResourceNotFoundException:
            logger.info("Provider %s already gone", provider_name)
        except Exception as exc:
            logger.warning("Delete failed (continuing): %s", exc)
        return {"PhysicalResourceId": provider_name}

    # Create or Update
    vendor = props.get("Vendor", "AtlassianOauth2")
    client_id = props["ClientId"]
    secret_arn = props["SecretArn"]
    client_secret = _get_client_secret(secret_arn)

    config = {
        "atlassianOauth2ProviderConfig": {
            "clientId": client_id,
            "clientSecret": client_secret,
        }
    }

    # Idempotent: if provider already exists (ConflictException on Create),
    # fall through to Update. PhysicalResourceId is the provider name (stable
    # across Create/Update), so CFN treats this as an in-place update.
    try:
        resp = control.create_oauth2_credential_provider(
            name=provider_name,
            credentialProviderVendor=vendor,
            oauth2ProviderConfigInput=config,
        )
        logger.info("Created provider %s", provider_name)
    except control.exceptions.ConflictException:
        resp = control.update_oauth2_credential_provider(
            name=provider_name,
            credentialProviderVendor=vendor,
            oauth2ProviderConfigInput=config,
        )
        logger.info("Updated provider %s", provider_name)

    callback_url = resp.get("callbackUrl", "")
    return {
        "PhysicalResourceId": provider_name,
        "Data": {
            "ProviderName": provider_name,
            "ProviderArn": resp.get("credentialProviderArn", ""),
            "CallbackUrl": callback_url,
        },
    }
