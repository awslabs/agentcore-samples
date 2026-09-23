import json
from unittest.mock import Mock, patch

import pytest

from sre_agent.memory.client import SREMemoryClient
from sre_agent.memory.strategies import (
    InfrastructureKnowledge,
    InvestigationSummary,
    UserPreference,
)
from sre_agent.memory.tools import (
    RetrieveMemoryTool,
    SaveInfrastructureTool,
    SaveInvestigationTool,
    SavePreferenceTool,
)


class TestSavePreferenceTool:
    """Tests for SavePreferenceTool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock memory client."""
        mock = Mock(spec=SREMemoryClient)
        return mock

    @pytest.fixture
    def save_preference_tool(self, mock_client):
        """Create SavePreferenceTool with mock client."""
        return SavePreferenceTool(mock_client)

    def test_save_preference_success(self, save_preference_tool, mock_client):
        """Test saving user preference successfully."""
        with patch("sre_agent.memory.tools._save_user_preference") as mock_save:
            mock_save.return_value = True

            preference = UserPreference(
                user_id="user123",
                preference_type="escalation",
                preference_value={"contact": "ops@company.com"},
            )

            result = save_preference_tool._run(
                content=preference, context="test context", actor_id="sre-agent"
            )

            assert "Saved user preference: escalation for user user123" in result
            mock_save.assert_called_once()

    def test_save_preference_failure(self, save_preference_tool, mock_client):
        """Test saving user preference failure."""
        with patch("sre_agent.memory.tools._save_user_preference") as mock_save:
            mock_save.return_value = False

            preference = UserPreference(
                user_id="user123",
                preference_type="escalation",
                preference_value={"contact": "ops@company.com"},
            )

            result = save_preference_tool._run(
                content=preference, context=None, actor_id="sre-agent"
            )

            assert "Failed to save user preference: escalation" in result


class TestSaveInfrastructureTool:
    """Tests for SaveInfrastructureTool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock memory client."""
        mock = Mock(spec=SREMemoryClient)
        return mock

    @pytest.fixture
    def save_infrastructure_tool(self, mock_client):
        """Create SaveInfrastructureTool with mock client."""
        return SaveInfrastructureTool(mock_client)

    def test_save_infrastructure_success(self, save_infrastructure_tool, mock_client):
        """Test saving infrastructure knowledge successfully."""
        with patch(
            "sre_agent.memory.tools._save_infrastructure_knowledge"
        ) as mock_save:
            mock_save.return_value = True

            knowledge = InfrastructureKnowledge(
                service_name="web-service",
                knowledge_type="dependency",
                knowledge_data={"depends_on": "database"},
            )

            result = save_infrastructure_tool._run(
                content=knowledge, context="test context", actor_id="sre-agent"
            )

            assert (
                "Saved infrastructure knowledge: dependency for web-service" in result
            )
            mock_save.assert_called_once()

    def test_save_infrastructure_failure(self, save_infrastructure_tool, mock_client):
        """Test saving infrastructure knowledge failure."""
        with patch(
            "sre_agent.memory.tools._save_infrastructure_knowledge"
        ) as mock_save:
            mock_save.return_value = False

            knowledge = InfrastructureKnowledge(
                service_name="web-service",
                knowledge_type="dependency",
                knowledge_data={"depends_on": "database"},
            )

            result = save_infrastructure_tool._run(
                content=knowledge, context=None, actor_id="sre-agent"
            )

            assert "Failed to save infrastructure knowledge for web-service" in result


class TestSaveInvestigationTool:
    """Tests for SaveInvestigationTool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock memory client."""
        mock = Mock(spec=SREMemoryClient)
        return mock

    @pytest.fixture
    def save_investigation_tool(self, mock_client):
        """Create SaveInvestigationTool with mock client."""
        return SaveInvestigationTool(mock_client)

    def test_save_investigation_success(self, save_investigation_tool, mock_client):
        """Test saving investigation summary successfully."""
        with patch("sre_agent.memory.tools._save_investigation_summary") as mock_save:
            mock_save.return_value = True

            summary = InvestigationSummary(
                incident_id="incident_123",
                query="Why is service down?",
                resolution_status="completed",
            )

            result = save_investigation_tool._run(
                content=summary, context="test context", actor_id="sre-agent"
            )

            assert "Saved investigation summary for incident incident_123" in result
            mock_save.assert_called_once()

    def test_save_investigation_failure(self, save_investigation_tool, mock_client):
        """Test saving investigation summary failure."""
        with patch("sre_agent.memory.tools._save_investigation_summary") as mock_save:
            mock_save.return_value = False

            summary = InvestigationSummary(
                incident_id="incident_123",
                query="Why is service down?",
                resolution_status="completed",
            )

            result = save_investigation_tool._run(
                content=summary, context=None, actor_id="sre-agent"
            )

            assert "Failed to save investigation summary for incident_123" in result


class TestRetrieveMemoryTool:
    """Tests for RetrieveMemoryTool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock memory client."""
        mock = Mock(spec=SREMemoryClient)
        return mock

    @pytest.fixture
    def retrieve_tool(self, mock_client):
        """Create RetrieveMemoryTool with mock client."""
        return RetrieveMemoryTool(mock_client)

    def test_retrieve_preferences_success(self, retrieve_tool, mock_client):
        """Test retrieving user preferences successfully."""
        mock_preferences = [
            Mock(
                model_dump=Mock(
                    return_value={
                        "user_id": "user123",
                        "preference_type": "escalation",
                        "preference_value": {"contact": "ops@company.com"},
                    }
                )
            )
        ]

        with patch(
            "sre_agent.memory.tools._retrieve_user_preferences"
        ) as mock_retrieve:
            mock_retrieve.return_value = mock_preferences

            result = retrieve_tool._run(
                memory_type="preference",
                query="escalation contacts",
                actor_id="user123",
            )

            result_data = json.loads(result)
            assert len(result_data) == 1
            assert result_data[0]["user_id"] == "user123"
            mock_retrieve.assert_called_once_with(
                mock_client, "user123", "escalation contacts"
            )

    def test_retrieve_infrastructure_knowledge(self, retrieve_tool, mock_client):
        """Test retrieving infrastructure knowledge."""
        mock_knowledge = [
            Mock(
                model_dump=Mock(
                    return_value={
                        "service_name": "web-service",
                        "knowledge_type": "dependency",
                        "knowledge_data": {"depends_on": "database"},
                    }
                )
            )
        ]

        with patch(
            "sre_agent.memory.tools._retrieve_infrastructure_knowledge"
        ) as mock_retrieve:
            mock_retrieve.return_value = mock_knowledge

            result = retrieve_tool._run(
                memory_type="infrastructure",
                query="service dependencies",
                actor_id="sre-agent",
            )

            result_data = json.loads(result)
            assert len(result_data) == 1
            assert result_data[0]["service_name"] == "web-service"
            mock_retrieve.assert_called_once_with(
                mock_client, "sre-agent", "service dependencies"
            )

    def test_retrieve_investigation_summaries(self, retrieve_tool, mock_client):
        """Test retrieving investigation summaries."""
        mock_summaries = [
            Mock(
                model_dump=Mock(
                    return_value={
                        "incident_id": "incident_123",
                        "query": "Service down",
                        "resolution_status": "completed",
                    }
                )
            )
        ]

        with patch(
            "sre_agent.memory.tools._retrieve_investigation_summaries"
        ) as mock_retrieve:
            mock_retrieve.return_value = mock_summaries

            result = retrieve_tool._run(
                memory_type="investigation",
                query="service outage",
                actor_id="sre-agent",
                max_results=3,
            )

            result_data = json.loads(result)
            assert len(result_data) == 1
            assert result_data[0]["incident_id"] == "incident_123"
            mock_retrieve.assert_called_once_with(
                mock_client, "sre-agent", "service outage"
            )

    def test_retrieve_unknown_memory_type(self, retrieve_tool, mock_client):
        """Test retrieving with unknown memory type."""
        result = retrieve_tool._run(
            memory_type="unknown", query="test query", actor_id="sre-agent"
        )

        result_data = json.loads(result)
        assert "error" in result_data
        assert "Unknown memory type: unknown" in result_data["error"]
        assert "supported_types" in result_data

    def test_retrieve_memory_exception(self, retrieve_tool, mock_client):
        """Test handling exceptions during retrieval."""
        with patch(
            "sre_agent.memory.tools._retrieve_user_preferences"
        ) as mock_retrieve:
            mock_retrieve.side_effect = Exception("Database error")

            result = retrieve_tool._run(
                memory_type="preference", query="test query", actor_id="user123"
            )

            result_data = json.loads(result)
            assert "error" in result_data
            assert "Error retrieving preference memory" in result_data["error"]
