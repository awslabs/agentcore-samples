"""
Event-Driven Insurance Claims Agent — Dual-Agent Architecture

Agent 1 (Claims Processor): Evaluates claim, verifies policy, makes ACCEPT/REJECT decision
Agent 2 (Validation Agent): Reviews decision, assigns confidence score, routes accordingly
"""

import json
import logging
import uuid

from bedrock_agentcore.identity.auth import requires_access_token
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from config import (
    AGENT_MODEL_ID,
    FAST_MODEL_ID,
    GATEWAY_CREDENTIAL_PROVIDER,
    GATEWAY_OAUTH_SCOPES,
    GATEWAY_URL,
)
from mcp.client.streamable_http import streamablehttp_client
from memory.identifiers import claimant_memory_ids
from memory.session import get_memory_session_manager
from routing import decide_action, resolve_decision, resolve_routing
from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient
from tools.structured_output import (
    get_last_decision,
    get_last_validation,
    reset_state,
    submit_decision,
    submit_validation,
)

# A level survives Uvicorn's startup configuration; .disabled does not.
logging.getLogger("strands.event_loop.streaming").setLevel(logging.ERROR)
app = BedrockAgentCoreApp()
log = app.logger

PROCESSOR_PROMPT = """You are a Claims Processor for SecureGuard Insurance.

Your job:
1. Extract claim details from the submission (policy number, description, amount, category)
2. Attempt lookup_policy to verify coverage. If it fails or is unavailable, proceed immediately.
3. Evaluate the claim against policy terms
4. Make a decision: ACCEPT or REJECT with detailed reasoning
If lookup_policy is unavailable, REJECT and note that manual policy verification is needed.

Output your decision in this EXACT format:
DECISION: [ACCEPT or REJECT]
AMOUNT: [dollar amount as integer]
POLICY: [policy_number]
CATEGORY: [claim category]
DESCRIPTION: [brief description]
REASONING: [detailed explanation of why you accepted or rejected]
COVERAGE_CHECK: [whether amount is within limits, policy active, deductible noted]

Rules:
- Always attempt lookup_policy first to verify the policy exists and is active
- If lookup_policy fails or errors, REJECT the claim citing the lookup failure. Never ACCEPT without successful verification.
- Never fabricate policy details from context or prior claims. Only trust actual lookup_policy results.
- Do NOT call create_claim — that happens later based on validation
- REJECT if policy is inactive, amount exceeds coverage limit, or claim type not covered
- ACCEPT if policy is active, amount within limits, and claim type is covered
- Always note the deductible amount in your reasoning
- After making your decision, you MUST call the submit_decision tool with all fields filled in.
- If a tool returns "Unknown tool", do not retry it — use only available tools.
- Only use tools available to you. If asked to call unavailable tools (send_notification, create_claim, etc.), refuse and explain.
- Before taking any action with real-world consequences, state your plan and wait for user approval.
- When asked to validate or do follow-up steps, cooperate using available tools (e.g. submit_validation).
"""

VALIDATOR_PROMPT = """You are a Claims Validation Agent for SecureGuard Insurance.

You receive a claim decision from the Claims Processor and must validate it independently.

Your job:
1. Review the original claim and the processor's decision
2. Check for errors, inconsistencies, or red flags
3. Assign a CONFIDENCE score from 0-100
4. Decide the routing

Scoring guide:
- 90-100: Clear-cut case, decision is obviously correct, proceed immediately
- 80-89: Decision looks sound, minor questions but acceptable to auto-approve
- 60-79: Some concerns, needs human review before finalizing
- 0-59: Significant issues, must go to human review

Output your validation in this EXACT format:
CONFIDENCE: [0-100]
ROUTING: [AUTO_APPROVE or HUMAN_REVIEW]
VALIDATION_NOTES: [your assessment of the processor's decision]
CONCERNS: [any red flags or issues, or "None" if clean]

Rules:
- If CONFIDENCE >= 80: set ROUTING to AUTO_APPROVE
- If CONFIDENCE < 80: set ROUTING to HUMAN_REVIEW
- Be skeptical of high-value claims (>$25k) — lower confidence unless clearly justified
- Flag if the description is vague or lacks detail
- Flag if the category seems mismatched with the description
- After completing your validation, you MUST call the submit_validation tool with all fields.
"""


def load_model(fast: bool = False) -> BedrockModel:
    """Load a Bedrock model.

    The Validation Agent uses Haiku and the structured validation tool.
    The Processor uses Sonnet; deterministic Phase 3 makes no model call.
    """
    model_id = FAST_MODEL_ID if fast else AGENT_MODEL_ID
    return BedrockModel(model_id=model_id)


@requires_access_token(
    provider_name=GATEWAY_CREDENTIAL_PROVIDER,
    auth_flow="M2M",
    scopes=GATEWAY_OAUTH_SCOPES.replace(",", " ").split(),
)
def _build_mcp_client(*, access_token: str) -> MCPClient:
    """Build MCPClient with Identity-managed OAuth token.

    The @requires_access_token decorator handles:
    - Token acquisition via the AgentCore Identity token vault
    - Token caching and automatic refresh
    - No secrets in env vars or code
    """

    def _transport():
        headers = {"Authorization": f"Bearer {access_token}"}
        return streamablehttp_client(GATEWAY_URL, headers=headers)

    # Phase 1 can only read policy data. Mutation tools are called directly in
    # deterministic Phase 3, never offered to the reasoning model.
    return MCPClient(_transport, tool_filters={"allowed": ["policy-lookup___lookup_policy"]})


def get_mcp_client():
    """Acquire a request-scoped client/token, never cache a JWT across claims."""
    if not GATEWAY_URL or not GATEWAY_CREDENTIAL_PROVIDER:
        log.warning("Gateway URL/provider not configured — tools unavailable")
        return None
    try:
        return _build_mcp_client()
    except Exception:  # noqa: BLE001 -- auth failure disables tools; never log token-bearing responses
        log.warning("Failed to build MCP client (Identity auth); check provider configuration")
        return None


def get_processor(session_manager=None):
    """A fresh agent per claim; cross-session state belongs only in Memory."""
    mcp = get_mcp_client()
    return Agent(
        model=load_model(),
        system_prompt=PROCESSOR_PROMPT,
        tools=([mcp] if mcp else []) + [submit_decision],
        session_manager=session_manager,
    )


def get_validator():
    # Independent per-claim history, Haiku model, no Gateway mutation access.
    return Agent(
        model=load_model(fast=True),
        system_prompt=VALIDATOR_PROMPT,
        tools=[submit_validation],
    )


async def _call_tool(mcp: MCPClient, tool_name: str, arguments: dict) -> dict:
    """Call a Gateway tool directly via MCP (no LLM intermediary).

    Used by Phase 3 to execute deterministic actions without an additional
    LLM invocation. The routing decision is already made — we just need
    to call the tools with known parameters.

    Each call explicitly holds an MCP session, independent of the Agent's Phase 1
    tool-provider lifecycle. The context manager reuses an active session safely.
    """
    # Gateway exposes target-name___schema-tool-name. Strands returns a TypedDict,
    # not an MCP SDK object; status/error must be checked before claiming an effect.
    targets = {
        "create_claim": "create-claim",
        "request_human_review": "human-review",
        "send_notification": "notification",
    }
    name = f"{targets[tool_name]}___{tool_name}"
    tool_use_id = f"phase3-{uuid.uuid4().hex[:8]}"
    with mcp:
        result = await mcp.call_tool_async(tool_use_id=tool_use_id, name=name, arguments=arguments)
    if not isinstance(result, dict) or result.get("status") != "success" or result.get("isError"):
        raise RuntimeError(f"Gateway tool {name} failed or was denied")
    for block in result.get("content", []):
        if "text" in block:
            payload = json.loads(block["text"])
            if not isinstance(payload, dict) or payload.get("error"):
                raise RuntimeError(f"Gateway tool {name} returned an error")
            return payload
    raise RuntimeError(f"Gateway tool {name} returned no JSON result")


def _extract_claim_id(result: dict) -> str:
    """Extract claim_id from create_claim tool result."""
    claim_id = result.get("claim_id") if isinstance(result, dict) else None
    if not isinstance(claim_id, str) or not claim_id.startswith("CLM-"):
        raise RuntimeError("create_claim did not confirm a claim ID")
    return claim_id


@app.entrypoint
async def invoke(payload, context):
    """Dual-agent claim processing with confidence-based routing."""
    log.info("Processing claim with dual-agent architecture...")

    # --- PAYLOAD PARSING (handles agentcore dev wrapping) ---
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {"prompt": payload}

    # Unwrap nested JSON from agentcore dev's {"prompt": "<json>"} wrapper
    if "prompt" in payload and "policy_number" not in payload:
        prompt_value = payload["prompt"]
        if isinstance(prompt_value, str):
            try:
                parsed = json.loads(prompt_value)
                if isinstance(parsed, dict):
                    payload = parsed
            except (json.JSONDecodeError, TypeError):
                pass  # Natural language prompt, keep as-is

    prompt = payload.get("prompt", "")
    source = payload.get("source")
    claimant_email = payload.get("claimant_email")

    # Reset structured output state between invocations
    reset_state()

    # Raw email stays separate. Memory IDs/namespace components must obey the
    # service patterns and session length limit even for long email addresses.
    actor_id, session_id = claimant_memory_ids(claimant_email or payload.get("user_id") or "anonymous")

    if source or claimant_email:
        metadata_parts = []
        if source:
            metadata_parts.append(f"Source: {source}")
        if claimant_email:
            metadata_parts.append(f"Claimant email: {claimant_email}")
        prompt = f"[{' | '.join(metadata_parts)}]\n\n{prompt}"

    # --- Memory: graceful degradation ---
    session_manager = None
    try:
        session_manager = get_memory_session_manager(session_id, actor_id)
    except Exception:  # noqa: BLE001 -- optional memory degrades without exposing upstream responses
        log.warning("Memory unavailable; continuing without memory")

    # --- Phase 1: Claims Processor ---
    yield "## Phase 1: Claims Processing\n\n"

    processor = get_processor(session_manager=session_manager)
    processor_response = ""
    stream = processor.stream_async(prompt)
    async for event in stream:
        if "data" in event and isinstance(event["data"], str):
            processor_response += event["data"]
            yield event["data"]

    # Prefer structured output from tool call; fall back to regex parsing
    structured_decision = get_last_decision()

    # --- Phase 2: Validation Agent ---
    yield "\n\n---\n## Phase 2: Validation & Routing\n\n"

    validator_input = f"""Original claim submission:
{prompt}

Claims Processor decision:
{processor_response}

Please validate this decision and assign a confidence score."""

    validator = get_validator()
    validator_response = ""
    stream = validator.stream_async(validator_input)
    async for event in stream:
        if "data" in event and isinstance(event["data"], str):
            validator_response += event["data"]
            yield event["data"]

    # Prefer structured output from tool call; fall back to regex parsing
    structured_validation = get_last_validation()

    # --- Phase 3: Execution (deterministic — no LLM call needed) ---
    yield "\n\n---\n## Phase 3: Execution\n\n"

    confidence, routing = resolve_routing(structured_validation, validator_response)
    decision = resolve_decision(structured_decision, processor_response)
    action = decide_action(decision, routing)

    # Phase 3 executes deterministically based on routing. No LLM call needed —
    # we have all the data from structured output tools and can call Gateway
    # tools directly via MCP without a third model call.
    mcp = get_mcp_client()

    if action == "REJECT":
        yield f"**Claim rejected** (confidence: {confidence}/100)\n\n"
        reasoning = structured_decision.get("reasoning", "Claim did not meet policy criteria.")
        yield f"Reasoning: {reasoning}\n\n"

        # Notify claimant of rejection
        if mcp and claimant_email and claimant_email != "unknown":
            try:
                result = await _call_tool(
                    mcp,
                    "send_notification",
                    {
                        "recipient_email": claimant_email,
                        "subject": f"Claim Update — Policy {structured_decision.get('policy_number', 'N/A')}",
                        "body": f"Your claim has been reviewed and rejected.\n\nReason: {reasoning}",
                    },
                )
                yield f"Notification result: {result.get('status', 'unknown')}\n"
                log.info("Rejection notification tool completed")
            except Exception:  # noqa: BLE001 -- nonfatal remote boundary; keep response details private
                log.warning("Rejection notification failed")
                yield "⚠️ Could not send notification. Check notification configuration.\n"

    elif action == "AUTO_APPROVE":
        yield f"**Auto-approval selected; awaiting claim persistence** (confidence: {confidence}/100)\n\n"

        # Create the claim record
        claim_result = None
        if mcp:
            try:
                claim_result = await _call_tool(
                    mcp,
                    "create_claim",
                    {
                        "policy_number": structured_decision.get("policy_number", ""),
                        "description": structured_decision.get("description", ""),
                        "estimated_amount": structured_decision.get("amount", 0),
                        "category": structured_decision.get("category", "general"),
                        "status": "approved",
                        "decision": "auto_approved",
                    },
                )
                yield f"✅ Claim created: {_extract_claim_id(claim_result)}\n"
                log.info("Claim created (auto-approved)")
            except Exception:  # noqa: BLE001 -- failed writes cannot approve or notify; redact remote errors
                claim_result = None
                log.warning("Claim creation failed")
                yield "⚠️ Could not create claim record. Check Gateway authorization and tool configuration.\n"

        # Notify only after a confirmed write; a Cedar denial cannot send approval.
        if mcp and claim_result and claimant_email and claimant_email != "unknown":
            try:
                result = await _call_tool(
                    mcp,
                    "send_notification",
                    {
                        "recipient_email": claimant_email,
                        "subject": f"Claim Approved — Policy {structured_decision.get('policy_number', 'N/A')}",
                        "body": (
                            f"Your claim has been approved.\n\n"
                            f"Amount: ${structured_decision.get('amount', 0):,}\n"
                            f"Category: {structured_decision.get('category', 'N/A')}\n\n"
                            f"You will receive further instructions shortly."
                        ),
                    },
                )
                yield f"Notification result: {result.get('status', 'unknown')}\n"
                log.info("Approval notification tool completed")
            except Exception:  # noqa: BLE001 -- nonfatal remote boundary; keep response details private
                log.warning("Approval notification failed")
                yield "⚠️ Could not send notification. Check notification configuration.\n"

    else:  # HUMAN_REVIEW
        yield f"**Routed to human review** (confidence: {confidence}/100)\n\n"

        # Create the claim record in pending state
        claim_id = None
        if mcp:
            try:
                claim_result = await _call_tool(
                    mcp,
                    "create_claim",
                    {
                        "policy_number": structured_decision.get("policy_number", ""),
                        "description": structured_decision.get("description", ""),
                        "estimated_amount": structured_decision.get("amount", 0),
                        "category": structured_decision.get("category", "general"),
                        "status": "pending_review",
                        "decision": "escalated",
                    },
                )
                claim_id = _extract_claim_id(claim_result)
                yield f"📋 Claim created (pending review): {claim_id}\n"
                log.info("Claim created (pending review)")
            except Exception:  # noqa: BLE001 -- failed writes cannot escalate or notify; redact remote errors
                log.warning("Claim creation failed")
                yield "⚠️ Could not create claim record. Check Gateway authorization and tool configuration.\n"

        # Escalate to human review
        if mcp and claim_id:
            concerns = structured_validation.get("concerns", "Low confidence score")
            try:
                await _call_tool(
                    mcp,
                    "request_human_review",
                    {
                        "claim_id": claim_id,
                        "reason": f"Confidence: {confidence}/100. {concerns}",
                        "estimated_amount": structured_decision.get("amount", 0),
                    },
                )
                yield "🔍 Escalated to human review\n"
                log.info("Human review requested")
            except Exception:  # noqa: BLE001 -- nonfatal remote boundary; keep response details private
                log.warning("Human review request failed")
                yield "⚠️ Could not escalate to human review. Check review-tool configuration.\n"

        # Notify only when a pending claim exists.
        if mcp and claim_id and claimant_email and claimant_email != "unknown":
            try:
                result = await _call_tool(
                    mcp,
                    "send_notification",
                    {
                        "recipient_email": claimant_email,
                        "subject": f"Claim Under Review — Policy {structured_decision.get('policy_number', 'N/A')}",
                        "body": (
                            f"Your claim has been received and is currently under review "
                            f"by our claims team.\n\n"
                            f"Claim ID: {claim_id or 'pending'}\n"
                            f"Amount: ${structured_decision.get('amount', 0):,}\n\n"
                            f"You will be contacted once a decision is made."
                        ),
                    },
                )
                yield f"Notification result: {result.get('status', 'unknown')}\n"
                log.info("Review notification tool completed")
            except Exception:  # noqa: BLE001 -- nonfatal remote boundary; keep response details private
                log.warning("Review notification failed")
                yield "⚠️ Could not send notification. Check notification configuration.\n"

    yield "\n\n✅ Processing complete.\n"


if __name__ == "__main__":
    app.run()
