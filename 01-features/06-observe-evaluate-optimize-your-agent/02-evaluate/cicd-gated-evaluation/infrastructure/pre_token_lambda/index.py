# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pre-token-generation Lambda V2: copies custom:roles into access token claims.

Security: Only roles in the VALID_ROLES allowlist are injected into the token.
Unknown or malformed role values are stripped to prevent privilege escalation.
"""

VALID_ROLES = {"FinanceUser", "HRUser"}


def handler(event, context):
    raw_roles = event["request"]["userAttributes"].get("custom:roles", "")

    # Validate each role against the allowlist
    validated = [role.strip() for role in raw_roles.split(",") if role.strip() in VALID_ROLES]

    event["response"] = {
        "claimsAndScopeOverrideDetails": {
            "accessTokenGeneration": {
                "claimsToAddOrOverride": {"custom:roles": ",".join(validated)},
            }
        }
    }
    return event
