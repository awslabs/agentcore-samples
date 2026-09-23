# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Authentication and Authorization Validator for Accounts Agent

Validates JWT claims from inbound tokens and checks account access authorization.
Supports dual-issuer validation: tokens from Auth0 (direct invocations) and tokens
from the AgentCore Token Exchange Service (RFC 8693 exchanged tokens from coordinator
with attenuated scopes -- only accounts:* scopes should be present).
"""

import json
import logging
from typing import Any, Dict

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


def validate_scope_for_accounts(claims: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate that the token scopes are appropriate for the accounts agent.

    Accepts fine-grained account-type scopes (accounts:savings:read, accounts:credit:read, etc.).
    Returns which account-type scopes are present so callers can filter by type.

    Args:
        claims: JWT claims dictionary

    Returns:
        Dict with "valid" boolean, "scopes" list, "account_type_scopes" list,
        and optional "error" message
    """
    scope_str = claims.get("scope", "")
    if isinstance(scope_str, list):
        scopes = scope_str
    elif isinstance(scope_str, str):
        scopes = scope_str.split() if scope_str else []
    else:
        scopes = []

    # Fine-grained account-type scopes
    fine_grained_account_scopes = {
        "accounts:savings:read",
        "accounts:savings:write",
        "accounts:transaction:read",
        "accounts:credit:read",
        "accounts:credit:write",
        "accounts:investment:read",
    }
    all_accepted = fine_grained_account_scopes
    present_account_scopes = [s for s in scopes if s in all_accepted]

    if not present_account_scopes:
        error_msg = (
            f"Insufficient scopes for accounts agent. Requires at least one of {sorted(all_accepted)}, got: {scopes}"
        )
        logger.warning(
            json.dumps(
                {
                    "event": "scope_validation_failed",
                    "agent": "accounts",
                    "required_any": sorted(all_accepted),
                    "actual": scopes,
                }
            )
        )
        return {"valid": False, "error": error_msg, "scopes": scopes}

    # Extract the fine-grained account-type scopes for filtering
    account_type_scopes = [s for s in scopes if s in fine_grained_account_scopes]

    logger.info(
        json.dumps(
            {
                "event": "scope_validation_passed",
                "agent": "accounts",
                "account_scopes": present_account_scopes,
                "account_type_scopes": account_type_scopes,
                "all_scopes": scopes,
            }
        )
    )

    # Warn about unexpected profile scopes that don't belong to the accounts agent
    unexpected_scopes = [s for s in scopes if s.startswith("profile:") or s in ("profile", "email")]
    if unexpected_scopes:
        logger.warning(
            json.dumps(
                {
                    "event": "unexpected_scopes_detected",
                    "agent": "accounts",
                    "unexpected_scopes": unexpected_scopes,
                    "all_scopes": scopes,
                }
            )
        )

    return {"valid": True, "scopes": scopes, "account_type_scopes": account_type_scopes}


def validate_forwarded_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate JWT claims from the inbound token (exchanged or original).

    The coordinator performs RFC 8693 token exchange before invoking this agent,
    so the token typically contains attenuated scopes (only accounts:* scopes).
    This validator ensures that:
    1. Required claims are present (sub, aud, exp, iss)
    2. The token was issued by the expected Auth0 domain OR the Token Exchange Service
    3. The audience matches the expected value
    4. Custom claims are present (customer_id, customer_number)
    5. For exchanged tokens: delegation chain (`act` claim) and scopes are validated

    Args:
        claims: JWT claims from the inbound token (exchanged or original)

    Returns:
        Dict with "valid" boolean and optional "error" message
    """
    logger.info("Validating JWT claims for accounts access")

    # Check required standard claims
    required_claims = ["sub", "aud", "exp", "iss"]
    missing_claims = [claim for claim in required_claims if claim not in claims]

    if missing_claims:
        error_msg = f"Missing required claims: {', '.join(missing_claims)}"
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
                    "agent": "accounts",
                    "issuer": issuer,
                    "exchange_id": claims.get("exchange_id"),
                    "delegation_chain": claims.get("act"),
                    "original_issuer": claims.get("original_issuer"),
                    "original_audience": claims.get("original_audience"),
                }
            )
        )

        # Validate scopes are appropriate for the accounts agent
        scope_result = validate_scope_for_accounts(claims)
        if not scope_result["valid"]:
            return {"valid": False, "error": scope_result["error"]}

    # Check for custom claims namespace
    claims_namespace = "https://agentcore.example.com/"
    customer_id_claim = f"{claims_namespace}customer_id"
    customer_number_claim = f"{claims_namespace}customer_number"

    customer_id = claims.get(customer_id_claim)
    customer_number = claims.get(customer_number_claim)

    if not customer_id:
        logger.warning("No customer_id in custom claims")
        return {"valid": False, "error": "Missing customer identity information"}

    logger.info(f"Validated customer_id={customer_id}, customer_number={customer_number}")

    return {
        "valid": True,
        "user_id": claims.get("sub"),
        "customer_id": customer_id,
        "customer_number": customer_number,
    }


def check_account_access(customer_id: str, account_number: str, claims: Dict[str, Any]) -> Dict[str, Any]:
    """
    Check if the customer has access to the specified account.

    This is a STUBBED implementation. In production, this would:
    1. Query the account ownership database
    2. Check account permissions (owner, joint, authorized user)
    3. Verify account status (active, closed, frozen)
    4. Apply business rules for account access

    Args:
        customer_id: Customer identifier
        account_number: Account number to check
        claims: JWT claims for additional context

    Returns:
        Dict with "authorized" boolean and optional "reason" message
    """
    logger.info(f"Checking account access: customer_id={customer_id}, account_number={account_number}")

    # STUBBED: In production, query account ownership
    # For now, authorize all accounts for demonstration

    # Mock authorization logic: accounts starting with "99" are unauthorized
    if account_number.startswith("99"):
        logger.warning(f"Access denied to account {account_number}")
        return {
            "authorized": False,
            "reason": "Customer does not have access to this account",
        }

    logger.info(f"Access granted to account {account_number}")
    return {
        "authorized": True,
        "access_level": "owner",  # Could be: owner, joint, authorized_user
    }
