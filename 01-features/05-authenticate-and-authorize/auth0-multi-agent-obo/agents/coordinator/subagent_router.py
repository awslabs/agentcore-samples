# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
SubAgent Router for Coordinator Agent.

This module handles routing requests to specialized action agents
(profile, accounts) via HTTP with RFC 8693 token exchange.

Architecture:
    Client -> Coordinator (receives JWT) -> [RFC 8693 Exchange] -> Action Agents (attenuated JWT)

The coordinator exchanges the user's original JWT for a new token with
attenuated scopes before invoking each sub-agent (RFC 8693 token exchange).
The router itself receives the already-exchanged token and forwards it
to the target agent. This approach ensures:
    - Least-privilege access: each sub-agent receives only the scopes it needs
    - Consistent user identity across all agents (sub claim preserved)
    - End-to-end audit trail via JWT claims and the RFC 8693 `act` claim
    - No credential escalation — exchanged tokens are strictly scope-attenuated
    - Action agents can independently validate the exchanged JWT
"""

import json
import logging
import os
from typing import Any, Dict, Optional

from shared.http.agent_http_client import (
    AgentHttpClient,
    AgentInvocationError,
    AuthenticationError,
    AuthorizationError,
)

logger = logging.getLogger(__name__)


class SubAgentRouter:
    """
    Routes requests to appropriate sub-agents in the financial services system.

    This router manages HTTP-based communication with two specialized agents:
    - Customer Profile Agent: Profile operations
    - Accounts Agent: Account queries and operations

    The router receives an already-exchanged JWT token (via RFC 8693 token exchange
    performed by the CoordinatorAgent) and forwards it to the target sub-agent.
    Each sub-agent receives a token with scopes attenuated to only what it needs.
    """

    def __init__(self, http_client: Optional[AgentHttpClient] = None):
        """
        Initialize the SubAgentRouter.

        Args:
            http_client: Optional AgentHttpClient instance for testing/DI
        """
        self.client = http_client or AgentHttpClient()

        # Load agent IDs from environment variables
        self.profile_agent_id = os.getenv("PROFILE_AGENT_ID", "")
        self.accounts_agent_id = os.getenv("ACCOUNTS_AGENT_ID", "")

        # Optional endpoint URL overrides (for testing or custom deployments)
        self.profile_endpoint_url = os.getenv("PROFILE_AGENT_URL")
        self.accounts_endpoint_url = os.getenv("ACCOUNTS_AGENT_URL")

        logger.info("SubAgentRouter initialized with agent IDs:")
        logger.info(f"  Profile: {self.profile_agent_id}")
        logger.info(f"  Accounts: {self.accounts_agent_id}")

    async def route_to_profile(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        jwt_token: str,
        session_id: str,
        exchanged_token: Optional[str] = None,
    ) -> str:
        """
        Route request to the Customer Profile Agent.

        Args:
            tool_name: Name of the tool being invoked
            tool_input: Tool input parameters
            jwt_token: Original Auth0 JWT for platform-level authentication
            session_id: Session ID for conversation continuity
            exchanged_token: RFC 8693 exchanged token with attenuated scopes

        Returns:
            JSON string response from the profile agent
        """
        return await self._invoke_agent(
            agent_id=self.profile_agent_id,
            agent_name="Profile",
            tool_name=tool_name,
            tool_input=tool_input,
            jwt_token=jwt_token,
            session_id=session_id,
            endpoint_url=self.profile_endpoint_url,
            exchanged_token=exchanged_token,
        )

    async def route_to_accounts(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        jwt_token: str,
        session_id: str,
        exchanged_token: Optional[str] = None,
    ) -> str:
        """
        Route request to the Accounts Agent.

        Args:
            tool_name: Name of the tool being invoked
            tool_input: Tool input parameters
            jwt_token: Original Auth0 JWT for platform-level authentication
            session_id: Session ID for conversation continuity
            exchanged_token: RFC 8693 exchanged token with attenuated scopes

        Returns:
            JSON string response from the accounts agent
        """
        return await self._invoke_agent(
            agent_id=self.accounts_agent_id,
            agent_name="Accounts",
            tool_name=tool_name,
            tool_input=tool_input,
            jwt_token=jwt_token,
            session_id=session_id,
            endpoint_url=self.accounts_endpoint_url,
            exchanged_token=exchanged_token,
        )

    async def _invoke_agent(
        self,
        agent_id: str,
        agent_name: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        jwt_token: str,
        session_id: str,
        endpoint_url: Optional[str] = None,
        exchanged_token: Optional[str] = None,
    ) -> str:
        """
        Invoke a sub-agent via HTTP.

        The original Auth0 jwt_token is sent as the Authorization header to pass
        the AgentCore Runtime's platform-level JWT Authorizer. The exchanged_token
        (RFC 8693, scope-attenuated) is sent in the request payload context for
        application-level scope enforcement by the sub-agent.

        Args:
            agent_id: The AgentCore agent ID
            agent_name: Human-readable agent name for logging
            tool_name: Name of the tool being invoked
            tool_input: Tool input parameters
            jwt_token: Original Auth0 JWT for platform authentication
            session_id: Session ID for conversation continuity
            endpoint_url: Optional endpoint URL override
            exchanged_token: RFC 8693 exchanged token with attenuated scopes

        Returns:
            JSON string response from the agent

        Raises:
            ValueError: If agent_id is not configured
        """
        if not agent_id:
            error_msg = f"{agent_name} agent ID not configured"
            logger.error(error_msg)
            return json.dumps(
                {
                    "error": "AGENT_NOT_CONFIGURED",
                    "message": error_msg,
                }
            )

        if not jwt_token:
            error_msg = "JWT token is required for agent invocation"
            logger.error(error_msg)
            return json.dumps(
                {
                    "error": "MISSING_TOKEN",
                    "message": error_msg,
                }
            )

        try:
            # Build the input text for the sub-agent
            input_text = self._build_input_text(tool_name, tool_input)

            logger.info(
                f"Invoking {agent_name} agent (ID: {agent_id}) for tool: {tool_name} via HTTP with exchanged token"
            )
            logger.debug(f"Input: {input_text}")
            logger.debug(f"Session ID: {session_id}")

            # Build context with tool info and optional exchanged token
            context = {"tool_name": tool_name, "tool_input": tool_input}
            if exchanged_token:
                context["exchanged_token"] = exchanged_token

            # Invoke the agent via HTTP
            # Original Auth0 JWT in Authorization header (platform auth)
            # Exchanged token in payload context (application-level scope enforcement)
            response = self.client.invoke_agent(
                agent_id=agent_id,
                input_text=input_text,
                jwt_token=jwt_token,
                session_id=session_id,
                additional_context=context,
                endpoint_url=endpoint_url,
            )

            logger.info(f"{agent_name} agent invocation completed successfully")
            logger.debug(f"Response: {response}")

            # Extract the response text
            output_text = response.get("response", "")

            # If the response is already a string, return it
            if isinstance(output_text, str):
                return output_text

            # Otherwise, serialize to JSON
            return json.dumps(output_text)

        except AuthenticationError as e:
            logger.error(f"Authentication failed for {agent_name} agent: {e}")
            return json.dumps(
                {
                    "error": "AUTHENTICATION_ERROR",
                    "message": f"JWT validation failed: {str(e)}",
                    "agent": agent_name,
                }
            )

        except AuthorizationError as e:
            logger.error(f"Authorization denied for {agent_name} agent: {e}")
            return json.dumps(
                {
                    "error": "AUTHORIZATION_ERROR",
                    "message": f"Access denied: {str(e)}",
                    "agent": agent_name,
                }
            )

        except AgentInvocationError as e:
            logger.error(f"Error invoking {agent_name} agent: {e}")
            return json.dumps(
                {
                    "error": "INVOCATION_ERROR",
                    "message": f"Failed to invoke {agent_name} agent: {str(e)}",
                    "status_code": e.status_code,
                }
            )

        except Exception as e:
            logger.exception(f"Unexpected error invoking {agent_name} agent: {str(e)}")
            return json.dumps(
                {
                    "error": "UNEXPECTED_ERROR",
                    "message": f"Failed to invoke {agent_name} agent: {str(e)}",
                }
            )

    def _build_input_text(self, tool_name: str, tool_input: Dict[str, Any]) -> str:
        """
        Build input text for sub-agent from tool name and parameters.

        Args:
            tool_name: Name of the tool
            tool_input: Tool parameters

        Returns:
            Natural language input text for the agent
        """
        # Remove the agent prefix from tool name
        # e.g., "profile_get_customer_profile" -> "get_customer_profile"
        tool_action = tool_name.split("_", 1)[-1] if "_" in tool_name else tool_name

        # Build natural language request
        if not tool_input:
            return f"Please {tool_action.replace('_', ' ')}"

        # Format parameters as natural language
        params_text = ", ".join([f"{k}: {v}" for k, v in tool_input.items()])
        return f"Please {tool_action.replace('_', ' ')} with parameters: {params_text}"

    def validate_agent_configuration(self) -> Dict[str, bool]:
        """
        Validate that all agent IDs are configured.

        Returns:
            Dictionary mapping agent names to configuration status
        """
        return {
            "profile": bool(self.profile_agent_id),
            "accounts": bool(self.accounts_agent_id),
        }

    def get_available_agents(self) -> list[str]:
        """
        Get list of available (configured) agents.

        Returns:
            List of agent names that are properly configured
        """
        config = self.validate_agent_configuration()
        return [name for name, configured in config.items() if configured]

    async def route_by_intent(
        self,
        intent: str,
        query: str,
        jwt_token: str,
        session_id: str,
    ) -> str:
        """
        Route a query based on detected intent.

        Args:
            intent: Detected intent (profile, accounts)
            query: User query text
            jwt_token: Original JWT token (for platform auth; exchanged token passed separately)
            session_id: Session ID for conversation continuity

        Returns:
            Response from the appropriate agent
        """
        intent_lower = intent.lower()

        # Build tool input (no user_context needed, it's in the JWT)
        tool_input = {"query": query}

        # Route based on intent
        if intent_lower == "profile" and self.profile_agent_id:
            return await self.route_to_profile("profile_query", tool_input, jwt_token, session_id)
        elif intent_lower == "accounts" and self.accounts_agent_id:
            return await self.route_to_accounts("accounts_query", tool_input, jwt_token, session_id)
        else:
            return json.dumps(
                {
                    "error": "INVALID_INTENT",
                    "message": f"Unknown or unconfigured intent: {intent}",
                }
            )
