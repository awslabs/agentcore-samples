"""XAA requesting app: a Strands agent hosted on Amazon Bedrock AgentCore Runtime.

Inbound auth (AgentCore Identity):
    The runtime is configured with a JWT authorizer that trusts your Okta org.
    Callers invoke the runtime with the user's Okta ID token as the bearer.
    AgentCore validates the token (issuer/audience) and binds the session to
    the user before this code runs.

On-behalf-of access (XAA):
    The Okta ID token reaches this code either via the allow-listed Authorization
    header (if the runtime forwards it) or in the invoke payload as `id_token`.
    Tools exchange it for a resource access token using the two-leg ID-JAG flow
    (see xaa_client.py) and call the todo API.
"""

from __future__ import annotations

import json
import logging
import os

import todo_tools
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from strands import Agent
from strands.models import BedrockModel

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

SYSTEM_PROMPT = (
    "You are a helpful todo assistant. You can list the user's todos, add new "
    "todos, and mark todos complete by calling the provided tools. When the "
    "user asks to complete a task, first list todos to find the matching id if "
    "you don't already have it. Be concise."
)


def _extract_id_token(payload: dict, context: RequestContext) -> str | None:
    """Get the user's Okta ID token from the Authorization header or the payload.

    The header path requires the runtime to allow-list `Authorization`; the
    payload path (`id_token`) works regardless and matches the AWS reference /
    the `agentcore` CLI invoke style.
    """
    headers = getattr(context, "request_headers", None) or {}
    auth = headers.get("Authorization") or headers.get("authorization")
    if auth:
        return auth.removeprefix("Bearer ")
    token = payload.get("id_token")
    if token:
        return token.removeprefix("Bearer ")
    return None


def _create_agent() -> Agent:
    model = BedrockModel(
        model_id=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
        temperature=0.1,
    )
    return Agent(
        name="xaa_todo_agent",
        system_prompt=SYSTEM_PROMPT,
        model=model,
        tools=[todo_tools.list_todos, todo_tools.add_todo, todo_tools.complete_todo],
    )


@app.entrypoint
async def invocations(payload, context: RequestContext):
    """Called by AgentCore Runtime on each request."""
    user_query = payload.get("prompt")
    if not user_query:
        yield {"status": "error", "error": "Missing required field: prompt"}
        return

    id_token = _extract_id_token(payload, context)
    if not id_token:
        yield {
            "status": "error",
            "error": "No Okta ID token found. Invoke with the user's ID token as the "
            "Authorization bearer (allow-listed on the runtime) or pass it in the "
            "payload as 'id_token'.",
        }
        return

    token_ctx = todo_tools.current_id_token.set(id_token)
    try:
        agent = _create_agent()
        async for event in agent.stream_async(user_query):
            yield json.loads(json.dumps(dict(event), default=str))
    except Exception:
        # Log the full exception server-side, but return a generic message so
        # internal details (tokens, endpoints, stack context) are not leaked to
        # the caller.
        logger.exception("Agent run failed")
        yield {"status": "error", "error": "Internal error while processing the request."}
    finally:
        todo_tools.current_id_token.reset(token_ctx)


if __name__ == "__main__":
    app.run()
