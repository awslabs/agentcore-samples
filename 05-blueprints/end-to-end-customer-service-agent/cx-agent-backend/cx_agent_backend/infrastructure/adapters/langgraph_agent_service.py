"""LangGraph implementation of agent service."""

import json
import logging
import os

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langchain_aws import ChatBedrock
from bedrock_agentcore.memory import MemoryClient

from cx_agent_backend.domain.entities.conversation import Message, MessageRole
from cx_agent_backend.domain.services.agent_service import (
    AgentRequest,
    AgentResponse,
    AgentService,
    AgentType,
)
from cx_agent_backend.domain.services.guardrail_service import (
    GuardrailAssessment,
    GuardrailService,
)
from cx_agent_backend.domain.services.llm_service import LLMService
from cx_agent_backend.infrastructure.adapters.tools import tools
from cx_agent_backend.infrastructure.aws.parameter_store_reader import (
    AWSParameterStoreReader,
)
from cx_agent_backend.infrastructure.aws.secret_reader import AWSSecretsReader

try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    import requests

    GATEWAY_AVAILABLE = True
except ImportError:
    GATEWAY_AVAILABLE = False

logger = logging.getLogger(__name__)


parameter_store_reader = AWSParameterStoreReader()
secret_reader = AWSSecretsReader()


class LangGraphAgentService(AgentService):
    """LangGraph implementation of agent service."""

    def __init__(
        self,
        guardrail_service: GuardrailService | None = None,
        llm_service: LLMService | None = None,
    ):
        self._guardrail_service = guardrail_service
        self._llm_service = llm_service

    async def _create_gateway_tool(self):
        """Create a wrapper tool that manages its own MCP session."""

        @tool
        async def tavily_search(query: str) -> str:
            """Search the web using Tavily API via gateway"""
            try:
                # Get gateway URL and credentials (decrypt encrypted parameters)
                gateway_url = parameter_store_reader.get_parameter(
                    "/amazon/gateway_url", decrypt=True
                )
                client_id = parameter_store_reader.get_parameter(
                    "/cognito/client_id", decrypt=True
                )
                client_secret = secret_reader.read_secret("cognito_client_secret")
                token_url = parameter_store_reader.get_parameter(
                    "/cognito/oauth_token_url", decrypt=True
                )

                if not all([gateway_url, client_id, client_secret, token_url]):
                    return "Gateway not configured properly"

                # Get access token
                token_response = requests.post(
                    token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=20,
                )

                if token_response.status_code != 200:
                    return f"Failed to get access token: {token_response.text}"

                access_token = token_response.json().get("access_token")
                if not access_token:
                    return "No access token received"

                # Use MCP session for this single call
                async with streamablehttp_client(
                    gateway_url, headers={"Authorization": f"Bearer {access_token}"}
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()

                        # List available tools first
                        tools_list = await session.list_tools()
                        logger.info(
                            f"Available MCP tools: {[tool.name for tool in tools_list.tools]}"
                        )

                        # Find the correct tool name
                        tavily_tool = None
                        for tool in tools_list.tools:
                            if "tavily" in tool.name.lower():
                                tavily_tool = tool
                                break

                        if not tavily_tool:
                            return "Tavily search tool not found in gateway"

                        logger.info(f"Using tool: {tavily_tool.name}")

                        # Call the tool with proper name
                        result = await session.call_tool(
                            tavily_tool.name, {"query": query}
                        )
                        logger.info(f"MCP tool result: {result}")

                        # Handle different result types
                        if hasattr(result, "content"):
                            if isinstance(result.content, list):
                                return "\n".join(
                                    str(item.text)
                                    if hasattr(item, "text")
                                    else str(item)
                                    for item in result.content
                                )
                            else:
                                return str(result.content)
                        else:
                            return str(result)

            except requests.exceptions.RequestException as e:
                logger.error(f"HTTP request error: {e}")
                return f"Network error during authentication: {str(e)}"
            except Exception as e:
                logger.error(f"Gateway tool error: {e}", exc_info=True)
                return f"Web search failed: {str(e)}"

        return tavily_search

    async def _get_gateway_tools(self, user_jwt_token: str = None):
        """Get gateway tools using wrapper approach."""
        if not GATEWAY_AVAILABLE:
            logger.warning("Gateway not available")
            return []

        try:
            gateway_tool = await self._create_gateway_tool()
            return [gateway_tool]
        except Exception as e:
            logger.warning(f"Failed to create gateway tools: {e}")
            return []

    async def _create_agent(
        self,
        agent_type: AgentType,
        model: str,
        memory_id: str = None,
        actor_id: str = None,
        session_id: str = None,
        user_jwt_token: str = None,
    ) -> any:
        """Create agent with specific model."""
        # Use ChatBedrock directly
        llm = ChatBedrock(
            model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            temperature=0.7,
        )

        # Add memory tool if memory parameters provided
        memory_tools = []
        memory_client = None
        if memory_id and actor_id and session_id:
            memory_client = MemoryClient(
                region_name=os.getenv("AWS_REGION", "us-east-1")
            )

            @tool
            def get_conversation_history():
                """Retrieve recent conversation history when needed for context"""
                try:
                    events = memory_client.list_events(
                        memory_id=memory_id,
                        actor_id=actor_id,
                        session_id=session_id,
                        max_results=10,
                    )
                    return f"Recent conversation history: {events}"
                except Exception as e:
                    return f"Could not retrieve history: {str(e)}"

            memory_tools = [get_conversation_history]

        system_message = (
            "You are a professional customer service agent for AnyCompany. Your goal is to provide accurate, helpful responses while following company protocols.\n\n"
            "TOOL USAGE STRATEGY:\n"
            "1. For COMPANY-RELATED queries (products, services, policies, procedures, support): Use retrieve_context to search our knowledge base\n"
            "2. For GENERIC queries (general information, current events, how-to guides): Use tavily_search via gateway\n"
            "3. If retrieve_context returns no results or insufficient information, fallback to tavily_search\n"
            "4. For ticket requests: Use create_support_ticket with complete details\n"
            "5. For ticket status: Use get_support_tickets\n\n"
            "DO NOT use both retrieve_context and tavily_search for the same query - choose the most appropriate tool based on the query type.\n\n"
            "RESPONSE GUIDELINES:\n"
            "- Be concise but thorough in explanations\n"
            "- Always cite sources when using knowledge base or web information\n"
            "- For ticket creation, gather: subject, description, priority, and contact info\n"
            "- If knowledge base has no relevant information, clearly state this and use web search\n"
            "- Maintain a professional, empathetic tone throughout interactions"
        )

        # Get gateway tools for this request using user's JWT token
        gateway_tools = await self._get_gateway_tools(user_jwt_token)

        # Log gateway tools for debugging
        logger.info("=== GATEWAY TOOLS DEBUG ===")
        for gateway_tool in gateway_tools:
            logger.info(f"Tool name: {gateway_tool.name}")
            logger.info(f"Tool description: {gateway_tool.description}")
            logger.info(f"Tool args_schema: {gateway_tool.args_schema}")
            if hasattr(gateway_tool, "func"):
                logger.info(f"Tool func: {gateway_tool.func}")
        logger.info("=== END GATEWAY TOOLS DEBUG ===")

        # Combine existing tools with memory tools and gateway tools
        all_tools = tools + memory_tools + gateway_tools
        # all_tools = gateway_tools

        return create_react_agent(
            llm, tools=all_tools, prompt=system_message
        ), memory_client

    async def process_request(self, request: AgentRequest) -> AgentResponse:
        """Process request through appropriate agent."""
        logger.info(
            "Processing request for user %s, session %s, agent type %s",
            request.user_id,
            request.session_id,
            request.agent_type,
        )

        # Check input guardrails if enabled
        if self._guardrail_service and request.messages:
            last_user_message = None
            for msg in reversed(request.messages):
                if msg.role == MessageRole.USER:
                    last_user_message = msg
                    break

            if last_user_message:
                input_result = await self._guardrail_service.check_input(
                    last_user_message
                )
                if input_result.assessment == GuardrailAssessment.BLOCKED:
                    return AgentResponse(
                        content=input_result.message,
                        agent_type=request.agent_type,
                        tools_used=[],
                        metadata={
                            "blocked_categories": ",".join(
                                input_result.blocked_categories
                            )
                        },
                        trace_id=None,
                    )

        # Get memory parameters from environment or request
        stm_memory_id = parameter_store_reader.get_parameter(
            "/amazon/ac_stm_memory_id", decrypt=True
        )
        if not stm_memory_id:
            logger.error("STM Memory ID not configured in parameter store")
            raise ValueError("STM Memory ID not configured")

        actor_id = request.user_id
        session_id = request.session_id

        # Extract user JWT token from request
        user_jwt_token = request.jwt_token

        agent, memory_client = await self._create_agent(
            request.agent_type,
            request.model,
            stm_memory_id,
            actor_id,
            session_id,
            user_jwt_token,
        )

        # Convert domain messages to LangChain format
        lc_messages = []
        for msg in request.messages:
            if msg.role == MessageRole.USER:
                lc_messages.append(HumanMessage(content=msg.content))
            elif msg.role == MessageRole.ASSISTANT:
                lc_messages.append(AIMessage(content=msg.content))

        # Create config
        config = RunnableConfig(
            configurable={
                "thread_id": f"{request.session_id}",
                "user_id": request.user_id,
            },
        )

        # Invoke agent with recursion limit
        logger.debug("Invoking agent with %s messages", len(lc_messages))
        response = await agent.ainvoke({"messages": lc_messages}, config)

        # Save conversation to memory if available
        if memory_client and lc_messages:
            try:
                last_user_msg = next(
                    (
                        msg.content
                        for msg in reversed(lc_messages)
                        if isinstance(msg, HumanMessage)
                    ),
                    None,
                )
                assistant_response = (
                    response["messages"][-1].content if response["messages"] else ""
                )

                if last_user_msg and assistant_response:
                    memory_client.create_event(
                        memory_id=stm_memory_id,
                        actor_id=actor_id,
                        session_id=session_id,
                        messages=[
                            (last_user_msg, "USER"),
                            (assistant_response, "ASSISTANT"),
                        ],
                    )
            except Exception as e:
                logger.warning(f"Failed to save conversation to memory: {e}")
        # Extract response
        last_message = response["messages"][-1]
        tools_used = []
        citations = []
        knowledge_base_id = None

        # Check all messages for tool usage and extract citations

        message_types = []
        for msg in response["messages"]:
            message_types.append(type(msg).__name__)

            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls"):
                if msg.tool_calls:
                    for tool_call in msg.tool_calls:
                        logger.info("=== TOOL CALL DEBUG ===")
                        logger.info(f"Tool call name: {tool_call.get('name')}")
                        logger.info(f"Tool call args: {tool_call.get('args')}")
                        logger.info(f"Full tool call: {tool_call}")
                        logger.info("=== END TOOL CALL DEBUG ===")
                        tools_used.append(tool_call["name"])

            # Extract citations from ToolMessage responses
            from langchain_core.messages import ToolMessage

            if isinstance(msg, ToolMessage):
                logger.info("=== TOOL RESPONSE DEBUG ===")
                logger.info(f"Tool message name: {getattr(msg, 'name', 'Unknown')}")
                logger.info(f"Tool message content: {msg.content}")
                logger.info(f"Tool message type: {type(msg.content)}")
                logger.info("=== END TOOL RESPONSE DEBUG ===")
                try:
                    # Parse tool response content
                    if isinstance(msg.content, str):
                        tool_response = json.loads(msg.content)
                        if "citations" in tool_response:
                            citations.extend(tool_response["citations"])
                        if "knowledge_base_id" in tool_response:
                            knowledge_base_id = tool_response["knowledge_base_id"]
                except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                    logger.debug(
                        "Failed to parse tool message content for citations; "
                        "content=%r; error=%s",
                        msg.content,
                        exc,
                    )

        # Remove duplicates
        tools_used = list(set(tools_used))

        # Check output guardrails if enabled
        if self._guardrail_service:
            output_message = Message.create_assistant_message(last_message.content)
            output_result = await self._guardrail_service.check_output(output_message)
            if output_result.assessment == GuardrailAssessment.BLOCKED:
                return AgentResponse(
                    content=output_result.message,
                    agent_type=request.agent_type,
                    tools_used=tools_used,
                    metadata={
                        "blocked_categories": ",".join(output_result.blocked_categories)
                    },
                    trace_id=None,
                )

        # Add trace metadata
        metadata = {
            "model": request.model,
            "agent_type": request.agent_type.value,
        }

        # Add citations to metadata if available
        if citations:
            metadata["citations"] = citations
        if knowledge_base_id:
            metadata["knowledge_base_id"] = knowledge_base_id
        return AgentResponse(
            content=last_message.content,
            agent_type=request.agent_type,
            tools_used=tools_used,
            metadata=metadata,
            trace_id=None,
        )

    async def stream_response(self, request: AgentRequest):
        """Stream response from agent."""
        agent, _ = self._create_agent(request.agent_type, request.model)

        # Convert domain messages to LangChain format
        lc_messages = []
        for msg in request.messages:
            if msg.role == MessageRole.USER:
                lc_messages.append(HumanMessage(content=msg.content))
            elif msg.role == MessageRole.ASSISTANT:
                lc_messages.append(AIMessage(content=msg.content))

        # Create config
        config = RunnableConfig(
            configurable={
                "thread_id": f"{request.session_id}",
                "user_id": request.user_id,
            }
        )

        # Stream agent response
        async for chunk in agent.astream({"messages": lc_messages}, config=config):
            if "messages" in chunk:
                for message in chunk["messages"]:
                    if hasattr(message, "content") and message.content:
                        yield message.content
