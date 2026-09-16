"""
Token Exchange Module for JWT to IAM Credentials

This module handles the exchange of JWT tokens for temporary IAM credentials
based on tenant role mappings stored in DynamoDB.

The exchange process:
1. Extracts claim_name and claim_value from JWT token
2. Looks up the mapping in DynamoDB table (lakehouse_tenant_role_map)
3. Retrieves the IAM role ARN from the mapping
4. Assumes the IAM role to get temporary credentials
5. Returns the temporary credentials for downstream use
"""

import logging
import os
from typing import Any, Optional

import boto3

logger = logging.getLogger()


def _resolve_idp_provider() -> str:
    """IdP selector for the Lambda (DR-8): env (set at deploy time) → SSM → cognito."""
    v = os.environ.get("IDP_PROVIDER")
    if v:
        return v.strip().lower()
    try:
        region = os.environ.get("AWS_REGION", "us-east-1")
        ssm = boto3.client("ssm", region_name=region)
        return ssm.get_parameter(Name="/app/lakehouse-agent/idp-provider")["Parameter"]["Value"].strip().lower()
    except Exception:
        return "cognito"


IDP_PROVIDER = _resolve_idp_provider()


def exchange_jwt_to_iam(claim_name: str, claim_value: str) -> dict[str, Any] | None:
    """
    Exchange JWT claim to IAM temporary credentials via DynamoDB role mapping.

    This function looks up the IAM role ARN from DynamoDB based on JWT claims
    (e.g., cognito:groups) and assumes that role to obtain temporary credentials.

    DynamoDB Table Schema:
    - Table Name: lakehouse_tenant_role_map
    - Composite Key: (claim_name, claim_value)
    - Attributes: role_type, role_value

    Example mapping:
    - Key: ("cognito:groups", '["adjusters"]')
    - Attributes: role_type="iam_role", role_value="arn:aws:iam::123456789012:role/lakehouse-adjusters-role"

    Args:
        claim_name: JWT claim name (e.g., "cognito:groups")
        claim_value: JWT claim value (e.g., '["adjusters"]')

    Returns:
        Dictionary with temporary credentials or None if exchange fails:
        {
            'AccessKeyId': str,
            'SecretAccessKey': str,
            'SessionToken': str,
            'Expiration': datetime,
            'RoleArn': str,
            'RoleName': str
        }
    """
    try:
        # Get table name from environment variable or SSM
        table_name = os.environ.get("TENANT_ROLE_MAPPING_TABLE")

        if not table_name:
            # Try to get from SSM Parameter Store
            try:
                ssm = boto3.client("ssm")
                response = ssm.get_parameter(Name="/app/lakehouse-agent/tenant-role-mapping-table")
                table_name = response["Parameter"]["Value"]
                logger.info(f"Loaded table name from SSM: {table_name}")
            except Exception as e:
                logger.error(f"Failed to get table name from SSM: {e}")
                # Fallback to default table name
                table_name = "lakehouse_tenant_role_map"
                logger.info(f"Using default table name: {table_name}")

        # Query DynamoDB for claim-to-role mapping
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)

        logger.info(f"🔍 Looking up role mapping for claim: {claim_name}={claim_value}")

        response = table.get_item(Key={"claim_name": claim_name, "claim_value": claim_value})

        if "Item" not in response:
            logger.warning(f"⚠️  No role mapping found for claim: {claim_name}={claim_value}")
            return None

        item = response["Item"]
        role_type = item.get("role_type")
        role_value = item.get("role_value")

        if role_type != "iam_role":
            logger.error(f"❌ Unexpected role_type: {role_type} (expected 'iam_role')")
            return None

        role_arn = role_value
        role_name = role_arn.split("/")[-1]

        logger.info(f"✅ Found role mapping: {claim_name}={claim_value} → {role_name}")
        logger.info(f"🔐 Assuming IAM role: {role_arn}")

        # Assume the IAM role
        sts_client = boto3.client("sts")

        # Create a safe session name from claim_value
        # Remove special characters and limit length
        safe_claim_value = claim_value.replace("[", "").replace("]", "").replace('"', "").replace(",", "-")
        session_name = f"lakehouse-{safe_claim_value[:32]}"

        response = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            DurationSeconds=3600,  # 1 hour
        )

        credentials = response["Credentials"]

        logger.info(f"✅ Successfully assumed role: {role_arn}")
        logger.info(f"   Session: {session_name}")
        logger.info(f"   Expires at: {credentials['Expiration']}")

        return {
            "AccessKeyId": credentials["AccessKeyId"],
            "SecretAccessKey": credentials["SecretAccessKey"],
            "SessionToken": credentials["SessionToken"],
            "Expiration": credentials["Expiration"],
            "RoleArn": role_arn,
            "RoleName": role_name,
        }

    except Exception as e:
        logger.error(f"❌ Error exchanging JWT to IAM credentials: {e!s}")
        import traceback

        logger.error(f"Stack trace: {traceback.format_exc()}")
        return None


def get_claim_for_exchange(claims: dict[str, Any]) -> tuple | None:
    """
    Extract the appropriate claim for token exchange from JWT claims.

    Priority order:
    1. cognito:groups - for group-based role mapping
    2. email - for user-specific role mapping
    3. username - for username-based role mapping

    Args:
        claims: Decoded JWT claims

    Returns:
        Tuple of (claim_name, claim_value) or None if no suitable claim found
    """
    import json

    if IDP_PROVIDER == "cognito":
        # [COGNITO] upstream verbatim: cognito:groups (whole-array) → email → username
        groups = claims.get("cognito:groups")
        if groups:
            claim_value = json.dumps(groups)
            logger.info(f"Found cognito:groups claim: {claim_value}")
            return ("cognito:groups", claim_value)

        email = claims.get("email")
        if email:
            logger.info(f"Found email claim: {email}")
            return ("email", email)

        username = claims.get("username") or claims.get("cognito:username")
        if username:
            logger.info(f"Found username claim: {username}")
            return ("username", username)
    else:  # okta
        # [OKTA] fork verbatim: Okta `groups` includes built-in `Everyone`, so filter
        # built-ins + iterate (per-app-group seed shape); then email → sub.
        OKTA_BUILTIN_GROUPS = {"Everyone"}  # Okta-specific; sibling IdPs revisit
        groups = claims.get("groups")
        if groups:
            for group in groups:
                if group in OKTA_BUILTIN_GROUPS:
                    continue
                claim_value = json.dumps([group])
                logger.info(f"Found groups claim (per-group iter): {claim_value}")
                return ("groups", claim_value)
            logger.warning(f"⚠️  All groups filtered as built-ins: {groups}")

        email = claims.get("email")
        if email:
            logger.info(f"Found email claim: {email}")
            return ("email", email)

        sub = claims.get("sub")
        if sub:
            logger.info(f"Found sub claim: {sub}")
            return ("sub", sub)

    logger.warning("⚠️  No suitable claim found for token exchange")
    return None
