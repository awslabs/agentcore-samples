# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Coordinator Agent implementation for AgentCore Identity.

This module defines the CoordinatorAgent class that orchestrates requests
across multiple action agents in the financial services system.

Uses AWS Bedrock for LLM inference.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import boto3

from shared.auth.token_exchange import (
    TokenExchangeError,
    TokenExchangeRequest,
    TokenExchangeService,
)
from subagent_router import SubAgentRouter
from tools.profile_tools import get_profile_tools
from tools.routing_tools import get_routing_tools

# OpenTelemetry tracing for coordinator token exchange observability
try:
    from opentelemetry import trace

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

logger = logging.getLogger(__name__)

# Bedrock model ID for Claude
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0")


COORDINATOR_SYSTEM_PROMPT = """You are a Financial Services Coordinator Agent for a banking platform.

Your role is to:
1. Understand customer requests related to their financial services
2. Route requests to the appropriate specialized agent (profile or accounts)
3. Maintain conversation context and provide helpful responses
4. Ensure all requests respect customer authorization and data access controls

You have access to the following specialized agents:
- Customer Profile Agent: Handle profile information, address updates, phone updates, and preferences
- Accounts Agent: Handle account balances, account details, and account-related queries

Important guidelines:
- Always verify you have the customer's context before making requests
- Route requests to the most appropriate specialized agent
- Provide clear, professional, and helpful responses
- If a request is ambiguous, ask clarifying questions
- Respect customer privacy and data access permissions

Customer Context:
You have access to the authenticated customer's information including:
- Customer ID
- Email address
- Permissions/Scopes
- Any custom claims from the identity provider

Use the available tools to route requests to specialized agents and retrieve information.
"""


class CoordinatorAgent:
    """
    Coordinator Agent that orchestrates requests across action agents.

    This agent serves as the main entry point for customer requests and
    intelligently routes them to specialized agents while maintaining
    conversation context and session state.
    """

    def __init__(
        self,
        session_id: str,
        user_context: Dict[str, Any],
        router: SubAgentRouter,
        token_exchange_service: Optional[TokenExchangeService] = None,
        bedrock_client: Optional[Any] = None,
    ):
        """
        Initialize the Coordinator Agent.

        Args:
            session_id: Unique identifier for the conversation session
            user_context: Authentication context containing user claims
            router: SubAgentRouter for routing to action agents
            token_exchange_service: RFC 8693 token exchange service for scope attenuation
            bedrock_client: Optional Bedrock Runtime client
        """
        self.session_id = session_id
        self.user_context = user_context
        self.router = router
        self.token_exchange_service = token_exchange_service

        # Initialize Bedrock Runtime client
        self.bedrock_client = bedrock_client or boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION"))

        # Initialize conversation history
        self.conversation_history: List[Dict[str, Any]] = []

        logger.info(
            f"Coordinator agent initialized: session_id={session_id}, "
            f"user_id={user_context.get('user_id', 'unknown')}, "
            f"customer_id={user_context.get('customer_id', 'unknown')}"
        )

    async def process(self, user_input: str, user_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a user request through the coordinator agent.

        Args:
            user_input: The user's natural language request
            user_context: Authentication context for the user

        Returns:
            Dictionary containing the agent's response
        """
        start_time = time.time()
        logger.info(f"Processing request: {user_input[:100]}...")

        try:
            # Add user message to conversation history
            self.conversation_history.append({"role": "user", "content": [{"text": user_input}]})

            # Get available tools based on permissions
            tools = self._get_available_tools(user_context)
            logger.debug(f"Available tools: {[t.get('name', '') for t in tools]}")

            # Create system prompt with user context
            system_prompt = self._create_system_prompt(user_context)

            # Invoke Bedrock
            response = await self._invoke_bedrock(
                messages=self.conversation_history,
                system=system_prompt,
                tools=tools,
            )

            # Extract the final response text
            output_text = ""
            for block in response.get("output", {}).get("message", {}).get("content", []):
                if "text" in block:
                    output_text += block["text"]

            # Add assistant response to conversation history
            self.conversation_history.append({"role": "assistant", "content": [{"text": output_text}]})

            duration_ms = (time.time() - start_time) * 1000
            logger.info(f"Request completed in {duration_ms:.2f}ms")

            return {"output": output_text}

        except Exception as e:
            logger.error(f"Error processing request: {e}", exc_info=True)
            return {"output": "I apologize, but I encountered an error processing your request. Please try again."}

    def _create_system_prompt(self, user_context: Dict[str, Any]) -> str:
        """Create a system prompt with user context embedded."""
        customer_info = f"""

Current Customer Context:
- Customer ID: {user_context.get("customer_id", "N/A")}
- Email: {user_context.get("email", "N/A")}
- User ID: {user_context.get("user_id", "N/A")}
- Permissions: {", ".join(user_context.get("permissions", []))}
"""
        return COORDINATOR_SYSTEM_PROMPT + customer_info

    # Scopes that grant access to the accounts agent
    ACCOUNTS_SCOPES = {
        "accounts:savings:read",
        "accounts:savings:write",
        "accounts:transaction:read",
        "accounts:credit:read",
        "accounts:credit:write",
        "accounts:investment:read",
    }

    def _has_accounts_scopes(self, permissions: List[str]) -> bool:
        """Check if the user has at least one accounts scope."""
        return bool(self.ACCOUNTS_SCOPES & set(permissions))

    def _get_available_tools(self, user_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get available tools based on user permissions."""
        permissions = user_context.get("permissions", [])

        tools = []
        routing_tools = get_routing_tools(self.router, user_context)

        # Only expose accounts routing tool if user has accounts scopes
        if self._has_accounts_scopes(permissions):
            tools.extend(routing_tools)
        else:
            tools.extend(t for t in routing_tools if t.get("name") != "route_to_accounts_agent")

        if "profile:personal:read" in permissions:
            tools.extend(get_profile_tools(self.router, user_context))

        return tools

    async def _invoke_bedrock(
        self,
        messages: List[Dict[str, Any]],
        system: str,
        tools: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Invoke Bedrock Converse API."""
        try:
            # Build the request
            request_params = {
                "modelId": BEDROCK_MODEL_ID,
                "messages": messages,
                "system": [{"text": system}],
                "inferenceConfig": {
                    "maxTokens": 4096,
                    "temperature": 0.7,
                },
            }

            # Add tools if available
            if tools:
                bedrock_tools = self._convert_tools_to_bedrock_format(tools)
                if bedrock_tools:
                    request_params["toolConfig"] = {"tools": bedrock_tools}

            logger.debug(f"Invoking Bedrock with model: {BEDROCK_MODEL_ID}")
            response = self.bedrock_client.converse(**request_params)
            logger.debug(f"Bedrock response stop_reason: {response.get('stopReason', '')}")

            # Handle tool use if present
            stop_reason = response.get("stopReason", "")
            if stop_reason == "tool_use":
                tool_results = await self._execute_tools(
                    response.get("output", {}).get("message", {}).get("content", [])
                )

                messages.append(response["output"]["message"])
                messages.append({"role": "user", "content": tool_results})

                # Recursive call
                return await self._invoke_bedrock(messages, system, tools)

            return {
                "output": response.get("output", {}),
                "stop_reason": stop_reason,
            }

        except Exception as e:
            logger.error(f"Bedrock API call failed: {e}", exc_info=True)
            raise

    def _convert_tools_to_bedrock_format(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert Anthropic-style tool definitions to Bedrock format."""
        bedrock_tools = []
        for tool in tools:
            bedrock_tool = {
                "toolSpec": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "inputSchema": {"json": tool.get("input_schema", {})},
                }
            }
            bedrock_tools.append(bedrock_tool)
        return bedrock_tools

    async def _execute_tools(self, content: List[Any]) -> List[Dict[str, Any]]:
        """Execute tool calls."""
        tool_results = []

        for block in content:
            if isinstance(block, dict) and "toolUse" in block:
                tool_use = block["toolUse"]
                tool_name = tool_use.get("name", "")
                tool_input = tool_use.get("input", {})
                tool_use_id = tool_use.get("toolUseId", "")

                logger.info(f"Executing tool: {tool_name}")

                try:
                    result = await self._route_tool_call(tool_name, tool_input)
                    logger.debug(f"Tool {tool_name} completed successfully")

                    tool_results.append(
                        {
                            "toolResult": {
                                "toolUseId": tool_use_id,
                                "content": [{"text": result}],
                            }
                        }
                    )

                except Exception as e:
                    logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)

                    tool_results.append(
                        {
                            "toolResult": {
                                "toolUseId": tool_use_id,
                                "content": [{"text": f"Error: {str(e)}"}],
                                "status": "error",
                            }
                        }
                    )

        return tool_results

    async def _route_tool_call(self, tool_name: str, tool_input: Dict[str, Any]) -> str:
        """Route tool calls to the appropriate handler."""
        tool_input["user_context"] = self.user_context

        # Get original JWT token and session_id for sub-agent communication
        jwt_token = self.user_context.get("access_token", "")
        session_id = self.session_id

        # Handle direct profile/accounts tools — exchange token with appropriate scope policy
        # NOTE: The original Auth0 JWT is sent as the Authorization header (passes AgentCore
        # Runtime's platform-level JWT Authorizer). The exchanged token with attenuated scopes
        # is sent in the payload context for application-level scope enforcement.
        if tool_name.startswith("profile_") or tool_name == "route_to_profile_agent":
            exchanged_token = self._exchange_token_for_agent(jwt_token, "profile")
            effective_tool = "profile_query" if tool_name == "route_to_profile_agent" else tool_name
            return await self.router.route_to_profile(
                effective_tool,
                tool_input,
                jwt_token,
                session_id,
                exchanged_token=exchanged_token if exchanged_token != jwt_token else None,
            )
        elif tool_name.startswith("accounts_") or tool_name == "route_to_accounts_agent":
            # Check scopes before routing — prevents retry loops on permission denial
            permissions = self.user_context.get("permissions", [])
            if not self._has_accounts_scopes(permissions):
                return json.dumps(
                    {
                        "error": "PERMISSION_DENIED",
                        "message": (
                            "You do not have permission to access account information. "
                            "Your current permissions do not include any accounts scopes. "
                            "Please contact your administrator to request access."
                        ),
                    }
                )
            exchanged_token = self._exchange_token_for_agent(jwt_token, "accounts")
            effective_tool = "accounts_query" if tool_name == "route_to_accounts_agent" else tool_name
            return await self.router.route_to_accounts(
                effective_tool,
                tool_input,
                jwt_token,
                session_id,
                exchanged_token=exchanged_token if exchanged_token != jwt_token else None,
            )
        elif tool_name == "get_available_agents":
            return self._get_available_agents_response()
        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    def _exchange_token_for_agent(self, jwt_token: str, target_agent: str) -> str:
        """
        Exchange the user's JWT for an attenuated token scoped to the target agent.

        Uses RFC 8693 token exchange to create a new JWT with reduced scopes
        appropriate for the target sub-agent, enforcing least-privilege access.

        Args:
            jwt_token: The original user JWT (subject token)
            target_agent: Target agent identifier ("profile" or "accounts")

        Returns:
            The exchanged token string, or the original token if exchange fails
        """
        # Acquire OTEL tracer if available
        tracer = trace.get_tracer(__name__) if OTEL_AVAILABLE else None
        span = None
        if tracer:
            span = tracer.start_span("coordinator.token_exchange")
            span.set_attribute("coordinator.target_agent", target_agent)

        try:
            if not self.token_exchange_service:
                logger.warning(
                    json.dumps(
                        {
                            "event": "token_exchange_skipped",
                            "reason": "token_exchange_service not configured",
                            "target_agent": target_agent,
                            "session_id": self.session_id,
                        }
                    )
                )
                if span:
                    span.set_attribute("coordinator.exchange_result", "fallback")
                return jwt_token

            if not jwt_token:
                logger.warning(
                    json.dumps(
                        {
                            "event": "token_exchange_skipped",
                            "reason": "no access_token available",
                            "target_agent": target_agent,
                            "session_id": self.session_id,
                        }
                    )
                )
                if span:
                    span.set_attribute("coordinator.exchange_result", "fallback")
                return jwt_token

            try:
                request = TokenExchangeRequest(
                    subject_token=jwt_token,
                    audience=target_agent,
                )

                response = self.token_exchange_service.exchange_token(
                    request=request,
                    actor_id="coordinator-agent",
                )

                logger.info(
                    json.dumps(
                        {
                            "event": "token_exchange_success",
                            "target_agent": target_agent,
                            "exchange_id": response.exchange_id,
                            "original_scopes": response.original_scopes,
                            "granted_scopes": response.granted_scopes,
                            "removed_scopes": response.removed_scopes,
                            "token_lifetime": response.expires_in,
                            "session_id": self.session_id,
                        }
                    )
                )

                if span:
                    span.set_attribute("coordinator.exchange_result", "success")

                return response.access_token

            except TokenExchangeError as e:
                logger.warning(
                    json.dumps(
                        {
                            "event": "token_exchange_failed",
                            "target_agent": target_agent,
                            "error_type": type(e).__name__,
                            "error": e.error,
                            "error_description": e.error_description,
                            "fallback": "original_token",
                            "session_id": self.session_id,
                        }
                    )
                )
                if span:
                    span.set_attribute("coordinator.exchange_result", "fallback")
                return jwt_token

            except Exception as e:
                logger.warning(
                    json.dumps(
                        {
                            "event": "token_exchange_unexpected_error",
                            "target_agent": target_agent,
                            "error_type": type(e).__name__,
                            "error": str(e),
                            "fallback": "original_token",
                            "session_id": self.session_id,
                        }
                    )
                )
                if span:
                    span.set_attribute("coordinator.exchange_result", "fallback")
                return jwt_token

        except Exception:
            if span:
                span.record_exception(exception=__import__("sys").exc_info()[1])
                span.set_status(trace.StatusCode.ERROR)
            raise
        finally:
            if span:
                span.end()

    def _get_available_agents_response(self) -> str:
        """Return information about available agents."""
        return json.dumps(
            {
                "available_agents": ["profile", "accounts"],
                "message": "You can ask about your profile or accounts.",
            }
        )


def create_agent(
    session_id: str,
    user_context: Dict[str, Any],
    router: SubAgentRouter,
    token_exchange_service: Optional[TokenExchangeService] = None,
) -> CoordinatorAgent:
    """
    Factory function to create a CoordinatorAgent instance.

    Args:
        session_id: Unique identifier for the conversation session
        user_context: Authentication context containing user claims
        router: SubAgentRouter for routing to action agents
        token_exchange_service: RFC 8693 token exchange service for scope attenuation

    Returns:
        Configured CoordinatorAgent instance
    """
    return CoordinatorAgent(
        session_id=session_id,
        user_context=user_context,
        router=router,
        token_exchange_service=token_exchange_service,
    )
