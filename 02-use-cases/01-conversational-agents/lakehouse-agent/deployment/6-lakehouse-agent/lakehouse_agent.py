#!/usr/bin/env python3
"""
Health Lakehouse Data Agent using Strands and AgentCore Gateway
Connects to Gateway tools for querying and managing lakehouse data with OAuth-based access control
"""

import logging
import os
from typing import Any, Optional

import boto3
from bedrock_agentcore import BedrockAgentCoreApp
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bypass tool consent for AgentCore deployment
os.environ["BYPASS_TOOL_CONSENT"] = "true"

# Initialize AgentCore App
app = BedrockAgentCoreApp()

# System prompt for lakehouse data agent
CLAIMS_SYSTEM_PROMPT = """
You are a helpful lakehouse data assistant that provides tools to help users query and update data in the lakehouse.

**Technical Context**:

You have access to tools that query the data lakehouse and surrounding data stores like DynamoDB.
Users can access tools and data based on their groups.

**Special instruction for admin group users**
For admin group users, they might encounter tool access issue. Retry with text-to-sql tool provided in case a specific tool fails.

**Two kinds of claim data — pick the right tool:**

You have two qualitatively different sources of claim information, each behind
its own tool group:
- **Structured claim records** (claim IDs, status, amounts, dates, approvals,
  counts, summaries) come from the `claims/*` tools backed by the lakehouse.
  Use these for facts and figures about claims.
- **Free-text claim notes** (adjuster narratives, damage descriptions, call
  summaries, written explanations) come from the `notes/*` `search_claim_notes`
  tool backed by OpenSearch. Use this for natural-language questions about what
  was *written* or *described* about a claim, or to find claims by keywords in
  their narrative text.

Choose based on whether the user wants structured fields (claims tools) or
narrative text (notes search). Both tool groups are automatically scoped to the
signed-in user's identity; you never pass a user identifier yourself.

**Communication Guidelines**:

Be professional, empathetic, and clear
Explain insurance terms in simple language
When helping with claims, gather all necessary information before submission
If you are working for administrators group, try to use text_to_sql tool in case the user do not have access to specific tools. 

**DO NOT MAKE UP ANSWERS. YOUR RESPONSES SHOULD BE BASED ON SOLID FACTS ONLY. DO NOT ANSWER WHEN YOU DO NOT KNOW**
"""

# Default model ID
MODEL_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"


def get_config() -> dict[str, str | None]:
    """
    Load configuration from environment variables and SSM Parameter Store.

    Priority:
    1. Environment variables (set by AgentCore Runtime)
    2. SSM Parameter Store
    3. Defaults

    Returns:
        Dictionary with configuration values
    """
    config = {}

    # Get region from boto3 session with proper fallback
    try:
        session = boto3.Session()
        config["region"] = (
            os.environ.get("AWS_REGION") or session.region_name or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        )
        if not session.region_name:
            logger.warning("⚠️  No region in AWS config, using fallback")
        logger.info(f"✅ Region: {config['region']}")
    except Exception as e:
        logger.warning(f"⚠️  Could not detect region: {e}")
        config["region"] = "us-east-1"

    # Try to get Gateway ARN from environment variable first
    config["gateway_arn"] = os.environ.get("GATEWAY_ARN")

    # If not in environment, try SSM Parameter Store
    if not config["gateway_arn"]:
        try:
            ssm = boto3.client("ssm", region_name=config["region"])
            response = ssm.get_parameter(Name="/app/lakehouse-agent/gateway-arn")
            config["gateway_arn"] = response["Parameter"]["Value"]
            logger.info(f"✅ Gateway ARN from SSM: {config['gateway_arn']}")
        except Exception as e:
            logger.warning(f"⚠️  Gateway ARN not found in SSM: {e}")
            config["gateway_arn"] = None
    else:
        logger.info(f"✅ Gateway ARN from environment: {config['gateway_arn']}")

    # GW2 (notes / OpenSearch) URL — REQUIRED (R6/R10). Read from env or SSM
    # `notes-gateway-url` (written by 5b/04 on BOTH IdP paths — Okta OBO gateway
    # and Cognito notes interceptor gateway share the GW2 SSM keys, so this is
    # IdP-agnostic). GW2 is mandatory: the agent must expose notes/* alongside
    # claims/*, so a missing URL is a hard error surfaced at connect time (no
    # silent claims-only degradation).
    config["obo_gateway_url"] = os.environ.get("OBO_GATEWAY_URL")
    if not config["obo_gateway_url"]:
        try:
            ssm = boto3.client("ssm", region_name=config["region"])
            response = ssm.get_parameter(Name="/app/lakehouse-agent/notes-gateway-url")
            config["obo_gateway_url"] = response["Parameter"]["Value"]
            logger.info(f"✅ GW2 (notes) Gateway URL from SSM: {config['obo_gateway_url']}")
        except Exception as e:
            logger.error(f"❌ GW2 (notes) Gateway URL not found in SSM (required): {e}")
            config["obo_gateway_url"] = None
    else:
        logger.info(f"✅ GW2 (notes) Gateway URL from environment: {config['obo_gateway_url']}")

    return config


def get_gateway_url(gateway_arn: str, region: str) -> str:
    """Convert Gateway ARN to URL using AgentCore API."""
    try:
        # Extract gateway ID from ARN
        # Format: arn:aws:bedrock-agentcore:region:account:gateway/gateway-id
        gateway_id = gateway_arn.split("/")[-1]

        # Get gateway details
        agentcore_client = boto3.client("bedrock-agentcore-control", region_name=region)
        response = agentcore_client.get_gateway(gatewayIdentifier=gateway_id)
        gateway_url = response["gatewayUrl"]

        logger.info(f"✅ Gateway URL: {gateway_url}")
        return gateway_url
    except Exception as e:
        logger.error(f"❌ Error getting gateway URL: {e}")
        return ""


def _extract_tool_events(agent_obj) -> list:
    """Return the tool names actually invoked this turn, in call order (with
    duplicates), read from the Strands message log.

    This is the REAL event log — each assistant `toolUse` content block records a
    tool the model actually called — NOT LLM self-narration. Tools reach the model
    namespaced by their MCPClient prefix (e.g. `claims___get_claims_summary`); we
    strip the namespace to the bare tool name for display. Fail-soft: any parsing
    hiccup yields an empty list so the response is never broken by this add-on.
    """
    events = []
    try:
        for msg in getattr(agent_obj, "messages", None) or []:
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            for block in msg.get("content", []) or []:
                if not isinstance(block, dict):
                    continue
                tu = block.get("toolUse")
                if tu and tu.get("name"):
                    # Strip any namespace prefix (gateway/client uses `___`).
                    events.append(str(tu["name"]).split("___")[-1])
    except Exception as e:  # never let telemetry extraction break the answer
        logger.warning(f"⚠️  Could not extract tool events: {e}")
    return events


def _looks_like_jwt(value: str) -> bool:
    """Return True if `value` has the three dot-separated segments of a JWS.

    Deliberately a SHAPE check, not a validation: the AgentCore Runtime authorizer
    has already validated signature, issuer, audience and expiry before any request
    reaches this code. What this guards against is a value that is not a bearer
    token at all.

    Why it is needed: the runtime forwards `Authorization` to the container whenever
    `requestHeaderAllowlist` includes it, and that allowlist is set unconditionally —
    including on the fallback path where no JWT authorizer is configured and the
    runtime uses IAM auth instead. On that path `Authorization` carries a SigV4
    signature, and nothing upstream distinguishes the two: the SDK does not treat
    `Authorization` as restricted, and normalises it into the request context before
    any allowlist rule is consulted. Without this check a SigV4 credential string
    would be forwarded to the gateways as if it were a user bearer.
    """
    if not value:
        return False
    parts = value.split(".")
    return len(parts) == 3 and all(parts)


def _extract_bearer_token(payload: dict[str, Any], context: Any) -> str:
    """Resolve the caller's bearer token, preferring the inbound Authorization header.

    Header-first, because the runtime is configured for JWT auth on both identity
    providers, so a validated `Authorization` header is present on every request that
    reaches this code. The payload field is a transitional fallback: it logs loudly
    when it fires, which is what makes an unknown caller observable instead of
    arguable.
    """
    header_value = ""
    request_headers = getattr(context, "request_headers", None) if context is not None else None
    if request_headers:
        raw = request_headers.get("Authorization") or request_headers.get("authorization") or ""
        if raw.lower().startswith("bearer "):
            header_value = raw[7:].strip()
        else:
            header_value = raw.strip()

    if header_value:
        if _looks_like_jwt(header_value):
            logger.info("🔑 Bearer token source: Authorization header")
            return header_value
        # Not a JWT: almost certainly a SigV4 signature from an IAM-authorized
        # runtime. Fall through to the payload so this path keeps failing the way it
        # failed before the header was read, rather than in a new way.
        logger.warning(
            "⚠️  Authorization header is present but is not JWT-shaped "
            "(expected three dot-separated segments); ignoring it. This is expected "
            "when the runtime has no JWT authorizer configured and is using IAM auth."
        )

    payload_token = payload.get("bearer_token", "") or ""
    if payload_token:
        logger.warning(
            "⚠️  Falling back to the payload bearer token — no usable Authorization "
            "header was forwarded. This fallback is transitional: if this line "
            "appears, a caller is still sending the token in the request body."
        )
        return payload_token

    if context is None:
        # The SDK passes a context object only when the entrypoint's second
        # parameter is named exactly `context`; otherwise it invokes the handler
        # with the payload alone and this argument defaults to None. That failure is
        # silent, so name it explicitly rather than letting it look like a caller
        # problem.
        logger.error(
            "❌ No request context was supplied. If the entrypoint signature was "
            "changed, the second parameter must be named exactly 'context' or the "
            "runtime will not pass request headers at all."
        )
    return ""


@app.entrypoint
def handle_request(payload: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """
    Handle requests to the lakehouse agent.

    Wires TWO prefixed MCP clients (R6): claims/* → GW1 (Interceptor_Gateway,
    SSM gateway-arn) and notes/* → GW2 (Notes_Gateway, SSM notes-gateway-url). The
    SAME inbound user bearer authenticates both gateways; the agent is
    IdP-agnostic (GW2's auth flip — Cognito interceptor vs Okta OBO — is entirely
    gateway-side). The union of both tool catalogs is exposed to the model.

    The caller's bearer token is read from the inbound `Authorization` header, which
    the runtime validates and forwards. The `bearer_token` payload field is still
    honoured as a transitional fallback and logs a warning when used.

    NOTE ON THE SIGNATURE: the second parameter MUST be named `context`. The runtime
    SDK inspects the handler's parameter names and passes the request context only
    when the second one is called `context`; with any other name it invokes the
    handler with the payload alone, and no request headers are available. Renaming it
    breaks header propagation silently.

    Args:
        payload: Request with the prompt (and, transitionally, a bearer token)
        context: Runtime request context carrying the forwarded request headers

    Returns:
        Agent response
    """
    user_prompt = payload.get("prompt", "Hello")
    bearer_token = _extract_bearer_token(payload, context)

    logger.info(f"📥 Received request: {user_prompt[:100]}...")
    logger.info(f"🔑 Bearer token present: {bool(bearer_token)}")

    # Load configuration
    config = get_config()
    gateway_arn = config["gateway_arn"]
    obo_gateway_url = config["obo_gateway_url"]
    region = config["region"]

    # The SAME inbound user JWT authenticates BOTH gateways (they validate the
    # same token). GW1 runs its interceptor flow; GW2 runs the Cognito interceptor
    # or Okta OBO (TOKEN_EXCHANGE) transparently — identical at the agent.
    auth_headers = {"Authorization": f"Bearer {bearer_token}"}

    # Track opened MCP clients so we deterministically close them in finally.
    open_clients = []
    tools = []

    try:
        # ── GW1 Interceptor_Gateway → claims/* tools ────────────────────────
        logger.info(f"🔗 Connecting to GW1 (claims): {gateway_arn}")
        gateway_url = get_gateway_url(gateway_arn, region)

        claims_client = MCPClient(
            lambda: streamablehttp_client(gateway_url, headers=auth_headers),
            prefix="claims",
        )
        claims_client.__enter__()
        open_clients.append(claims_client)
        claims_tools = claims_client.list_tools_sync()
        tools += claims_tools
        logger.info(f"✅ Loaded {len(claims_tools)} tools from GW1")

        # ── GW2 Notes_Gateway → notes/* tools (REQUIRED, R6/R10) ────────────
        # GW2 is mandatory: the agent must expose notes/* alongside claims/*.
        # Fail LOUD (symmetric with GW1, which has no try/except) rather than
        # silently degrading to claims-only — a missing URL or a connect/
        # list_tools failure is a hard error the operator must fix.
        if not obo_gateway_url:
            raise RuntimeError(
                "notes gateway required — run notebook 05b to deploy it and ensure "
                "notes-gateway-url is set in SSM (/app/lakehouse-agent/notes-gateway-url)."
            )
        logger.info(f"🔗 Connecting to GW2 (notes): {obo_gateway_url}")
        notes_client = MCPClient(
            lambda: streamablehttp_client(obo_gateway_url, headers=auth_headers),
            prefix="notes",
        )
        notes_client.__enter__()
        open_clients.append(notes_client)
        notes_tools = notes_client.list_tools_sync()
        tools += notes_tools
        logger.info(f"✅ Loaded {len(notes_tools)} tools from GW2")

        logger.info(f"✅ Total tools available to the agent: {len(tools)}")

        # Create Bedrock model
        model = BedrockModel(model_id=MODEL_ID, region_name=region)

        # Create agent with the merged tool set from BOTH gateways. The LLM
        # selects claims/* vs notes/* by tool description — no hard-coded routing.
        agent = Agent(model=model, tools=tools, system_prompt=CLAIMS_SYSTEM_PROMPT)

        # Process request
        logger.info("⏳ Processing request...")
        response = agent(user_prompt)
        logger.info("✅ Request processed")

        # Extract response content
        response_text = ""
        if hasattr(response, "message") and "content" in response.message:
            for content in response.message["content"]:
                if isinstance(content, dict) and "text" in content:
                    response_text += content["text"]
        else:
            response_text = str(response)

        # Real tool-use telemetry from the message log (see _extract_tool_events).
        tool_events = _extract_tool_events(agent)
        tools_used = list(dict.fromkeys(tool_events))  # de-dup, preserve call order

        return {
            "content": response_text,
            "tools_used": tools_used,
            "tool_calls": len(tool_events),
        }

    finally:
        # Deterministically close every opened MCP client (reverse order),
        # swallowing teardown errors so cleanup never masks the real result.
        for client in reversed(open_clients):
            try:
                client.__exit__(None, None, None)
            except Exception as e:
                logger.warning(f"⚠️  Error closing MCP client during cleanup: {e}")


if __name__ == "__main__":
    app.run()
