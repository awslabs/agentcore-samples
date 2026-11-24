"""Cognito pre-token-generation (V2): copy the persona's tenant and role into the access token.

The Gateway's CUSTOM_JWT authorizer and the Cedar fence read these claims; the agent never
decides who the caller is. Missing attributes fail closed (no claims added, so policy denies).
"""

CLAIMS = {"custom:customer_id": "customer_id", "custom:role": "role"}


def lambda_handler(event, _context):
    attrs = event.get("request", {}).get("userAttributes", {})
    claims = {claim: attrs[attr] for attr, claim in CLAIMS.items() if attrs.get(attr)}
    event["response"] = {
        "claimsAndScopeOverrideDetails": {
            "accessTokenGeneration": {"claimsToAddOrOverride": claims},
            "idTokenGeneration": {"claimsToAddOrOverride": claims},
        }
    }
    return event
