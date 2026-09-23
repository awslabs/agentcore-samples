# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for Customer Profile Agent class.
"""

import sys
import os
from unittest.mock import patch

# Add agents directory to path and clear cached modules for clean import
_profile_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents", "customer_profile")
sys.path.insert(0, _profile_path)

# Clear any cached agent/profile_service modules to ensure we load the right one
for mod_name in list(sys.modules.keys()):
    if (
        mod_name in ("agent", "profile_service")
        or mod_name.startswith("agent.")
        or mod_name.startswith("profile_service.")
    ):
        del sys.modules[mod_name]

import agent as _agent_module
from agent import CustomerProfileAgent, create_agent


class TestCustomerProfileAgentInit:
    """Tests for CustomerProfileAgent initialization."""

    def test_init_with_user_id_only(self):
        """Test initialization with only user_id."""
        agent = CustomerProfileAgent(user_id="user-123")

        assert agent.user_id == "user-123"
        assert agent.customer_id == "user-123"  # Defaults to user_id

    def test_init_with_user_id_and_customer_id(self):
        """Test initialization with both user_id and customer_id."""
        agent = CustomerProfileAgent(user_id="user-123", customer_id="CUST-456")

        assert agent.user_id == "user-123"
        assert agent.customer_id == "CUST-456"

    def test_init_with_none_user_id(self):
        """Test initialization with None user_id."""
        agent = CustomerProfileAgent(user_id=None, customer_id="CUST-456")

        assert agent.user_id is None
        assert agent.customer_id == "CUST-456"


class TestCustomerProfileAgentMapCustomerId:
    """Tests for _map_customer_id method."""

    def test_maps_known_customer_ids(self):
        """Test that known customer IDs are preserved."""
        for cust_id in ["CUST001", "CUST002", "CUST003"]:
            agent = CustomerProfileAgent(user_id="user-123", customer_id=cust_id)
            assert agent._map_customer_id() == cust_id

    def test_maps_unknown_to_default(self):
        """Test that unknown customer IDs map to default."""
        agent = CustomerProfileAgent(user_id="user-123", customer_id="UNKNOWN-999")

        assert agent._map_customer_id() == "CUST001"

    def test_maps_none_to_default(self):
        """Test that None customer_id maps to default."""
        agent = CustomerProfileAgent(user_id="user-123", customer_id=None)

        assert agent._map_customer_id() == "CUST001"


class TestCustomerProfileAgentProcessQuery:
    """Tests for process_query method."""

    @patch.object(_agent_module, "profile_service")
    def test_profile_query_keywords(self, mock_profile_service):
        """Test that profile keywords trigger profile retrieval."""
        mock_profile_service.get_profile.return_value = {
            "title": "Mr",
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "primary_phone": "+1234567890",
            "customer_since": "2020-01-01",
            "address": {
                "street_line_1": "123 Main St",
                "suburb": "Sydney",
                "state": "NSW",
                "postcode": "2000",
                "country": "Australia",
            },
            "preferred_contact_method": "email",
        }

        agent = CustomerProfileAgent(user_id="user-123", customer_id="CUST001")

        for keyword in ["show my profile", "view profile", "profile please"]:
            result = agent.process_query(keyword)
            assert result["status"] == "success"
            assert "response" in result

    @patch.object(_agent_module, "profile_service")
    def test_address_query(self, mock_profile_service):
        """Test that address keyword triggers address retrieval."""
        mock_profile_service.get_profile.return_value = {
            "address": {
                "street_line_1": "123 Main St",
                "suburb": "Sydney",
                "state": "NSW",
                "postcode": "2000",
                "country": "Australia",
            }
        }

        agent = CustomerProfileAgent(user_id="user-123", customer_id="CUST001")
        result = agent.process_query("what is my address?")

        assert result["status"] == "success"
        assert "Residential Address" in result["response"]

    @patch.object(_agent_module, "profile_service")
    def test_phone_query(self, mock_profile_service):
        """Test that phone keyword triggers phone info retrieval."""
        mock_profile_service.get_profile.return_value = {
            "primary_phone": "+1234567890",
            "secondary_phone": "+0987654321",
        }

        agent = CustomerProfileAgent(user_id="user-123", customer_id="CUST001")
        result = agent.process_query("what is my phone number?")

        assert result["status"] == "success"
        assert "Contact Numbers" in result["response"]

    @patch.object(_agent_module, "profile_service")
    def test_preferences_query(self, mock_profile_service):
        """Test that preference keyword triggers preferences retrieval."""
        mock_profile_service.get_profile.return_value = {
            "preferred_contact_method": "email",
            "marketing_preferences": {
                "email_opt_in": True,
                "sms_opt_in": False,
                "mail_opt_in": True,
            },
        }

        agent = CustomerProfileAgent(user_id="user-123", customer_id="CUST001")
        result = agent.process_query("show my preferences")

        assert result["status"] == "success"
        # Response includes preferred contact method
        assert "email" in result["response"].lower() or "Preferred Contact" in result["response"]

    @patch.object(_agent_module, "profile_service")
    def test_profile_not_found(self, mock_profile_service):
        """Test handling when profile is not found."""
        mock_profile_service.get_profile.return_value = None

        agent = CustomerProfileAgent(user_id="user-123", customer_id="CUST001")
        result = agent.process_query("show my profile")

        assert result["status"] == "error"
        assert result["error"] == "PROFILE_NOT_FOUND"

    @patch.object(_agent_module, "profile_service")
    def test_exception_handling(self, mock_profile_service):
        """Test that exceptions are caught and returned as errors."""
        mock_profile_service.get_profile.side_effect = Exception("Database error")

        agent = CustomerProfileAgent(user_id="user-123", customer_id="CUST001")
        result = agent.process_query("show my profile")

        assert result["status"] == "error"
        assert "Database error" in result["error"]

    @patch.object(_agent_module, "profile_service")
    def test_default_query_shows_profile(self, mock_profile_service):
        """Test that unrecognized queries default to showing profile."""
        mock_profile_service.get_profile.return_value = {
            "title": "Mr",
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "address": {},
        }

        agent = CustomerProfileAgent(user_id="user-123", customer_id="CUST001")
        result = agent.process_query("random query with no keywords")

        assert result["status"] == "success"
        # Default behavior is to show profile


class TestCreateAgent:
    """Tests for create_agent factory function."""

    def test_creates_agent_with_params(self):
        """Test factory function creates agent with parameters."""
        agent = create_agent(user_id="user-123", customer_id="CUST-456")

        assert isinstance(agent, CustomerProfileAgent)
        assert agent.user_id == "user-123"
        assert agent.customer_id == "CUST-456"

    def test_creates_agent_with_defaults(self):
        """Test factory function creates agent with default None values."""
        agent = create_agent()

        assert isinstance(agent, CustomerProfileAgent)
        assert agent.user_id is None
