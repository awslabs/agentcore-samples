# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Authentication and Authorization Validator for Customer Profile Agent.

Validates JWT claims from inbound tokens and checks profile access authorization.
Supports dual-issuer validation: tokens from Auth0 (direct invocations) and tokens
from the AgentCore Token Exchange Service (RFC 8693 exchanged tokens from coordinator
with attenuated scopes -- only profile:* scopes should be present).
"""

import json
import logging
from typing import Any, Dict
from datetime import datetime

logger = logging.getLogger(__name__)

# Token Exchange Service issuer identifier
TOKEN_EXCHANGE_ISSUER = "urn:agentcore:token-exchange-service"


def is_exchanged_token(claims: Dict[str, Any]) -> bool:
    """
    Determine if a token was issued by the Token Exchange Service.

    Exchanged tokens contain both an `act` claim (RFC 8693 delegation chain)
    and an `exchange_id` claim (audit identifier).

    Args:
        claims: JWT claims dictionary

    Returns:
        True if the token has both `act` and `exchange_id` claims
    """
    return "act" in claims and "exchange_id" in claims


def validate_scope_for_profile(claims: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate that the token scopes are appropriate for the profile agent.

    Accepts fine-grained profile scopes (profile:personal:read, profile:preferences:read).
    If unexpected scopes are present (e.g., accounts:*), a warning is logged but
    validation still passes as long as at least one required scope is present.

    Args:
        claims: JWT claims dictionary

    Returns:
        Dict with "valid" boolean, "scopes" list, and optional "error" message
    """
    scope_str = claims.get("scope", "")
    if isinstance(scope_str, list):
        scopes = scope_str
    elif isinstance(scope_str, str):
        scopes = scope_str.split() if scope_str else []
    else:
        scopes = []

    # Fine-grained profile scopes
    fine_grained_profile_scopes = {
        "profile:personal:read",
        "profile:personal:write",
        "profile:preferences:read",
        "profile:preferences:write",
    }
    all_accepted = fine_grained_profile_scopes
    present_profile_scopes = [s for s in scopes if s in all_accepted]

    if not present_profile_scopes:
        error_msg = (
            f"Insufficient scopes for profile agent. Requires at least one of {sorted(all_accepted)}, got: {scopes}"
        )
        logger.warning(
            json.dumps(
                {
                    "event": "scope_validation_failed",
                    "agent": "customer_profile",
                    "required_any": sorted(all_accepted),
                    "actual": scopes,
                }
            )
        )
        return {"valid": False, "error": error_msg, "scopes": scopes}

    logger.info(
        json.dumps(
            {
                "event": "scope_validation_passed",
                "agent": "customer_profile",
                "profile_scopes": present_profile_scopes,
                "all_scopes": scopes,
            }
        )
    )

    # Warn about unexpected account scopes that don't belong to the profile agent
    unexpected_scopes = [s for s in scopes if s.startswith("accounts:")]
    if unexpected_scopes:
        logger.warning(
            json.dumps(
                {
                    "event": "unexpected_scopes_detected",
                    "agent": "customer_profile",
                    "unexpected_scopes": unexpected_scopes,
                    "all_scopes": scopes,
                }
            )
        )

    return {"valid": True, "scopes": scopes}


def validate_forwarded_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate JWT claims from the inbound token (exchanged or original).

    The coordinator performs RFC 8693 token exchange before invoking this agent,
    so the token typically contains attenuated scopes (only profile:* scopes).
    This validator ensures that:
    1. Required claims are present (sub, aud, exp, iss)
    2. The token was issued by the expected Auth0 domain OR the Token Exchange Service
    3. The audience matches the expected value
    4. Custom claims are present (customer_id)
    5. For exchanged tokens: delegation chain (`act` claim) and scopes are validated

    Args:
        claims: JWT claims from the inbound token (exchanged or original)

    Returns:
        Dict with "valid" boolean and optional "error" message
    """
    logger.info("Validating JWT claims for profile access")

    if not claims:
        logger.error("No claims provided in request")
        return {
            "valid": False,
            "error": "Missing authentication claims. Request must come through coordinator.",
        }

    # Check required standard claims
    required_claims = ["sub", "aud", "exp", "iss"]
    missing_claims = [claim for claim in required_claims if claim not in claims]

    if missing_claims:
        error_msg = f"Missing required claims: {missing_claims}"
        logger.warning(error_msg)
        return {"valid": False, "error": error_msg}

    # Validate issuer (accept Auth0 domain OR Token Exchange Service)
    issuer = claims.get("iss", "")
    if not issuer:
        return {"valid": False, "error": "Invalid issuer"}

    logger.info(f"Claims validated for issuer: {issuer}")

    # Detect and handle exchanged tokens from the Token Exchange Service
    if is_exchanged_token(claims):
        logger.info(
            json.dumps(
                {
                    "event": "exchanged_token_detected",
                    "agent": "customer_profile",
                    "issuer": issuer,
                    "exchange_id": claims.get("exchange_id"),
                    "delegation_chain": claims.get("act"),
                    "original_issuer": claims.get("original_issuer"),
                    "original_audience": claims.get("original_audience"),
                }
            )
        )

        # Validate scopes are appropriate for the profile agent
        scope_result = validate_scope_for_profile(claims)
        if not scope_result["valid"]:
            return {"valid": False, "error": scope_result["error"]}

    # Check for custom claims namespace
    claims_namespace = "https://agentcore.example.com/"
    customer_id_claim = f"{claims_namespace}customer_id"
    customer_number_claim = f"{claims_namespace}customer_number"

    customer_id = claims.get(customer_id_claim) or claims.get("customer_id")
    customer_number = claims.get(customer_number_claim)

    # For M2M tokens (client_credentials grant), customer_id may not be present.
    # -----------------------------------------------------------------------
    # WARNING: DEMO-ONLY FALLBACK
    # M2M tokens lack a human user context, so there is no customer_id claim.
    # For this sample application we fall back to a hardcoded demo customer so
    # that the end-to-end flow can be exercised without a real user login.
    #
    # PRODUCTION TODO: Do NOT fall back to a hardcoded customer_id.  Instead:
    #   1. Reject M2M tokens that lack a customer_id claim outright, OR
    #   2. Require the caller to pass an explicit customer_id parameter in the
    #      MCP tool request and validate it against an access-control policy.
    # -----------------------------------------------------------------------
    if not customer_id:
        sub = claims.get("sub", "")
        if "@clients" in sub:
            # DEMO ONLY — hardcoded fallback for M2M tokens (see warning above)
            customer_id = "CUST001"
            logger.warning(
                "DEMO FALLBACK: M2M token has no customer_id claim — "
                "falling back to hardcoded customer_id='CUST001'. "
                "This behaviour must NOT be used in production."
            )
        else:
            logger.warning("No customer_id in custom claims")
            return {"valid": False, "error": "Missing customer identity information"}

    logger.info(f"Validated customer_id={customer_id}, customer_number={customer_number}")

    return {
        "valid": True,
        "user_id": claims.get("sub"),
        "customer_id": customer_id,
        "customer_number": customer_number,
    }


def authorize_profile_access(claims: Dict[str, Any], requested_customer_id: str) -> bool:
    """
    Check if the authenticated user can access the requested profile.

    Users can only access their own profile data.

    Args:
        claims: Validated JWT claims
        requested_customer_id: The customer ID being accessed

    Returns:
        bool: True if access is authorized, False otherwise
    """
    # Get customer_id from claims (check both locations)
    claims_namespace = "https://agentcore.example.com/"
    customer_id = claims.get(f"{claims_namespace}customer_id") or claims.get("customer_id")

    if not customer_id:
        logger.error("No customer_id in claims")
        return False

    # Users can only access their own profile
    if customer_id != requested_customer_id:
        logger.warning(
            f"Authorization denied: customer_id={customer_id} attempted to access "
            f"profile for customer_id={requested_customer_id}"
        )
        return False

    logger.info(f"Profile access authorized for customer_id={customer_id}")
    return True


def get_audit_context(claims: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract audit context from claims for logging.

    Args:
        claims: Validated JWT claims

    Returns:
        Dict[str, Any]: Audit context information
    """
    claims_namespace = "https://agentcore.example.com/"
    return {
        "customer_id": claims.get(f"{claims_namespace}customer_id") or claims.get("customer_id"),
        "sub": claims.get("sub"),
        "email": claims.get("email"),
        "timestamp": datetime.utcnow().isoformat(),
    }
