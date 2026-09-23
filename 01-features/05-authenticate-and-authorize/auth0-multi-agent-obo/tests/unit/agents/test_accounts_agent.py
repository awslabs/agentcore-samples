# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for Accounts Agent class.
"""

import sys
import os
import importlib.util
from unittest.mock import patch


# Load module from specific path to avoid conflicts with other agent modules
def load_module_from_path(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_accounts_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents", "accounts")
sys.path.insert(0, _accounts_path)

# Load account tools first so agent can import them
_tools_path = os.path.join(_accounts_path, "tools", "account_tools.py")
_account_tools = load_module_from_path("account_tools", _tools_path)

# Load agent module
_agent_path = os.path.join(_accounts_path, "agent.py")
_agent_module = load_module_from_path("accounts_agent_module", _agent_path)

AccountsAgent = _agent_module.AccountsAgent
get_accounts = _account_tools.get_accounts
get_account_balance = _account_tools.get_account_balance
get_account_details = _account_tools.get_account_details


class TestAccountsAgentInit:
    """Tests for AccountsAgent initialization."""

    def test_init_with_user_id_only(self):
        """Test initialization with only user_id."""
        agent = AccountsAgent(user_id="user-123")

        assert agent.user_id == "user-123"
        assert agent.customer_id == "user-123"  # Defaults to user_id

    def test_init_with_user_id_and_customer_id(self):
        """Test initialization with both user_id and customer_id."""
        agent = AccountsAgent(user_id="user-123", customer_id="CUST-456")

        assert agent.user_id == "user-123"
        assert agent.customer_id == "CUST-456"

    def test_init_with_none_customer_id(self):
        """Test initialization with None customer_id defaults to user_id."""
        agent = AccountsAgent(user_id="user-123", customer_id=None)

        assert agent.customer_id == "user-123"


class TestAccountsAgentProcessQuery:
    """Tests for process_query method."""

    @patch("accounts_agent_module.get_accounts")
    def test_list_accounts_query(self, mock_get_accounts):
        """Test listing all accounts."""
        mock_get_accounts.return_value = {
            "accounts": [
                {
                    "account_number": "12345678",
                    "account_name": "Savings",
                    "balance": 1000.00,
                    "available_balance": 950.00,
                    "currency": "AUD",
                    "account_type": "savings",
                }
            ],
            "authorization": {"authorized": True},
        }

        agent = AccountsAgent(user_id="user-123", customer_id="CUST001")
        result = agent.process_query("show my accounts")

        assert result["status"] == "success"
        mock_get_accounts.assert_called_once()

    @patch("accounts_agent_module.get_account_balance")
    def test_balance_query_with_account(self, mock_get_balance):
        """Test balance query for specific account."""
        mock_get_balance.return_value = {
            "account_number": "12345678",
            "balance": 1000.00,
            "available_balance": 950.00,
            "currency": "AUD",
        }

        agent = AccountsAgent(user_id="user-123", customer_id="CUST001")
        result = agent.process_query("what is the balance of account 12345678?")

        assert result["status"] == "success"
        mock_get_balance.assert_called_once()

    @patch("accounts_agent_module.get_accounts")
    def test_balance_query_without_account(self, mock_get_accounts):
        """Test balance query without specific account shows all."""
        mock_get_accounts.return_value = {
            "accounts": [
                {
                    "account_number": "12345678",
                    "account_name": "Savings",
                    "balance": 1000.00,
                    "available_balance": 950.00,
                    "currency": "AUD",
                }
            ]
        }

        agent = AccountsAgent(user_id="user-123", customer_id="CUST001")
        result = agent.process_query("show me my balance")

        assert result["status"] == "success"

    @patch("accounts_agent_module.get_account_details")
    def test_account_details_query(self, mock_get_details):
        """Test account details query."""
        mock_get_details.return_value = {
            "account_number": "12345678",
            "account_name": "Savings Account",
            "account_type": "savings",
            "balance": 1000.00,
            "status": "active",
        }

        agent = AccountsAgent(user_id="user-123", customer_id="CUST001")
        result = agent.process_query("show details for account 12345678")

        assert result["status"] == "success"

    @patch("accounts_agent_module.get_accounts")
    def test_exception_handling(self, mock_get_accounts):
        """Test that exceptions are caught and returned as errors."""
        mock_get_accounts.side_effect = Exception("Database error")

        agent = AccountsAgent(user_id="user-123", customer_id="CUST001")
        result = agent.process_query("show my accounts")

        assert result["status"] == "error"
        assert "error" in result

    @patch("accounts_agent_module.get_accounts")
    def test_passes_customer_id_to_tools(self, mock_get_accounts):
        """Test that customer_id is passed to account tools."""
        mock_get_accounts.return_value = {"accounts": []}

        agent = AccountsAgent(user_id="user-123", customer_id="CUST-SPECIFIC")
        agent.process_query("show my accounts")

        # Verify customer_id was passed
        call_kwargs = mock_get_accounts.call_args[1]
        assert call_kwargs["customer_id"] == "CUST-SPECIFIC"


class TestAccountsAgentExtractAccountNumber:
    """Tests for _extract_account_number method."""

    def test_extracts_8_digit_number(self):
        """Test extraction of 8-digit account number."""
        agent = AccountsAgent(user_id="user-123")

        query = "show balance for account 12345678"
        result = agent._extract_account_number(query)

        assert result == "12345678"

    def test_extracts_from_hash_prefix(self):
        """Test extraction with # prefix."""
        agent = AccountsAgent(user_id="user-123")

        query = "details of #12345678"
        result = agent._extract_account_number(query)

        assert result == "12345678"

    def test_returns_none_for_no_account(self):
        """Test returns None when no account number found."""
        agent = AccountsAgent(user_id="user-123")

        query = "show all my accounts"
        result = agent._extract_account_number(query)

        assert result is None

    def test_extracts_from_account_keyword(self):
        """Test extraction after 'account' keyword."""
        agent = AccountsAgent(user_id="user-123")

        query = "what is account 87654321 balance"
        result = agent._extract_account_number(query)

        assert result == "87654321"
