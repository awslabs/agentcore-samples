# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Account Tools - Customer-Scoped Implementation

Tools that return account data scoped to the authenticated customer.
Demonstrates proper authorization patterns where customers can only
see their own accounts. Fine-grained scope-based filtering ensures
each exchanged token only reveals account types matching its scopes.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Mapping from account_type to the fine-grained read scope required
ACCOUNT_TYPE_SCOPE_MAP: Dict[str, str] = {
    "savings": "accounts:savings:read",
    "transaction": "accounts:transaction:read",
    "credit": "accounts:credit:read",
    "investment": "accounts:investment:read",
}

# =============================================================================
# Mock Account Database
# Maps customer_id to their accounts. In production, this would be a database.
# =============================================================================

CUSTOMER_ACCOUNTS_DB: Dict[str, List[Dict[str, Any]]] = {
    # Demo customer - John Doe (Auth0 test user)
    "CUST-001": [
        {
            "account_id": "ACC-001-TXN",
            "account_number": "12345678",
            "account_name": "Everyday Transaction",
            "account_type": "transaction",
            "bsb": "123-456",
            "currency": "AUD",
            "balance": 15_432.50,
            "available_balance": 14_932.50,
            "pending_transactions": -500.00,
            "status": "active",
            "opened_date": "2022-03-15",
            "features": {
                "overdraft_limit": 2_000.00,
                "interest_rate": 0.01,
                "monthly_fee": 5.00,
                "fee_waived": True,
                "atm_withdrawals_included": 5,
            },
        },
        {
            "account_id": "ACC-001-SAV",
            "account_number": "12345679",
            "account_name": "High Interest Savings",
            "account_type": "savings",
            "bsb": "123-456",
            "currency": "AUD",
            "balance": 48_750.00,
            "available_balance": 48_750.00,
            "pending_transactions": 0.00,
            "interest_accrued": 89.45,
            "status": "active",
            "opened_date": "2022-03-15",
            "features": {
                "interest_rate": 4.5,
                "bonus_interest_rate": 0.5,
                "bonus_conditions": "Deposit $1000+ monthly, no withdrawals",
                "monthly_fee": 0.00,
            },
        },
        {
            "account_id": "ACC-001-INV",
            "account_number": "12345680",
            "account_name": "Managed Investment",
            "account_type": "investment",
            "currency": "AUD",
            "balance": 125_000.00,
            "available_balance": 125_000.00,
            "market_value": 127_450.00,
            "unrealized_gain": 2_450.00,
            "status": "active",
            "opened_date": "2023-06-01",
            "features": {
                "portfolio_type": "Balanced Growth",
                "management_fee_percent": 0.85,
                "risk_profile": "moderate",
            },
        },
        {
            "account_id": "ACC-001-CC",
            "account_number": "12345681",
            "account_name": "Platinum Rewards Credit Card",
            "account_type": "credit",
            "currency": "AUD",
            "balance": -2_340.75,
            "available_balance": 17_659.25,
            "credit_limit": 20_000.00,
            "current_balance": 2_340.75,
            "minimum_payment": 50.00,
            "due_date": "2026-03-15",
            "pending_transactions": -125.50,
            "status": "active",
            "opened_date": "2022-09-01",
            "features": {
                "interest_rate": 19.99,
                "annual_fee": 149.00,
                "rewards_program": "Platinum Rewards",
                "points_balance": 42_850,
                "contactless_enabled": True,
            },
        },
    ],
    # Demo customer - Jane Smith
    "CUST-002": [
        {
            "account_id": "ACC-002-TXN",
            "account_number": "22345678",
            "account_name": "Premium Transaction",
            "account_type": "transaction",
            "bsb": "123-456",
            "currency": "AUD",
            "balance": 8_250.75,
            "available_balance": 8_250.75,
            "pending_transactions": 0.00,
            "status": "active",
            "opened_date": "2021-08-20",
            "features": {
                "overdraft_limit": 5_000.00,
                "interest_rate": 0.02,
                "monthly_fee": 0.00,
                "premium_benefits": True,
            },
        },
        {
            "account_id": "ACC-002-SAV",
            "account_number": "22345679",
            "account_name": "Goal Saver",
            "account_type": "savings",
            "bsb": "123-456",
            "currency": "AUD",
            "balance": 15_000.00,
            "available_balance": 15_000.00,
            "pending_transactions": 0.00,
            "interest_accrued": 45.20,
            "status": "active",
            "opened_date": "2022-01-10",
            "savings_goal": 20_000.00,
            "features": {"interest_rate": 3.5, "monthly_fee": 0.00},
        },
        {
            "account_id": "ACC-002-CC",
            "account_number": "22345680",
            "account_name": "Essentials Credit Card",
            "account_type": "credit",
            "currency": "AUD",
            "balance": -890.30,
            "available_balance": 4_109.70,
            "credit_limit": 5_000.00,
            "current_balance": 890.30,
            "minimum_payment": 25.00,
            "due_date": "2026-03-20",
            "pending_transactions": 0.00,
            "status": "active",
            "opened_date": "2023-03-15",
            "features": {
                "interest_rate": 21.49,
                "annual_fee": 0.00,
                "rewards_program": None,
                "contactless_enabled": True,
            },
        },
    ],
    # Demo customer - Bob Wilson (restricted account example)
    "CUST-003": [
        {
            "account_id": "ACC-003-TXN",
            "account_number": "32345678",
            "account_name": "Basic Transaction",
            "account_type": "transaction",
            "bsb": "123-456",
            "currency": "AUD",
            "balance": 1_234.56,
            "available_balance": 1_234.56,
            "pending_transactions": 0.00,
            "status": "active",
            "opened_date": "2023-11-01",
            "features": {
                "overdraft_limit": 0.00,
                "interest_rate": 0.00,
                "monthly_fee": 4.00,
            },
        }
    ],
}

# Map Auth0 user IDs to customer IDs (in production, this would be in a database)
USER_TO_CUSTOMER_MAP: Dict[str, str] = {
    # These would be populated from Auth0 custom claims or a user mapping table
    # Format: "auth0|user_id": "CUST-XXX"
}


def _filter_accounts_by_scopes(
    accounts: List[Dict[str, Any]],
    scopes: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Filter accounts based on fine-grained scopes in the exchanged token.

    Only accounts matching the fine-grained account-type scopes
    (accounts:savings:read, etc.) in the token are returned.

    Args:
        accounts: List of account dicts to filter
        scopes: Scopes from the token (None means no filtering)

    Returns:
        Filtered list of accounts
    """
    if not scopes:
        return accounts

    scope_set = set(scopes)

    # Check if any fine-grained account-type scopes are present
    has_fine_grained = any(s in scope_set for s in ACCOUNT_TYPE_SCOPE_MAP.values())

    if not has_fine_grained:
        # No recognised account-type scopes — return empty
        return []

    # Filter to only account types the token has scopes for
    allowed_types: Set[str] = set()
    for account_type, required_scope in ACCOUNT_TYPE_SCOPE_MAP.items():
        if required_scope in scope_set:
            allowed_types.add(account_type)

    filtered = [acc for acc in accounts if acc["account_type"] in allowed_types]

    logger.info(
        json.dumps(
            {
                "event": "scope_based_account_filtering",
                "total_accounts": len(accounts),
                "filtered_accounts": len(filtered),
                "allowed_types": sorted(allowed_types),
                "removed_count": len(accounts) - len(filtered),
            }
        )
    )

    return filtered


def _map_customer_id_to_mock(customer_id: str) -> str:
    """
    Map an incoming customer_id to a known mock data ID.

    In production, this would be a direct database lookup.
    For demo purposes, unknown customer IDs are mapped to CUST-001
    so that any authenticated user sees sample account data.
    """
    if customer_id in CUSTOMER_ACCOUNTS_DB:
        return customer_id
    logger.info(f"[MOCK] Mapping unknown customer_id={customer_id} to CUST-001 for demo")
    return "CUST-001"


def _get_customer_id_for_user(user_id: str, provided_customer_id: Optional[str] = None) -> str:
    """
    Resolve the customer_id for a given user.

    In production, this would query a database or use Auth0 custom claims.
    For demo purposes, we use the provided customer_id or derive from user_id,
    then map to a known mock customer ID.
    """
    if provided_customer_id:
        return _map_customer_id_to_mock(provided_customer_id)

    # Check mapping table
    if user_id in USER_TO_CUSTOMER_MAP:
        return _map_customer_id_to_mock(USER_TO_CUSTOMER_MAP[user_id])

    # Default to CUST-001 for demo purposes
    return "CUST-001"


def _verify_account_ownership(customer_id: str, account_number: str) -> Dict[str, Any]:
    """
    Verify that the customer owns the specified account.

    Returns authorization decision with details for audit logging.
    """
    customer_accounts = CUSTOMER_ACCOUNTS_DB.get(customer_id, [])

    for account in customer_accounts:
        if account["account_number"] == account_number:
            return {
                "authorized": True,
                "reason": "Customer is account owner",
                "access_level": "owner",
                "account_id": account["account_id"],
            }

    # Check if account exists but belongs to another customer
    for other_customer_id, accounts in CUSTOMER_ACCOUNTS_DB.items():
        for account in accounts:
            if account["account_number"] == account_number:
                logger.warning(
                    f"AUTHORIZATION DENIED: customer_id={customer_id} attempted to access "
                    f"account {account_number} belonging to {other_customer_id}"
                )
                return {
                    "authorized": False,
                    "reason": "Account belongs to another customer",
                    "access_level": None,
                    "audit_event": "unauthorized_access_attempt",
                }

    return {"authorized": False, "reason": "Account not found", "access_level": None}


def get_accounts(
    customer_id: str,
    user_id: Optional[str] = None,
    include_auth_details: bool = False,
    scopes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Get all accounts for a customer, filtered by token scopes.

    This function enforces customer-scoped authorization - customers can
    only see their own accounts. When fine-grained scopes are present
    (e.g., accounts:savings:read), only matching account types are returned.

    Args:
        customer_id: Customer identifier (from JWT claims)
        user_id: Auth0 user ID for audit logging
        include_auth_details: Include authorization details in response
        scopes: Token scopes for fine-grained account-type filtering

    Returns:
        Dict containing list of accounts with authorization info
    """
    # Map to known mock customer for demo
    customer_id = _map_customer_id_to_mock(customer_id)

    logger.info(f"[AUTH] Getting accounts for customer_id={customer_id}, user_id={user_id}")

    # Authorization check
    auth_decision = {
        "action": "list_accounts",
        "customer_id": customer_id,
        "user_id": user_id,
        "timestamp": datetime.utcnow().isoformat(),
        "authorized": True,
        "reason": "Customer requesting own accounts",
    }

    # Get accounts for this customer only
    customer_accounts = CUSTOMER_ACCOUNTS_DB.get(customer_id, [])

    # Apply scope-based filtering
    customer_accounts = _filter_accounts_by_scopes(customer_accounts, scopes)

    if not customer_accounts:
        logger.info(f"[AUTH] No accounts found for customer_id={customer_id}")
        auth_decision["result"] = "no_accounts_found"
    else:
        logger.info(f"[AUTH] AUTHORIZED: Retrieved {len(customer_accounts)} accounts for customer_id={customer_id}")
        auth_decision["result"] = f"returned_{len(customer_accounts)}_accounts"

    # Build response with account summaries (not full details)
    account_summaries = [
        {
            "account_number": acc["account_number"],
            "account_name": acc["account_name"],
            "account_type": acc["account_type"],
            "currency": acc["currency"],
            "balance": acc["balance"],
            "available_balance": acc["available_balance"],
            "status": acc["status"],
        }
        for acc in customer_accounts
    ]

    response = {
        "customer_id": customer_id,
        "accounts": account_summaries,
        "total_accounts": len(account_summaries),
        "timestamp": datetime.utcnow().isoformat(),
    }

    if include_auth_details:
        response["authorization"] = auth_decision

    return response


def get_account_balance(
    customer_id: str,
    account_number: str,
    user_id: Optional[str] = None,
    include_auth_details: bool = False,
) -> Dict[str, Any]:
    """
    Get balance information for a specific account.

    Enforces authorization - customer must own the account.

    Args:
        customer_id: Customer identifier
        account_number: Account number
        user_id: Auth0 user ID for audit logging
        include_auth_details: Include authorization details in response

    Returns:
        Dict containing balance information or authorization error
    """
    # Map to known mock customer for demo
    customer_id = _map_customer_id_to_mock(customer_id)

    logger.info(f"[AUTH] Getting balance for customer_id={customer_id}, account_number={account_number}")

    # Authorization check
    auth_result = _verify_account_ownership(customer_id, account_number)

    auth_decision = {
        "action": "get_account_balance",
        "customer_id": customer_id,
        "user_id": user_id,
        "account_number": account_number,
        "timestamp": datetime.utcnow().isoformat(),
        **auth_result,
    }

    if not auth_result["authorized"]:
        logger.warning(
            f"[AUTH] DENIED: customer_id={customer_id} cannot access account {account_number} - {auth_result['reason']}"
        )
        response = {
            "error": "AUTHORIZATION_DENIED",
            "message": f"You do not have access to account {account_number}",
            "reason": auth_result["reason"],
        }
        if include_auth_details:
            response["authorization"] = auth_decision
        return response

    logger.info(f"[AUTH] AUTHORIZED: customer_id={customer_id} accessing account {account_number}")

    # Get the account data
    customer_accounts = CUSTOMER_ACCOUNTS_DB.get(customer_id, [])
    account = next(
        (acc for acc in customer_accounts if acc["account_number"] == account_number),
        None,
    )

    if not account:
        return {"error": "Account not found"}

    response = {
        "customer_id": customer_id,
        "account_number": account_number,
        "account_name": account["account_name"],
        "account_type": account["account_type"],
        "currency": account["currency"],
        "current_balance": account["balance"],
        "available_balance": account["available_balance"],
        "pending_transactions": account.get("pending_transactions", 0.00),
        "as_of_date": datetime.utcnow().isoformat(),
    }

    # Add type-specific balance info
    if account["account_type"] == "savings":
        response["interest_accrued"] = account.get("interest_accrued", 0.00)
    elif account["account_type"] == "investment":
        response["market_value"] = account.get("market_value", account["balance"])
        response["unrealized_gain"] = account.get("unrealized_gain", 0.00)
    elif account["account_type"] == "credit":
        response["credit_limit"] = account.get("credit_limit", 0.00)
        response["current_balance"] = account.get("current_balance", 0.00)
        response["minimum_payment"] = account.get("minimum_payment", 0.00)
        response["due_date"] = account.get("due_date", "")

    if include_auth_details:
        response["authorization"] = auth_decision

    return response


def get_account_details(
    customer_id: str,
    account_number: str,
    user_id: Optional[str] = None,
    include_auth_details: bool = False,
) -> Dict[str, Any]:
    """
    Get detailed information for a specific account.

    Enforces authorization - customer must own the account.

    Args:
        customer_id: Customer identifier
        account_number: Account number
        user_id: Auth0 user ID for audit logging
        include_auth_details: Include authorization details in response

    Returns:
        Dict containing detailed account information or authorization error
    """
    # Map to known mock customer for demo
    customer_id = _map_customer_id_to_mock(customer_id)

    logger.info(f"[AUTH] Getting account details for customer_id={customer_id}, account_number={account_number}")

    # Authorization check
    auth_result = _verify_account_ownership(customer_id, account_number)

    auth_decision = {
        "action": "get_account_details",
        "customer_id": customer_id,
        "user_id": user_id,
        "account_number": account_number,
        "timestamp": datetime.utcnow().isoformat(),
        **auth_result,
    }

    if not auth_result["authorized"]:
        logger.warning(
            f"[AUTH] DENIED: customer_id={customer_id} cannot access "
            f"details for account {account_number} - {auth_result['reason']}"
        )
        response = {
            "error": "AUTHORIZATION_DENIED",
            "message": f"You do not have access to account {account_number}",
            "reason": auth_result["reason"],
        }
        if include_auth_details:
            response["authorization"] = auth_decision
        return response

    logger.info(f"[AUTH] AUTHORIZED: customer_id={customer_id} accessing details for account {account_number}")

    # Get the full account data
    customer_accounts = CUSTOMER_ACCOUNTS_DB.get(customer_id, [])
    account = next(
        (acc for acc in customer_accounts if acc["account_number"] == account_number),
        None,
    )

    if not account:
        return {"error": "Account not found"}

    # Return full account details including features
    response = {
        "customer_id": customer_id,
        **account,
        "ownership": {
            "type": "individual",
            "primary_owner": customer_id,
            "access_level": auth_result["access_level"],
        },
        "timestamp": datetime.utcnow().isoformat(),
    }

    if include_auth_details:
        response["authorization"] = auth_decision

    return response


def check_account_access(customer_id: str, account_number: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Check if a customer has access to a specific account.

    This is useful for pre-authorization checks before performing operations.

    Args:
        customer_id: Customer identifier
        account_number: Account number to check
        user_id: Auth0 user ID for audit logging

    Returns:
        Dict with authorization decision and details
    """
    logger.info(f"[AUTH CHECK] Checking access: customer_id={customer_id}, account_number={account_number}")

    auth_result = _verify_account_ownership(customer_id, account_number)

    return {
        "customer_id": customer_id,
        "account_number": account_number,
        "user_id": user_id,
        "timestamp": datetime.utcnow().isoformat(),
        **auth_result,
    }
