import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

RESOURCE_SERVER_ID = os.environ.get("RESOURCE_SERVER_ID", "")


def lambda_handler(event, context):
    """
    Cognito Pre-Token Generation Lambda Trigger
    Adds custom claims to the ID token based on user's email
    """

    logger.info(f"Pre-token generation event: {json.dumps(event)}")

    try:
        # Extract user attributes from the event
        user_attributes = event["request"]["userAttributes"]
        email = user_attributes.get("email", "")

        logger.info(f"Processing token for user with email: {email}")

        # Add custom claims based on email domain or specific rules
        # Example: Add a custom tag based on email domain
        custom_tag = "default_user"

        if email == "vscode-admin@example.com":
            # Example: Set custom tag based on email
            custom_tag = "admin_user"
        elif email == "vscode-readonly@example.com":
            # Test user with limited scopes — only mcp.read, no mcp.write
            # Used to verify the gateway rejects requests with insufficient scopes
            custom_tag = "readonly_user"
        else:
            custom_tag = "regular_user"

        # Add custom claims to the ID token
        # Note: You can add to claimsOverrideDetails for ID token
        if (
            "claimsOverrideDetails" not in event["response"]
            or event["response"]["claimsOverrideDetails"] is None
        ):
            event["response"]["claimsOverrideDetails"] = {}

        if "claimsToAddOrOverride" not in event["response"]["claimsOverrideDetails"]:
            event["response"]["claimsOverrideDetails"]["claimsToAddOrOverride"] = {}

        # Add custom claims to ID token
        event["response"]["claimsOverrideDetails"]["claimsToAddOrOverride"][
            "user_tag"
        ] = custom_tag
        event["response"]["claimsOverrideDetails"]["claimsToAddOrOverride"]["email"] = (
            email
        )

        # Add custom claims to the Access token (V2 trigger)
        if (
            "claimsAndScopeOverrideDetails" not in event["response"]
            or event["response"]["claimsAndScopeOverrideDetails"] is None
        ):
            event["response"]["claimsAndScopeOverrideDetails"] = {}

        if (
            "accessTokenGeneration"
            not in event["response"]["claimsAndScopeOverrideDetails"]
        ):
            event["response"]["claimsAndScopeOverrideDetails"][
                "accessTokenGeneration"
            ] = {}

        if (
            "claimsToAddOrOverride"
            not in event["response"]["claimsAndScopeOverrideDetails"][
                "accessTokenGeneration"
            ]
        ):
            event["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"][
                "claimsToAddOrOverride"
            ] = {}

        # Add email, user_tag, and aud to access token
        event["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"][
            "claimsToAddOrOverride"
        ]["email"] = email
        event["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"][
            "claimsToAddOrOverride"
        ]["user_tag"] = custom_tag
        # Inject the audience claim so the proxy Lambda and AgentCore Gateway
        # can verify the token is scoped to this resource server.
        # Cognito requires aud to match the current session's app client ID.
        client_id = event.get("callerContext", {}).get("clientId", "")
        if client_id:
            event["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"][
                "claimsToAddOrOverride"
            ]["aud"] = client_id

        # For the readonly test user, suppress the mcp.write and mcp.read scopes so the
        # gateway rejects write operations with insufficient_scope.
        if custom_tag == "readonly_user":
            event["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"][
                "scopesToSuppress"
            ] = [
                f"{RESOURCE_SERVER_ID}/mcp.write",
                f"{RESOURCE_SERVER_ID}/mcp.read",
            ]
            logger.info(
                "Suppressed mcp.write and mcp.read scopes for readonly test user"
            )

        logger.info(
            f"Added custom claims to ID token and Access token: "
            f"user_tag={custom_tag}, email={email}"
        )

    except Exception as e:
        logger.error(f"Error in pre-token generation: {str(e)}", exc_info=True)
        # Don't fail the authentication, just log the error
        pass

    return event
