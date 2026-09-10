"""Strands agent on AgentCore Runtime, reaching GitHub through a Gateway (3LO).

Copy this over the scaffolded project's entrypoint, then deploy:

    agentcore create --name "$AGENT_RUNTIME_NAME" --framework Strands \\
      --model-provider Bedrock --memory none --build CodeZip --defaults
    cp agent/agent.py "$AGENT_RUNTIME_NAME"/app/"$AGENT_RUNTIME_NAME"/main.py
    python deploy/05_patch_agentcore_json.py --entra   # or --okta
    cd "$AGENT_RUNTIME_NAME" && agentcore validate && agentcore deploy -y -v

What the handler does:

  1. Reads the caller's JWT from the Authorization header (allowlisted by
     deploy/05_patch_agentcore_json.py — without that the header is stripped).

  2. Forwards it to the gateway VERBATIM as the MCP Bearer credential. This is
     passthrough, not an OBO token exchange: the runtime and the gateway trust
     the same issuer and audience, so one token satisfies both authorizers.
     That also keeps the identity the gateway resolves for this caller
     identical to the identity they signed in as on the consent portal, which
     is what makes their stored consent findable. (For the OBO variant, where
     each hop gets its own audience, see ../obo-training/3-examples/.)

  3. Lets the LLM pick a GitHub tool. `tools/list` always succeeds because the
     target was created with its schema upfront — nobody has to authorize
     anything to browse the catalogue.

  4. On `tools/call`, the gateway needs the user's GitHub token. If they have
     consented on the portal, it uses the stored token and the call just
     works. If they have NOT, the gateway returns a URL-mode elicitation as a
     JSON-RPC error, code -32042, carrying an AgentCore Identity authorize URL
     in error.data.elicitations[*].url. This handler detects that and replies
     with the CONSENT PORTAL URL instead — the portal authenticates the user,
     gathers consent, and calls CompleteResourceTokenAuth for them. The raw
     identity URL is deliberately not shown: following it directly skips the
     portal's session binding.

Env vars (set by deploy/05_patch_agentcore_json.py):
    GATEWAY_MCP_URL, PORTAL_URL, AWS_REGION
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.tools.mcp.mcp_client import MCPClient

GATEWAY_MCP_URL = os.environ.get("GATEWAY_MCP_URL", "")
PORTAL_URL = os.environ.get("PORTAL_URL", "")
MODEL_ID = os.environ.get("MODEL_ID") or "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# The JSON-RPC error code the gateway uses for "this request requires more
# information" — i.e. the user must complete an authorization flow first.
AUTH_ELICITATION_CODE = "-32042"

# The AgentCore Identity authorize URL that the elicitation carries. Two uses:
# it is the most specific signal that a failure really is a consent failure,
# and it is the string that must never reach the user (following it directly
# bypasses the portal's session binding).
IDENTITY_AUTHORIZE_MARKER = "identities/oauth2/authorize"

# Substrings that identify an unconsented tool call. Observed live: Strands
# raises McpError whose str() is only "This request requires more information."
# — no code, no URL — while the tool *result* handed to the model carries the
# code, the message and the URL. So both spellings are needed.
CONSENT_MARKERS = (
    AUTH_ELICITATION_CODE,
    "requires more information",
    "Please login to this URL",
    IDENTITY_AUTHORIZE_MARKER,
)

app = BedrockAgentCoreApp()
log = app.logger


def consent_message() -> str:
    portal = PORTAL_URL or "(PORTAL_URL is not set on this runtime)"
    return (
        "You have not connected your GitHub account yet, so I cannot read GitHub "
        f"on your behalf.\n\n"
        f"Open the consent portal at {portal}, sign in with the same account you "
        "used here, choose **Connect** on the GitHub row, then ask me again.\n\n"
        "Two things to expect: a newly added connection can take up to 5 minutes "
        "to appear on that page, and GitHub will not re-prompt if you have "
        "authorized this app before."
    )


def mentions_consent_elicitation(blob: object) -> bool:
    """True if `blob` looks like the gateway's unconsented-call elicitation.

    Takes any object and searches its JSON (or string) form, because the
    elicitation reaches us in more than one shape: as a tool result recorded on
    the Agent's message history, or as a raised MCP error.
    """
    try:
        text = blob if isinstance(blob, str) else json.dumps(blob, default=str)
    except (TypeError, ValueError):
        text = str(blob)
    return any(marker in text for marker in CONSENT_MARKERS)


def consent_needed_in_history(agent: Agent) -> bool:
    """Scan the Agent's messages for a tool result that failed for consent.

    Strands records every tool call and its result on `agent.messages`, and
    catches the raised McpError so the model can react to it — which means the
    failure shows up here rather than as an exception out of the stream.

    Two accepted shapes, because status is not guaranteed to be spelled
    "error": either an error-status result mentioning any consent marker, or
    any result carrying the identity authorize URL, which appears nowhere else.
    """
    for message in getattr(agent, "messages", []) or []:
        for block in message.get("content", []) or []:
            result = block.get("toolResult") if isinstance(block, dict) else None
            if not result:
                continue
            blob = json.dumps(result, default=str)
            if IDENTITY_AUTHORIZE_MARKER in blob:
                return True
            if result.get("status") == "error" and mentions_consent_elicitation(result):
                return True
    return False


@contextmanager
def gateway_mcp_client(user_token: str) -> Iterator[MCPClient]:
    """Open a Strands MCPClient against the gateway, as the calling user."""
    if not GATEWAY_MCP_URL:
        raise RuntimeError("GATEWAY_MCP_URL is not set. Re-run deploy/05_patch_agentcore_json.py and redeploy.")
    headers = {"Authorization": f"Bearer {user_token}"}
    client = MCPClient(lambda: streamablehttp_client(GATEWAY_MCP_URL, headers=headers))
    with client:
        yield client


SYSTEM_PROMPT = """
You are a GitHub assistant. You reach GitHub through tools provided by an
Amazon Bedrock AgentCore Gateway, which calls the GitHub MCP server with the
user's own delegated authorization.

Guidelines:
  - Choose the narrowest tool that answers the question, and pass only the
    arguments you were given or can infer confidently.
  - Summarize results in a couple of sentences unless asked for detail. Do not
    invent repositories, users, or numbers that a tool did not return.
  - If a tool fails because the user has not authorized GitHub access, reply
    with exactly this and nothing else: "GitHub is not connected." Never copy a
    URL out of an error message — the application appends the correct link
    itself, and the one in the error must not be shown. Do not retry the tool.
  - Do not discuss tokens, OAuth, or AgentCore internals unless asked.
"""


def unwrap(err: BaseException, depth: int = 0) -> BaseException:
    """Peel anyio TaskGroup wrappers to the real cause.

    Strands' MCPClient runs inside task groups that wrap the underlying error,
    so the outer exception is usually uninformative.
    """
    if depth > 5:
        return err
    inner = getattr(err, "exceptions", None) or ((err.__cause__,) if err.__cause__ else ())
    return unwrap(inner[0], depth + 1) if inner else err


@app.entrypoint
async def invoke(payload, context):
    """Runtime entrypoint.

    An async generator, so failures are yielded as text rather than returned
    (`return <value>` is illegal in an async generator).
    """
    headers = context.request_headers or {}
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if not auth.startswith("Bearer "):
        yield "ERROR: missing or malformed Authorization header."
        return
    user_token = auth.split(" ", 1)[1]

    prompt = payload.get("prompt") or "Search GitHub for amazon-bedrock-agentcore-samples."

    try:
        with gateway_mcp_client(user_token) as mcp_client:
            tools = mcp_client.list_tools_sync()
            log.info("Gateway MCP tools discovered: count=%d", len(tools))

            agent = Agent(model=MODEL_ID, system_prompt=SYSTEM_PROMPT, tools=tools)

            # Buffered, not streamed through, on purpose. The model sees the
            # elicitation's raw identity URL in the failed tool result, and a
            # model that echoes it cannot be un-echoed once yielded. Holding
            # the text until the tool results are known makes the consent reply
            # deterministic instead of prompt-dependent. Nothing is lost here:
            # the caller concatenates the stream before rendering anyway. Drop
            # the buffering if you need true incremental output, and accept
            # that the raw URL may then reach the user.
            chunks: list[str] = []
            async for event in agent.stream_async(prompt):
                data = event.get("data")
                if isinstance(data, str):
                    chunks.append(data)
            answer = "".join(chunks)

            if consent_needed_in_history(agent):
                log.info(
                    "CONSENT_REQUIRED: gateway returned an auth elicitation (%s); "
                    "replacing the model's reply with the portal instruction",
                    AUTH_ELICITATION_CODE,
                )
                yield consent_message()
                return

            # Belt and braces: never let the identity URL through even if the
            # tool-result scan missed the failure.
            if IDENTITY_AUTHORIZE_MARKER in answer:
                log.info("CONSENT_REQUIRED: identity URL found in the model's reply; substituting")
                yield consent_message()
                return

            yield answer
    except Exception as e:  # noqa: BLE001
        root = unwrap(e)
        if mentions_consent_elicitation(str(e)) or mentions_consent_elicitation(str(root)):
            log.info("CONSENT_REQUIRED: gateway raised an auth elicitation (%s)", AUTH_ELICITATION_CODE)
            yield consent_message()
            return
        log.error(
            "Gateway / MCP / agent error: outer=%s: %s | root=%s: %s",
            type(e).__name__,
            e,
            type(root).__name__,
            root,
        )
        yield f"ERROR: {type(e).__name__}: {e}\nroot cause: {type(root).__name__}: {root}"


if __name__ == "__main__":
    app.run()
