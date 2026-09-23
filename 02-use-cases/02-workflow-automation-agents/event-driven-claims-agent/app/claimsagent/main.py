"""
Event-Driven Insurance Claims Agent — Dual-Agent Architecture

Agent 1 (Claims Processor): Evaluates claim, verifies policy, makes ACCEPT/REJECT decision
Agent 2 (Validation Agent): Reviews decision, assigns confidence score, routes accordingly
"""

import json
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

app = BedrockAgentCoreApp()
log = app.logger

PROCESSOR_PROMPT = """You are a Claims Processor for SecureGuard Insurance.

Your job:
1. Extract claim details from the submission (policy number, description, amount, category)
2. Attempt lookup_policy to verify coverage. If it fails or is unavailable, proceed immediately.
3. Evaluate the claim against policy terms
4. Make a decision: ACCEPT or REJECT with detailed reasoning
If lookup_policy is unavailable, ACCEPT plausible claims conditionally, noting manual verification is needed. Do not reject solely because lookup failed.

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

_processor = None
_validator = None
_mcp_client = None


def load_model(fast: bool = False) -> BedrockModel:
    """Load a Bedrock model.

    Cost routing: the Validation Agent (Phase 2) is a classification task
    that doesn't require tool use — Haiku is sufficient and ~5x cheaper/faster.
    The Processor and Executor use the full Sonnet model for complex reasoning.
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

    return MCPClient(_transport)


def get_mcp_client():
    """Get or create the MCPClient for Gateway tool access."""
    global _mcp_client
    if _mcp_client is None:
        if not GATEWAY_URL:
            log.warning("GATEWAY_URL not configured — tools unavailable")
            return None
        try:
            _mcp_client = _build_mcp_client()
        except Exception as exc:
            log.warning("Failed to build MCP client (Identity auth): %s", exc)
            return None
    return _mcp_client


def get_processor(session_manager=None):
    """Create or return the Claims Processor agent.

    When a session_manager is provided, a fresh agent is created for that session
    (memory is per-invocation). Without memory, the cached singleton is reused.
    """
    global _processor
    if session_manager is not None:
        # Per-invocation agent with memory — enables cross-session recall
        return Agent(
            model=load_model(),
            system_prompt=PROCESSOR_PROMPT,
            tools=[get_mcp_client(), submit_decision],
            session_manager=session_manager,
        )
    if _processor is None:
        _processor = Agent(
            model=load_model(),
            system_prompt=PROCESSOR_PROMPT,
            tools=[get_mcp_client(), submit_decision],
        )
    return _processor


def get_validator():
    global _validator
    if _validator is None:
        # Validator only validates — no Gateway tool access (least privilege).
        # Uses the fast model (Haiku): validation is a classification task,
        # ~5x cheaper and ~3-8s faster per invocation than Sonnet.
        _validator = Agent(
            model=load_model(fast=True),
            system_prompt=VALIDATOR_PROMPT,
            tools=[submit_validation],
        )
    return _validator


async def _call_tool(mcp: MCPClient, tool_name: str, arguments: dict) -> dict:
    """Call a Gateway tool directly via MCP (no LLM intermediary).

    Used by Phase 3 to execute deterministic actions without an additional
    LLM invocation. The routing decision is already made — we just need
    to call the tools with known parameters.

    The MCPClient is already started by the Agent in Phase 1, so we can
    call tools directly without re-initializing the connection.
    """
    tool_use_id = f"phase3-{uuid.uuid4().hex[:8]}"
    result = await mcp.call_tool_async(
        tool_use_id=tool_use_id,
        name=tool_name,
        arguments=arguments,
    )
    # MCPToolResult has .content list; extract the text payload
    if result and hasattr(result, "content") and result.content:
        for content_block in result.content:
            if hasattr(content_block, "text"):
                try:
                    return json.loads(content_block.text)
                except (json.JSONDecodeError, TypeError):
                    return {"raw": content_block.text}
    return {"raw": str(result)}


def _extract_claim_id(result: dict) -> str:
    """Extract claim_id from create_claim tool result."""
    if isinstance(result, dict):
        return result.get("claim_id", "unknown")
    return "unknown"


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

    # Extract actor/session identifiers for memory
    # Use claimant_email as actor_id for cross-session recall of repeat claimants
    actor_id = claimant_email or payload.get("user_id", "anonymous")
    session_id = f"claim-{actor_id}-{uuid.uuid4().hex}"

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
    except Exception as exc:
        log.warning("Memory unavailable (running without memory): %s", exc)

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
    # tools directly via MCP, saving 6–16s and ~$0.01 per invocation.
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
                yield f"📧 Notification sent to {claimant_email}\n"
                log.info("Rejection notification sent: %s", result)
            except Exception as exc:
                log.warning("Failed to send rejection notification (non-fatal): %s", exc)
                yield f"⚠️ Could not send notification: {exc}\n"

    elif action == "AUTO_APPROVE":
        yield f"**Auto-approved** (confidence: {confidence}/100)\n\n"

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
                log.info("Claim created (auto-approved): %s", claim_result)
            except Exception as exc:
                log.warning("Failed to create claim (non-fatal): %s", exc)
                yield f"⚠️ Could not create claim record: {exc}\n"

        # Notify claimant of approval
        if mcp and claimant_email and claimant_email != "unknown":
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
                yield f"📧 Approval notification sent to {claimant_email}\n"
                log.info("Approval notification sent: %s", result)
            except Exception as exc:
                log.warning("Failed to send approval notification (non-fatal): %s", exc)
                yield f"⚠️ Could not send notification: {exc}\n"

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
                log.info("Claim created (pending review): %s", claim_result)
            except Exception as exc:
                log.warning("Failed to create claim (non-fatal): %s", exc)
                yield f"⚠️ Could not create claim record: {exc}\n"

        # Escalate to human review
        if mcp and claim_id:
            concerns = structured_validation.get("concerns", "Low confidence score")
            try:
                result = await _call_tool(
                    mcp,
                    "request_human_review",
                    {
                        "claim_id": claim_id,
                        "reason": f"Confidence: {confidence}/100. {concerns}",
                        "estimated_amount": structured_decision.get("amount", 0),
                    },
                )
                yield "🔍 Escalated to human review\n"
                log.info("Human review requested: %s", result)
            except Exception as exc:
                log.warning("Failed to request human review (non-fatal): %s", exc)
                yield f"⚠️ Could not escalate to human review: {exc}\n"

        # Notify claimant their claim is under review
        if mcp and claimant_email and claimant_email != "unknown":
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
                yield f"📧 Review notification sent to {claimant_email}\n"
                log.info("Review notification sent: %s", result)
            except Exception as exc:
                log.warning("Failed to send review notification (non-fatal): %s", exc)
                yield f"⚠️ Could not send notification: {exc}\n"

    yield "\n\n✅ Processing complete.\n"


if __name__ == "__main__":
    app.run()
