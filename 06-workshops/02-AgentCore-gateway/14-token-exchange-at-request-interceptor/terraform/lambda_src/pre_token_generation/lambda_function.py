import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    logger.info("Pre Token Generation Lambda triggered")
    logger.info("Trigger Source: %s", event.get("triggerSource", "Unknown"))

    # V3_0 format for both ID and access token customization
    event["response"]["claimsAndScopeOverrideDetails"] = {
        "idTokenGeneration": {
            "claimsToAddOrOverride": {
                "custom:role": "agentcore_user",
                "custom:permissions": "read,write",
                "custom:tenant": "default",
                "custom:api_access": "enabled",
            },
            "claimsToSuppress": [],
        },
        "accessTokenGeneration": {
            "claimsToAddOrOverride": {
                "custom:role": "agentcore_user",
                "custom:permissions": "read,write",
                "custom:tenant": "default",
                "custom:api_access": "enabled",
            },
            "claimsToSuppress": [],
            "scopesToAdd": [],
            "scopesToSuppress": [],
        },
    }

    logger.info("Custom claims added to both ID and access tokens")
    return event
