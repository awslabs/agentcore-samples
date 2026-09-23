# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for Coordinator Agent class.

Note: These tests require the coordinator module and its dependencies which
are only properly configured in the container environment. The coordinator
has complex imports (tools.profile_tools, tools.routing_tools) that require
special path setup. Tests are skipped when running locally.
"""

import json
import pytest
import sys
import os
from unittest.mock import patch, MagicMock, AsyncMock

# Skip tests if coordinator dependencies aren't available
_coordinator_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents", "coordinator")
sys.path.insert(0, _coordinator_path)

try:
    # Clear any cached agent/tools modules to ensure we load the right one
    for mod_name in list(sys.modules.keys()):
        if mod_name in ("agent",) or mod_name.startswith("agent.") or mod_name.startswith("tools."):
            del sys.modules[mod_name]

    from agent import CoordinatorAgent, create_agent

    HAS_COORDINATOR_DEPS = True
except (ImportError, ModuleNotFoundError):
    HAS_COORDINATOR_DEPS = False
    CoordinatorAgent = None
    create_agent = None

pytestmark = pytest.mark.skipif(
    not HAS_COORDINATOR_DEPS,
    reason="Coordinator module dependencies not available (container-only)",
)


# ---- Fixtures ----


@pytest.fixture
def mock_router():
    """Create a mock SubAgentRouter."""
    router = MagicMock()
    router.route_to_profile = AsyncMock(return_value='{"status": "success"}')
    router.route_to_accounts = AsyncMock(return_value='{"status": "success"}')
    return router


@pytest.fixture
def full_permissions_context():
    """User context with all 13 fine-grained scopes."""
    return {
        "user_id": "auth0|123456789",
        "customer_id": "CUST-12345",
        "email": "test@example.com",
        "access_token": "mock-jwt-token",
        "permissions": [
            "openid",
            "profile",
            "email",
            "profile:personal:read",
            "profile:personal:write",
            "profile:preferences:read",
            "profile:preferences:write",
            "accounts:savings:read",
            "accounts:savings:write",
            "accounts:transaction:read",
            "accounts:credit:read",
            "accounts:credit:write",
            "accounts:investment:read",
        ],
    }


@pytest.fixture
def profile_only_context():
    """User context with only profile scopes (no accounts)."""
    return {
        "user_id": "auth0|profile-only-user",
        "customer_id": "CUST-00099",
        "email": "profile-only@example.com",
        "access_token": "mock-jwt-token-profile-only",
        "permissions": [
            "openid",
            "profile",
            "email",
            "profile:personal:read",
            "profile:personal:write",
            "profile:preferences:read",
            "profile:preferences:write",
        ],
    }


@pytest.fixture
def no_permissions_context():
    """User context with no fine-grained permissions."""
    return {
        "user_id": "auth0|no-perms-user",
        "customer_id": "CUST-00000",
        "email": "noperms@example.com",
        "access_token": "mock-jwt-token-noperms",
        "permissions": [],
    }


class TestCoordinatorAgentInit:
    """Tests for CoordinatorAgent initialization."""

    def test_init_with_required_params(self, mock_router, full_permissions_context):
        """Test initialization with required parameters."""
        agent = CoordinatorAgent(
            session_id="session-abc",
            user_context=full_permissions_context,
            router=mock_router,
        )

        assert agent.session_id == "session-abc"
        assert agent.user_context == full_permissions_context

    def test_init_with_token_exchange_service(self, mock_router, full_permissions_context):
        """Test initialization with optional token_exchange_service."""
        mock_tes = MagicMock()

        agent = CoordinatorAgent(
            session_id="session-abc",
            user_context=full_permissions_context,
            router=mock_router,
            token_exchange_service=mock_tes,
        )

        assert agent.token_exchange_service is mock_tes

    def test_init_with_custom_bedrock_client(self, mock_router, full_permissions_context):
        """Test initialization with optional bedrock_client."""
        mock_bedrock = MagicMock()

        agent = CoordinatorAgent(
            session_id="session-abc",
            user_context=full_permissions_context,
            router=mock_router,
            bedrock_client=mock_bedrock,
        )

        assert agent.bedrock_client is mock_bedrock

    def test_init_sets_conversation_history(self, mock_router, full_permissions_context):
        """Test initialization creates empty conversation history."""
        agent = CoordinatorAgent(
            session_id="session-abc",
            user_context=full_permissions_context,
            router=mock_router,
        )

        assert agent.conversation_history == []


class TestCoordinatorAgentProcess:
    """Tests for process method."""

    @pytest.mark.asyncio
    @patch("agent.CoordinatorAgent._invoke_bedrock")
    async def test_process_adds_to_history(self, mock_invoke, mock_router, full_permissions_context):
        """Test that process adds messages to conversation history."""
        mock_invoke.return_value = {
            "output": {"message": {"content": [{"text": "Response"}]}},
            "stop_reason": "end_turn",
        }

        agent = CoordinatorAgent(
            session_id="session-abc",
            user_context=full_permissions_context,
            router=mock_router,
        )

        await agent.process("Hello", full_permissions_context)

        # Should have user message in history
        assert len(agent.conversation_history) >= 1

    @pytest.mark.asyncio
    @patch("agent.CoordinatorAgent._invoke_bedrock")
    async def test_process_returns_response(self, mock_invoke, mock_router, full_permissions_context):
        """Test that process returns the response text."""
        mock_invoke.return_value = {
            "output": {"message": {"content": [{"text": "Hello! How can I help?"}]}},
            "stop_reason": "end_turn",
        }

        agent = CoordinatorAgent(
            session_id="session-abc",
            user_context=full_permissions_context,
            router=mock_router,
        )

        response = await agent.process("Hello", full_permissions_context)

        assert "output" in response
        assert "Hello" in response["output"] or "help" in response["output"].lower()

    @pytest.mark.asyncio
    @patch("agent.CoordinatorAgent._invoke_bedrock")
    async def test_process_handles_exception(self, mock_invoke, mock_router, full_permissions_context):
        """Test that process handles exceptions gracefully."""
        mock_invoke.side_effect = Exception("API Error")

        agent = CoordinatorAgent(
            session_id="session-abc",
            user_context=full_permissions_context,
            router=mock_router,
        )

        response = await agent.process("Hello", full_permissions_context)

        assert "output" in response
        assert "error" in response["output"].lower() or "apologize" in response["output"].lower()


class TestAccountsScopesClassAttribute:
    """Tests for ACCOUNTS_SCOPES class attribute."""

    def test_accounts_scopes_defined(self):
        """Test that ACCOUNTS_SCOPES class attribute exists and is a set."""
        assert hasattr(CoordinatorAgent, "ACCOUNTS_SCOPES")
        assert isinstance(CoordinatorAgent.ACCOUNTS_SCOPES, set)

    def test_accounts_scopes_contains_expected_scopes(self):
        """Test that ACCOUNTS_SCOPES contains the correct fine-grained scopes."""
        expected = {
            "accounts:savings:read",
            "accounts:savings:write",
            "accounts:transaction:read",
            "accounts:credit:read",
            "accounts:credit:write",
            "accounts:investment:read",
        }
        assert CoordinatorAgent.ACCOUNTS_SCOPES == expected

    def test_accounts_scopes_does_not_contain_profile_scopes(self):
        """Test that ACCOUNTS_SCOPES has no profile-related scopes."""
        for scope in CoordinatorAgent.ACCOUNTS_SCOPES:
            assert not scope.startswith("profile:"), f"Unexpected profile scope in ACCOUNTS_SCOPES: {scope}"


class TestHasAccountsScopes:
    """Tests for _has_accounts_scopes method."""

    def test_returns_true_with_all_accounts_scopes(self, mock_router, full_permissions_context):
        """Test returns True when user has all accounts scopes."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=full_permissions_context,
            router=mock_router,
        )
        permissions = full_permissions_context["permissions"]
        assert agent._has_accounts_scopes(permissions) is True

    def test_returns_true_with_single_accounts_scope(self, mock_router, full_permissions_context):
        """Test returns True with just one accounts scope."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=full_permissions_context,
            router=mock_router,
        )
        assert agent._has_accounts_scopes(["accounts:savings:read"]) is True

    def test_returns_false_with_profile_only_scopes(self, mock_router, profile_only_context):
        """Test returns False when user has only profile scopes."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=profile_only_context,
            router=mock_router,
        )
        permissions = profile_only_context["permissions"]
        assert agent._has_accounts_scopes(permissions) is False

    def test_returns_false_with_no_permissions(self, mock_router, no_permissions_context):
        """Test returns False when user has no permissions."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=no_permissions_context,
            router=mock_router,
        )
        assert agent._has_accounts_scopes([]) is False

    def test_returns_false_with_openid_only(self, mock_router, full_permissions_context):
        """Test returns False with only openid/profile/email scopes."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=full_permissions_context,
            router=mock_router,
        )
        assert agent._has_accounts_scopes(["openid", "profile", "email"]) is False


class TestGetAvailableTools:
    """Tests for _get_available_tools scope-gating behavior."""

    def test_full_scopes_includes_accounts_tools(self, mock_router, full_permissions_context):
        """Test that user with accounts scopes gets the accounts routing tool."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=full_permissions_context,
            router=mock_router,
        )

        tools = agent._get_available_tools(full_permissions_context)
        tool_names = [t.get("name") for t in tools]

        assert "route_to_accounts_agent" in tool_names

    def test_full_scopes_includes_profile_tools(self, mock_router, full_permissions_context):
        """Test that user with profile:personal:read gets profile tools."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=full_permissions_context,
            router=mock_router,
        )

        tools = agent._get_available_tools(full_permissions_context)
        tool_names = [t.get("name") for t in tools]

        assert "route_to_profile_agent" in tool_names

    def test_profile_only_user_no_accounts_tool(self, mock_router, profile_only_context):
        """Test that user with only profile scopes does NOT get accounts routing tool."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=profile_only_context,
            router=mock_router,
        )

        tools = agent._get_available_tools(profile_only_context)
        tool_names = [t.get("name") for t in tools]

        assert "route_to_accounts_agent" not in tool_names

    def test_profile_only_user_still_gets_profile_tools(self, mock_router, profile_only_context):
        """Test that profile-only user still gets profile tools."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=profile_only_context,
            router=mock_router,
        )

        tools = agent._get_available_tools(profile_only_context)
        tool_names = [t.get("name") for t in tools]

        # Should have profile routing tool and profile tools
        assert "route_to_profile_agent" in tool_names
        assert "profile_get_customer_profile" in tool_names

    def test_profile_only_user_gets_other_routing_tools(self, mock_router, profile_only_context):
        """Test that profile-only user still gets non-accounts routing tools."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=profile_only_context,
            router=mock_router,
        )

        tools = agent._get_available_tools(profile_only_context)
        tool_names = [t.get("name") for t in tools]

        # get_available_agents and route_to_profile_agent should be present
        assert "get_available_agents" in tool_names
        assert "route_to_profile_agent" in tool_names

    def test_no_permissions_user_gets_minimal_tools(self, mock_router, no_permissions_context):
        """Test that user with no permissions gets minimal tool set."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=no_permissions_context,
            router=mock_router,
        )

        tools = agent._get_available_tools(no_permissions_context)
        tool_names = [t.get("name") for t in tools]

        # Should NOT get accounts or profile tools
        assert "route_to_accounts_agent" not in tool_names
        assert "profile_get_customer_profile" not in tool_names


class TestRouteToolCallPermissionDenied:
    """Tests for _route_tool_call PERMISSION_DENIED safety net."""

    @pytest.mark.asyncio
    async def test_accounts_tool_denied_for_profile_only_user(self, mock_router, profile_only_context):
        """Test that accounts tool call returns PERMISSION_DENIED for profile-only user."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=profile_only_context,
            router=mock_router,
        )

        result = await agent._route_tool_call(
            "route_to_accounts_agent",
            {"query": "Show my balance"},
        )

        parsed = json.loads(result)
        assert parsed["error"] == "PERMISSION_DENIED"

    @pytest.mark.asyncio
    async def test_accounts_prefixed_tool_denied_for_profile_only_user(self, mock_router, profile_only_context):
        """Test that accounts_* prefixed tool is denied for profile-only user."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=profile_only_context,
            router=mock_router,
        )

        result = await agent._route_tool_call(
            "accounts_query",
            {"query": "Show my balance"},
        )

        parsed = json.loads(result)
        assert parsed["error"] == "PERMISSION_DENIED"
        assert "permission" in parsed["message"].lower()

    @pytest.mark.asyncio
    async def test_accounts_tool_allowed_with_accounts_scopes(self, mock_router, full_permissions_context):
        """Test that accounts tool call succeeds for user with accounts scopes."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=full_permissions_context,
            router=mock_router,
        )

        result = await agent._route_tool_call(
            "route_to_accounts_agent",
            {"query": "Show my balance"},
        )

        # Should NOT be PERMISSION_DENIED
        parsed = json.loads(result)
        assert parsed.get("error") != "PERMISSION_DENIED"

    @pytest.mark.asyncio
    async def test_profile_tool_not_denied_for_profile_user(self, mock_router, profile_only_context):
        """Test that profile tool call succeeds for profile-scoped user."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=profile_only_context,
            router=mock_router,
        )

        result = await agent._route_tool_call(
            "route_to_profile_agent",
            {"query": "Show my profile"},
        )

        # Should NOT be PERMISSION_DENIED
        try:
            parsed = json.loads(result)
            assert parsed.get("error") != "PERMISSION_DENIED"
        except json.JSONDecodeError:
            # Non-JSON response is fine (means it was routed successfully)
            pass

    @pytest.mark.asyncio
    async def test_get_available_agents_always_works(self, mock_router, no_permissions_context):
        """Test that get_available_agents tool works regardless of scopes."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=no_permissions_context,
            router=mock_router,
        )

        result = await agent._route_tool_call(
            "get_available_agents",
            {},
        )

        parsed = json.loads(result)
        assert "available_agents" in parsed

    @pytest.mark.asyncio
    async def test_unknown_tool_raises_error(self, mock_router, full_permissions_context):
        """Test that unknown tool raises ValueError."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=full_permissions_context,
            router=mock_router,
        )

        with pytest.raises(ValueError, match="Unknown tool"):
            await agent._route_tool_call("nonexistent_tool", {})


class TestPermissionsVsScopePrecedence:
    """Tests verifying that RBAC `permissions` claim takes precedence over `scope`.

    Auth0 JWTs contain both `scope` (all API scopes, client-level) and
    `permissions` (RBAC-restricted, per-user). The coordinator must use
    `permissions` as the primary authorization source so that users without
    an accounts role cannot access accounts tools even though `scope` contains
    all scopes.
    """

    def test_profile_only_rbac_user_no_accounts_tools(self, mock_router):
        """Profile-only RBAC user should not get accounts tools even though
        the JWT scope claim contains all scopes.

        This is the core RBAC precedence test: user_context["permissions"]
        is populated from the `permissions` claim (profile-only), not `scope`
        (which has everything).
        """
        # Simulates what extract_user_context_from_payload produces when
        # the JWT has permissions=[profile scopes] and scope=[all scopes]
        profile_only_rbac_context = {
            "user_id": "auth0|profile-only-rbac",
            "customer_id": "CUST-RBAC-001",
            "email": "rbac-profile@example.com",
            "access_token": "mock-jwt",
            "permissions": [
                "openid",
                "profile",
                "email",
                "profile:personal:read",
                "profile:personal:write",
                "profile:preferences:read",
                "profile:preferences:write",
            ],
        }

        agent = CoordinatorAgent(
            session_id="s1",
            user_context=profile_only_rbac_context,
            router=mock_router,
        )

        tools = agent._get_available_tools(profile_only_rbac_context)
        tool_names = [t.get("name") for t in tools]

        assert "route_to_accounts_agent" not in tool_names
        assert "route_to_profile_agent" in tool_names

    def test_full_rbac_user_gets_all_tools(self, mock_router):
        """Full-access RBAC user should get both profile and accounts tools."""
        full_rbac_context = {
            "user_id": "auth0|full-rbac",
            "customer_id": "CUST-RBAC-002",
            "email": "rbac-full@example.com",
            "access_token": "mock-jwt",
            "permissions": [
                "openid",
                "profile",
                "email",
                "profile:personal:read",
                "profile:personal:write",
                "profile:preferences:read",
                "profile:preferences:write",
                "accounts:savings:read",
                "accounts:savings:write",
                "accounts:transaction:read",
                "accounts:credit:read",
                "accounts:credit:write",
                "accounts:investment:read",
            ],
        }

        agent = CoordinatorAgent(
            session_id="s1",
            user_context=full_rbac_context,
            router=mock_router,
        )

        tools = agent._get_available_tools(full_rbac_context)
        tool_names = [t.get("name") for t in tools]

        assert "route_to_accounts_agent" in tool_names
        assert "route_to_profile_agent" in tool_names

    @pytest.mark.asyncio
    async def test_profile_only_rbac_accounts_denied(self, mock_router):
        """Profile-only RBAC user gets PERMISSION_DENIED on accounts tool call."""
        profile_only_rbac_context = {
            "user_id": "auth0|profile-only-rbac",
            "customer_id": "CUST-RBAC-001",
            "email": "rbac-profile@example.com",
            "access_token": "mock-jwt",
            "permissions": [
                "openid",
                "profile",
                "email",
                "profile:personal:read",
                "profile:personal:write",
                "profile:preferences:read",
                "profile:preferences:write",
            ],
        }

        agent = CoordinatorAgent(
            session_id="s1",
            user_context=profile_only_rbac_context,
            router=mock_router,
        )

        result = await agent._route_tool_call(
            "route_to_accounts_agent",
            {"query": "Show my balance"},
        )

        parsed = json.loads(result)
        assert parsed["error"] == "PERMISSION_DENIED"


class TestCreateAgentFactory:
    """Tests for create_agent factory function."""

    def test_creates_agent(self, mock_router, full_permissions_context):
        """Test factory creates CoordinatorAgent instance."""
        agent = create_agent(
            session_id="session-abc",
            user_context=full_permissions_context,
            router=mock_router,
        )

        assert isinstance(agent, CoordinatorAgent)
        assert agent.session_id == "session-abc"

    def test_creates_agent_with_token_exchange_service(self, mock_router, full_permissions_context):
        """Test factory creates agent with token_exchange_service."""
        mock_tes = MagicMock()

        agent = create_agent(
            session_id="session-abc",
            user_context=full_permissions_context,
            router=mock_router,
            token_exchange_service=mock_tes,
        )

        assert isinstance(agent, CoordinatorAgent)
        assert agent.token_exchange_service is mock_tes


class TestSystemPrompt:
    """Tests for _create_system_prompt."""

    def test_system_prompt_includes_customer_context(self, mock_router, full_permissions_context):
        """Test that system prompt embeds user context."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=full_permissions_context,
            router=mock_router,
        )

        prompt = agent._create_system_prompt(full_permissions_context)

        assert "CUST-12345" in prompt
        assert "test@example.com" in prompt
        assert "auth0|123456789" in prompt

    def test_system_prompt_includes_permissions(self, mock_router, full_permissions_context):
        """Test that system prompt lists permissions."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=full_permissions_context,
            router=mock_router,
        )

        prompt = agent._create_system_prompt(full_permissions_context)

        assert "profile:personal:read" in prompt
        assert "accounts:savings:read" in prompt


class TestConvertToolsToBedrockFormat:
    """Tests for _convert_tools_to_bedrock_format."""

    def test_converts_tool_format(self, mock_router, full_permissions_context):
        """Test conversion from Anthropic-style to Bedrock format."""
        agent = CoordinatorAgent(
            session_id="s1",
            user_context=full_permissions_context,
            router=mock_router,
        )

        tools = [
            {
                "name": "test_tool",
                "description": "A test tool",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

        bedrock_tools = agent._convert_tools_to_bedrock_format(tools)

        assert len(bedrock_tools) == 1
        assert "toolSpec" in bedrock_tools[0]
        assert bedrock_tools[0]["toolSpec"]["name"] == "test_tool"
        assert bedrock_tools[0]["toolSpec"]["description"] == "A test tool"
        assert "json" in bedrock_tools[0]["toolSpec"]["inputSchema"]
