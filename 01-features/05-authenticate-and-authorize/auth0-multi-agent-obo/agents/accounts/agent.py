# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Accounts Agent Implementation

Agent that returns customer-scoped account data with authorization logging.
"""

import logging
from typing import Any, Dict, Optional

from tools.account_tools import get_accounts, get_account_balance, get_account_details

logger = logging.getLogger(__name__)


class AccountsAgent:
    """
    Accounts Agent - Customer-Scoped Implementation

    This agent handles account-related queries and enforces authorization.
    Customers can only see their own accounts.

    Attributes:
        user_id: Auth0 user identifier (sub claim)
        customer_id: Financial institution customer identifier
    """

    def __init__(self, user_id: str, customer_id: Optional[str] = None):
        """
        Initialize the Accounts Agent.

        Args:
            user_id: Auth0 user identifier
            customer_id: Customer identifier from custom claims
        """
        self.user_id = user_id
        self.customer_id = customer_id or user_id

        logger.info(f"AccountsAgent initialized for user_id={user_id}, customer_id={self.customer_id}")

    def process_query(self, query: str, include_auth_details: bool = True) -> Dict[str, Any]:
        """
        Process an account-related query.

        Args:
            query: Natural language query about accounts
            include_auth_details: Include authorization details in response

        Returns:
            Response dict with account data and authorization info
        """
        logger.info(f"Processing accounts query: {query[:100]}")

        query_lower = query.lower()

        try:
            if "balance" in query_lower:
                # Extract account number if present
                account_number = self._extract_account_number(query)
                if account_number:
                    result = get_account_balance(
                        customer_id=self.customer_id,
                        account_number=account_number,
                        user_id=self.user_id,
                        include_auth_details=include_auth_details,
                    )
                else:
                    # Return all balances
                    accounts_data = get_accounts(
                        customer_id=self.customer_id,
                        user_id=self.user_id,
                        include_auth_details=include_auth_details,
                    )
                    result = {
                        "accounts": [
                            {
                                "account_number": acc["account_number"],
                                "account_name": acc["account_name"],
                                "balance": acc["balance"],
                                "available_balance": acc["available_balance"],
                                "currency": acc["currency"],
                            }
                            for acc in accounts_data.get("accounts", [])
                        ],
                        "authorization": accounts_data.get("authorization"),
                    }

            elif "detail" in query_lower or "information" in query_lower:
                account_number = self._extract_account_number(query)
                if account_number:
                    result = get_account_details(
                        customer_id=self.customer_id,
                        account_number=account_number,
                        user_id=self.user_id,
                        include_auth_details=include_auth_details,
                    )
                else:
                    result = {"error": "Please specify an account number"}

            elif "savings" in query_lower:
                # Filter for savings accounts
                accounts_data = get_accounts(
                    customer_id=self.customer_id,
                    user_id=self.user_id,
                    include_auth_details=include_auth_details,
                )
                savings_accounts = [
                    acc for acc in accounts_data.get("accounts", []) if acc["account_type"] == "savings"
                ]
                result = {
                    "accounts": savings_accounts,
                    "total_accounts": len(savings_accounts),
                    "authorization": accounts_data.get("authorization"),
                }

            elif "transaction" in query_lower or "checking" in query_lower:
                # Filter for transaction accounts
                accounts_data = get_accounts(
                    customer_id=self.customer_id,
                    user_id=self.user_id,
                    include_auth_details=include_auth_details,
                )
                transaction_accounts = [
                    acc for acc in accounts_data.get("accounts", []) if acc["account_type"] == "transaction"
                ]
                result = {
                    "accounts": transaction_accounts,
                    "total_accounts": len(transaction_accounts),
                    "authorization": accounts_data.get("authorization"),
                }

            elif "investment" in query_lower:
                # Filter for investment accounts
                accounts_data = get_accounts(
                    customer_id=self.customer_id,
                    user_id=self.user_id,
                    include_auth_details=include_auth_details,
                )
                investment_accounts = [
                    acc for acc in accounts_data.get("accounts", []) if acc["account_type"] == "investment"
                ]
                result = {
                    "accounts": investment_accounts,
                    "total_accounts": len(investment_accounts),
                    "authorization": accounts_data.get("authorization"),
                }

            else:
                # Default: list all accounts
                result = get_accounts(
                    customer_id=self.customer_id,
                    user_id=self.user_id,
                    include_auth_details=include_auth_details,
                )

            return {
                "status": "success",
                "data": result,
                "agent": "accounts_agent",
                "customer_id": self.customer_id,
            }

        except Exception as e:
            logger.error(f"Error processing accounts query: {str(e)}")
            return {
                "status": "error",
                "error": str(e),
                "message": "Failed to process accounts query",
            }

    def _extract_account_number(self, query: str) -> Optional[str]:
        """
        Extract account number from query.

        Args:
            query: User query

        Returns:
            Account number if found, None otherwise
        """
        import re

        patterns = [
            r"\b(\d{8})\b",  # 8-digit account number
            r"account\s+(?:number\s+)?(\d{8})",
            r"#(\d{8})",
        ]

        for pattern in patterns:
            match = re.search(pattern, query)
            if match:
                return match.group(1)

        return None
