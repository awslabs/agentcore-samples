from unittest.mock import AsyncMock, Mock, patch

import pytest

from sre_agent.agent_state import AgentState
from sre_agent.supervisor import SupervisorAgent


class TestMemoryIntegration:
    """Integration tests for memory system with supervisor agent."""

    @pytest.fixture
    def mock_memory_config(self):
        """Mock memory configuration."""
        with patch("sre_agent.supervisor._load_memory_config") as mock_config:
            config_mock = Mock()
            config_mock.enabled = True
            config_mock.memory_name = "test-memory"
            config_mock.region = "us-east-1"
            mock_config.return_value = config_mock
            yield config_mock

    @pytest.fixture
    def mock_memory_client(self):
        """Mock SREMemoryClient."""
        with patch("sre_agent.supervisor.SREMemoryClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client
            yield mock_client

    @pytest.fixture
    def mock_memory_tools(self):
        """Mock memory tools."""
        with (
            patch("sre_agent.supervisor.SaveMemoryTool") as mock_save,
            patch("sre_agent.supervisor.RetrieveMemoryTool") as mock_retrieve,
        ):
            save_tool = Mock()
            retrieve_tool = Mock()
            mock_save.return_value = save_tool
            mock_retrieve.return_value = retrieve_tool
            yield save_tool, retrieve_tool

    @pytest.fixture
    def mock_memory_hooks(self):
        """Mock memory hooks."""
        with patch("sre_agent.supervisor.MemoryHookProvider") as mock_hooks_class:
            mock_hooks = Mock()
            mock_hooks.on_investigation_start = AsyncMock(
                return_value={
                    "user_preferences": [],
                    "infrastructure_knowledge": [],
                    "past_investigations": [],
                }
            )
            mock_hooks.on_investigation_complete = AsyncMock()
            mock_hooks_class.return_value = mock_hooks
            yield mock_hooks

    def test_supervisor_memory_initialization_enabled(
        self,
        mock_memory_config,
        mock_memory_client,
        mock_memory_tools,
        mock_memory_hooks,
    ):
        """Test supervisor initializes memory system when enabled."""
        supervisor = SupervisorAgent(llm_provider="anthropic")

        assert supervisor.memory_client is not None
        assert supervisor.save_memory_tool is not None
        assert supervisor.retrieve_memory_tool is not None
        assert supervisor.memory_hooks is not None

    def test_supervisor_memory_initialization_disabled(self):
        """Test supervisor handles disabled memory system."""
        with patch("sre_agent.supervisor._load_memory_config") as mock_config:
            config_mock = Mock()
            config_mock.enabled = False
            mock_config.return_value = config_mock

            supervisor = SupervisorAgent(llm_provider="anthropic")

            assert supervisor.memory_client is None
            assert supervisor.save_memory_tool is None
            assert supervisor.retrieve_memory_tool is None
            assert supervisor.memory_hooks is None

    @pytest.mark.asyncio
    async def test_create_investigation_plan_with_memory(
        self,
        mock_memory_config,
        mock_memory_client,
        mock_memory_tools,
        mock_memory_hooks,
    ):
        """Test investigation plan creation includes memory context."""
        # Mock LLM response
        mock_plan = Mock()
        mock_plan.steps = ["Check kubernetes status"]
        mock_plan.agents_sequence = ["kubernetes"]
        mock_plan.complexity = "simple"
        mock_plan.auto_execute = True
        mock_plan.reasoning = "Single agent check"

        with patch.object(SupervisorAgent, "_create_llm") as mock_create_llm:
            mock_llm = Mock()
            mock_structured_llm = Mock()
            mock_structured_llm.ainvoke = AsyncMock(return_value=mock_plan)
            mock_llm.with_structured_output.return_value = mock_structured_llm
            mock_create_llm.return_value = mock_llm

            supervisor = SupervisorAgent(llm_provider="anthropic")

            # Test state with memory fields
            state = AgentState(
                current_query="Why is my pod failing?",
                user_id="user123",
                incident_id="incident_456",
                session_id="test_session_123",
                messages=[],
                next="FINISH",
                agent_results={},
                metadata={},
                requires_collaboration=False,
                agents_invoked=[],
                final_response=None,
                auto_approve_plan=False,
                memory_context=None,
                captured_preferences=None,
                captured_knowledge=None,
            )

            plan = await supervisor.create_investigation_plan(state)

            # Verify memory hooks were called
            mock_memory_hooks.on_investigation_start.assert_called_once_with(
                query="Why is my pod failing?",
                user_id="user123",
                actor_id="sre-agent",
                session_id="test_session_123",
                incident_id="incident_456",
            )

            # Verify memory context was stored in state
            assert state.get("memory_context") is not None

            # Verify plan was created
            assert plan.steps == ["Check kubernetes status"]
            assert plan.agents_sequence == ["kubernetes"]

    @pytest.mark.asyncio
    async def test_aggregate_responses_saves_summary(
        self,
        mock_memory_config,
        mock_memory_client,
        mock_memory_tools,
        mock_memory_hooks,
    ):
        """Test that aggregate_responses saves investigation summary."""
        with (
            patch.object(SupervisorAgent, "_create_llm") as mock_create_llm,
            patch.object(SupervisorAgent, "formatter") as mock_formatter,
        ):
            mock_llm = Mock()
            mock_create_llm.return_value = mock_llm

            mock_formatter.format_investigation_response.return_value = (
                "Investigation complete."
            )

            supervisor = SupervisorAgent(llm_provider="anthropic")

            # Test state with agent results
            state = AgentState(
                current_query="Service down",
                user_id="user123",
                incident_id="incident_789",
                session_id="test_session_456",
                messages=[],
                next="FINISH",
                agent_results={"kubernetes": "Pod is healthy"},
                metadata={},
                requires_collaboration=False,
                agents_invoked=["kubernetes"],
                final_response=None,
                auto_approve_plan=False,
                memory_context=None,
                captured_preferences=None,
                captured_knowledge=None,
            )

            result = await supervisor.aggregate_responses(state)

            # Verify memory hooks were called to save summary
            mock_memory_hooks.on_investigation_complete.assert_called_once_with(
                state=state, final_response="Investigation complete."
            )

            # Verify response was returned
            assert result["final_response"] == "Investigation complete."
            assert result["next"] == "FINISH"
