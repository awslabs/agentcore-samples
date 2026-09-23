import os
import jwt
from typing import Optional, Dict, Any

# Bypass tool consent prompts for high-agency tools
os.environ["BYPASS_TOOL_CONSENT"] = "true"

from strands import Agent
from strands_tools import shell, file_read, file_write, editor
from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from .mcp_client.client import get_streamable_http_mcp_client
from .model.load import load_model

MEMORY_ID = os.getenv("BEDROCK_AGENTCORE_MEMORY_ID")
REGION = os.getenv("AWS_REGION")


def _get_bearer_token(context) -> Optional[str]:
    """Extract Bearer token from the Authorization header."""
    auth = (getattr(context, "request_headers", None) or {}).get("Authorization", "")
    return auth[7:] if auth.startswith("Bearer ") else None


def _decode_jwt(token: str) -> Dict[str, Any]:
    """Decode JWT claims. Token already validated by Runtime authorizer."""
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except jwt.exceptions.DecodeError:
        return {}


# Integrate with Bedrock AgentCore
app = BedrockAgentCoreApp()
log = app.logger


@app.entrypoint
async def invoke(payload, context):
    session_id = getattr(context, "session_id", "default-session")

    # Extract user identity from JWT claims
    user_token = _get_bearer_token(context)
    claims = _decode_jwt(user_token) if user_token else {}
    email = claims.get("email") or claims.get("username", "")
    groups = claims.get("cognito:groups", [])
    actor_id = claims.get("sub") or email or payload.get("user_id") or "default-user"
    log.info(f"User: {actor_id}, has_token: {user_token is not None}")

    # Configure memory if available
    session_manager = None
    if MEMORY_ID:
        session_manager = AgentCoreMemorySessionManager(
            AgentCoreMemoryConfig(
                memory_id=MEMORY_ID,
                session_id=session_id,
                actor_id=actor_id,
                retrieval_config={
                    "/facts/{actorId}/": RetrievalConfig(top_k=10, relevance_score=0.4),
                    "/preferences/{actorId}/": RetrievalConfig(top_k=5, relevance_score=0.5),
                    "/summaries/{actorId}/{sessionId}/": RetrievalConfig(top_k=5, relevance_score=0.4),
                    "/episodes/{actorId}/{sessionId}/": RetrievalConfig(top_k=5, relevance_score=0.4),
                },
            ),
            REGION,
        )
    else:
        log.warning("MEMORY_ID is not set. Skipping memory session manager initialization.")

    # High-agency tools from strands_tools
    high_agency_tools = [shell, file_read, file_write, editor]

    # Get MCP client - pass user token if available for scope-based Gateway authorization
    # Impersonation (forwarding user token) works here because the Gateway is within our/organization trust boundary.
    # For third-party APIs, use delegation via AgentCore Identity instead(skipped to keep demo short):
    # https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-getting-started-google.html
    strands_mcp_client = get_streamable_http_mcp_client(user_token=user_token)

    with strands_mcp_client as client:
        # Get MCP Tools
        tools = client.list_tools_sync()

        # Create agent
        agent = Agent(
            model=load_model(),
            session_manager=session_manager,
            system_prompt=(
                "You are a customer support agent. Your role is to answer customer questions "
                "about orders, account information, and refund requests.\n\n"
                f"Current user:\n"
                f"- Email: {email or 'unknown'}\n"
                f"- Groups: {', '.join(groups) if groups else 'none'}\n\n"
                "Guidelines:\n"
                "- Use the customer's email to look up their account and orders automatically\n"
                "- When showing orders, always fetch full order details (get_order) to include "
                "item names, quantities, and prices — not just order IDs and totals\n"
                "- Summarize information clearly and concisely for the customer\n\n"
                "Policy enforcement:\n"
                "- If a tool call is denied due to a policy violation, report the denial exactly "
                "as returned by the system. Do NOT invent or guess the reason — only state what "
                "the policy engine actually returned. Do NOT retry the same request.\n\n"
                "When relevant memories are retrieved from previous sessions:\n"
                "- Use stored preferences to personalize recommendations and responses\n"
                "- Reference past facts about the customer to provide continuity\n"
                "- Build on previous session summaries to maintain context across conversations\n"
                "- Acknowledge returning customers and their history when appropriate"
            ),
            tools=high_agency_tools + tools,
        )

        # Execute and format response
        stream = agent.stream_async(payload.get("prompt"))

        async for event in stream:
            if "data" in event and isinstance(event["data"], str):
                yield event["data"]


def format_response(result) -> str:
    """Extract code from metrics and format with LLM response."""
    parts = []

    # Extract executed code from metrics
    try:
        tool_metrics = result.metrics.tool_metrics.get("code_interpreter")
        if tool_metrics and hasattr(tool_metrics, "tool"):
            action = tool_metrics.tool["input"]["code_interpreter_input"]["action"]
            if "code" in action:
                parts.append(f"## Executed Code:\n```{action.get('language', 'python')}\n{action['code']}\n```\n---\n")
    except (AttributeError, KeyError):
        pass  # No code to extract

    # Add LLM response
    parts.append(f"## 📊 Result:\n{str(result)}")
    return "\n".join(parts)


if __name__ == "__main__":
    app.run()
