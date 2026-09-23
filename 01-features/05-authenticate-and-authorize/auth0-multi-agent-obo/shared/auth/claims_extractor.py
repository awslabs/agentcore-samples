# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Claims extraction utilities for AgentCore Identity."""

from typing import Any, Optional

from ..models.user_context import UserContext


# Default claims namespace for custom claims
DEFAULT_CLAIMS_NAMESPACE = "https://agentcore.example.com/"


def extract_standard_claims(claims: dict[str, Any]) -> dict[str, Any]:
    """
    Extract standard OIDC claims from a JWT claims dictionary.

    Args:
        claims: The decoded JWT claims dictionary

    Returns:
        Dictionary containing standard OIDC claims
    """
    standard_claims = {
        # Subject (user identifier)
        "sub": claims.get("sub", ""),
        # User profile information
        "name": claims.get("name", ""),
        "given_name": claims.get("given_name", ""),
        "family_name": claims.get("family_name", ""),
        "middle_name": claims.get("middle_name", ""),
        "nickname": claims.get("nickname", ""),
        "preferred_username": claims.get("preferred_username", ""),
        # Contact information
        "email": claims.get("email", ""),
        "email_verified": claims.get("email_verified", False),
        "phone_number": claims.get("phone_number", ""),
        "phone_number_verified": claims.get("phone_number_verified", False),
        # Additional profile
        "picture": claims.get("picture", ""),
        "locale": claims.get("locale", ""),
        "updated_at": claims.get("updated_at", ""),
        # Token metadata
        "iss": claims.get("iss", ""),
        "aud": claims.get("aud", ""),
        "exp": claims.get("exp", 0),
        "iat": claims.get("iat", 0),
        "nbf": claims.get("nbf", 0),
        # Authorization
        "scope": claims.get("scope", ""),
        "azp": claims.get("azp", ""),
    }

    return standard_claims


def extract_custom_claims(claims: dict[str, Any], namespace: str = DEFAULT_CLAIMS_NAMESPACE) -> dict[str, Any]:
    """
    Extract custom namespaced claims from a JWT claims dictionary.

    Custom claims in Auth0 must be namespaced to avoid conflicts with
    standard OIDC claims. This function extracts claims with the specified
    namespace and returns them with the namespace prefix removed.

    Args:
        claims: The decoded JWT claims dictionary
        namespace: The namespace prefix for custom claims

    Returns:
        Dictionary containing custom claims with namespace prefix removed

    Example:
        >>> claims = {
        ...     "sub": "auth0|123",
        ...     "https://agentcore.example.com/customer_id": "CUST-001",
        ...     "https://agentcore.example.com/account_types": ["transaction"]
        ... }
        >>> extract_custom_claims(claims)
        {"customer_id": "CUST-001", "account_types": ["transaction"]}
    """
    custom_claims = {}

    for key, value in claims.items():
        if key.startswith(namespace):
            # Remove namespace prefix to get the claim name
            claim_name = key[len(namespace) :]
            custom_claims[claim_name] = value

    return custom_claims


def get_claim_value(
    claims: dict[str, Any],
    claim_name: str,
    default: Any = None,
    namespace: Optional[str] = DEFAULT_CLAIMS_NAMESPACE,
) -> Any:
    """
    Get a claim value, checking both standard and namespaced versions.

    Args:
        claims: The decoded JWT claims dictionary
        claim_name: The claim name to retrieve
        default: Default value if claim not found
        namespace: The namespace to check (None to skip namespace check)

    Returns:
        The claim value or default if not found
    """
    # First check for standard claim
    if claim_name in claims:
        return claims[claim_name]

    # Then check for namespaced claim
    if namespace:
        namespaced_key = f"{namespace}{claim_name}"
        if namespaced_key in claims:
            return claims[namespaced_key]

    return default


def extract_scopes(claims: dict[str, Any]) -> list[str]:
    """
    Extract and parse scopes from claims.

    Scopes can be provided as a space-separated string or as a list.

    Args:
        claims: The decoded JWT claims dictionary

    Returns:
        List of scope strings
    """
    scope_value = claims.get("scope", "")

    if isinstance(scope_value, list):
        return scope_value
    elif isinstance(scope_value, str):
        return scope_value.split() if scope_value else []
    else:
        return []


def extract_permissions(claims: dict[str, Any], namespace: str = DEFAULT_CLAIMS_NAMESPACE) -> list[str]:
    """
    Extract permissions from claims.

    Permissions may be in a 'permissions' claim or a namespaced claim.

    Args:
        claims: The decoded JWT claims dictionary
        namespace: The namespace for custom claims

    Returns:
        List of permission strings
    """
    # Check standard permissions claim
    permissions = claims.get("permissions", [])
    if permissions:
        return permissions if isinstance(permissions, list) else [permissions]

    # Check namespaced permissions
    namespaced_key = f"{namespace}permissions"
    permissions = claims.get(namespaced_key, [])
    return permissions if isinstance(permissions, list) else [permissions]


def build_user_context(claims: dict[str, Any], namespace: str = DEFAULT_CLAIMS_NAMESPACE) -> UserContext:
    """
    Build a UserContext instance from JWT claims.

    This is a convenience function that combines claims extraction
    with UserContext construction.

    Args:
        claims: The decoded JWT claims dictionary
        namespace: The namespace for custom claims

    Returns:
        UserContext instance populated from claims
    """
    return UserContext.from_jwt_claims(claims, claims_namespace=namespace)


def validate_required_claims(
    claims: dict[str, Any],
    required_claims: list[str],
    namespace: Optional[str] = DEFAULT_CLAIMS_NAMESPACE,
) -> tuple[bool, list[str]]:
    """
    Validate that all required claims are present.

    Args:
        claims: The decoded JWT claims dictionary
        required_claims: List of required claim names
        namespace: The namespace to check for custom claims

    Returns:
        Tuple of (all_present, missing_claims)
    """
    missing_claims = []

    for claim_name in required_claims:
        value = get_claim_value(claims, claim_name, namespace=namespace)
        if value is None or value == "":
            missing_claims.append(claim_name)

    return len(missing_claims) == 0, missing_claims


def extract_user_metadata(claims: dict[str, Any], namespace: str = DEFAULT_CLAIMS_NAMESPACE) -> dict[str, Any]:
    """
    Extract user metadata from claims.

    User metadata often includes additional profile information stored
    in Auth0 user_metadata or app_metadata.

    Args:
        claims: The decoded JWT claims dictionary
        namespace: The namespace for custom claims

    Returns:
        Dictionary containing user metadata
    """
    metadata = {}

    # Check for standard metadata claims
    user_metadata_key = f"{namespace}user_metadata"
    app_metadata_key = f"{namespace}app_metadata"

    if user_metadata_key in claims:
        metadata["user_metadata"] = claims[user_metadata_key]

    if app_metadata_key in claims:
        metadata["app_metadata"] = claims[app_metadata_key]

    return metadata


def format_claim_for_logging(claims: dict[str, Any]) -> dict[str, Any]:
    """
    Format claims for safe logging by removing sensitive information.

    Args:
        claims: The decoded JWT claims dictionary

    Returns:
        Sanitized claims dictionary safe for logging
    """
    sensitive_keys = [
        "email",
        "phone_number",
        "phone",
        "address",
        "birthdate",
        "date_of_birth",
    ]

    sanitized = {}
    for key, value in claims.items():
        # Check if key contains sensitive information
        is_sensitive = any(sens in key.lower() for sens in sensitive_keys)

        if is_sensitive and isinstance(value, str) and value:
            # Mask sensitive strings
            sanitized[key] = "***REDACTED***"
        else:
            sanitized[key] = value

    return sanitized
