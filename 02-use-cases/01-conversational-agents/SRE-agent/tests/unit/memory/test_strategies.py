from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from sre_agent.memory.strategies import (
    InfrastructureKnowledge,
    InvestigationSummary,
    UserPreference,
    _retrieve_user_preferences,
    _save_user_preference,
)


class TestUserPreference:
    """Tests for UserPreference model."""

    def test_user_preference_creation(self):
        """Test creating a UserPreference instance."""
        preference = UserPreference(
            user_id="user123",
            preference_type="escalation",
            preference_value={"contact": "ops-team@company.com"},
            context="Test context",
        )

        assert preference.user_id == "user123"
        assert preference.preference_type == "escalation"
        assert preference.preference_value["contact"] == "ops-team@company.com"
        assert preference.context == "Test context"
        assert isinstance(preference.timestamp, datetime)

    def test_user_preference_without_context(self):
        """Test creating UserPreference without context."""
        preference = UserPreference(
            user_id="user123",
            preference_type="notification",
            preference_value={"channel": "#alerts"},
        )

        assert preference.context is None


class TestInfrastructureKnowledge:
    """Tests for InfrastructureKnowledge model."""

    def test_infrastructure_knowledge_creation(self):
        """Test creating InfrastructureKnowledge instance."""
        knowledge = InfrastructureKnowledge(
            service_name="web-service",
            knowledge_type="dependency",
            knowledge_data={"depends_on": "database"},
            confidence=0.9,
        )

        assert knowledge.service_name == "web-service"
        assert knowledge.knowledge_type == "dependency"
        assert knowledge.knowledge_data["depends_on"] == "database"
        assert knowledge.confidence == 0.9
        assert isinstance(knowledge.timestamp, datetime)

    def test_infrastructure_knowledge_default_confidence(self):
        """Test default confidence value."""
        knowledge = InfrastructureKnowledge(
            service_name="api-service",
            knowledge_type="baseline",
            knowledge_data={"metric": "cpu", "value": "50%"},
        )

        assert knowledge.confidence == 0.8  # Default value


class TestInvestigationSummary:
    """Tests for InvestigationSummary model."""

    def test_investigation_summary_creation(self):
        """Test creating InvestigationSummary instance."""
        summary = InvestigationSummary(
            incident_id="incident_123",
            query="Why is the service down?",
            timeline=[{"time": "10:00", "action": "Started investigation"}],
            actions_taken=["Checked logs", "Verified metrics"],
            resolution_status="completed",
            key_findings=["High CPU usage", "Memory leak detected"],
        )

        assert summary.incident_id == "incident_123"
        assert summary.query == "Why is the service down?"
        assert len(summary.timeline) == 1
        assert len(summary.actions_taken) == 2
        assert summary.resolution_status == "completed"
        assert len(summary.key_findings) == 2
        assert isinstance(summary.timestamp, datetime)

    def test_investigation_summary_empty_lists(self):
        """Test InvestigationSummary with empty lists."""
        summary = InvestigationSummary(
            incident_id="incident_456", query="Test query", resolution_status="ongoing"
        )

        assert summary.timeline == []
        assert summary.actions_taken == []
        assert summary.key_findings == []


class TestStrategyFunctions:
    """Tests for strategy helper functions."""

    @pytest.mark.asyncio
    async def test_save_user_preference_success(self):
        """Test successful user preference saving."""
        mock_client = Mock()
        mock_client.save_event = AsyncMock(return_value=True)

        preference = UserPreference(
            user_id="user123",
            preference_type="escalation",
            preference_value={"contact": "ops@company.com"},
        )

        result = await _save_user_preference(mock_client, "user123", preference)

        assert result is True
        mock_client.save_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_user_preference_failure(self):
        """Test user preference saving failure."""
        mock_client = Mock()
        mock_client.save_event = AsyncMock(side_effect=Exception("Save failed"))

        preference = UserPreference(
            user_id="user123",
            preference_type="escalation",
            preference_value={"contact": "ops@company.com"},
        )

        result = await _save_user_preference(mock_client, "user123", preference)

        assert result is False

    @pytest.mark.asyncio
    async def test_retrieve_user_preferences_success(self):
        """Test successful user preference retrieval."""
        mock_client = Mock()
        mock_client.retrieve_memories = AsyncMock(
            return_value=[
                {
                    "user_id": "user123",
                    "preference_type": "escalation",
                    "preference_value": {"contact": "ops@company.com"},
                    "context": None,
                    "timestamp": datetime.utcnow(),
                }
            ]
        )

        preferences = await _retrieve_user_preferences(
            mock_client, "user123", "escalation query"
        )

        assert len(preferences) == 1
        assert preferences[0].user_id == "user123"
        assert preferences[0].preference_type == "escalation"
        mock_client.retrieve_memories.assert_called_once_with(
            memory_type="preferences", actor_id="user123", query="escalation query"
        )

    @pytest.mark.asyncio
    async def test_retrieve_user_preferences_failure(self):
        """Test user preference retrieval failure."""
        mock_client = Mock()
        mock_client.retrieve_memories = AsyncMock(
            side_effect=Exception("Retrieval failed")
        )

        preferences = await _retrieve_user_preferences(
            mock_client, "user123", "escalation query"
        )

        assert preferences == []
