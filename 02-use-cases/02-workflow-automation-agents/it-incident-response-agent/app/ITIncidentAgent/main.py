"""IT Incident Response Agent — AgentCore Runtime entrypoint (v3, CLI-first).

Flow per invocation:
  1. Receive ticket payload from the trigger Lambda via AgentCore Runtime.
     Supports two modes:
       - Full ticket: {ticket_id, requester_id, title, description, priority}
       - Jira issue key: {issue_key, requester_id} (when Jira integration enabled)
  2. Retrieve past-incident summaries from AgentCore Memory for context.
  3. Connect to MCP servers (Gateway + optionally Atlassian Remote MCP).
  4. Run a Strands agent with aggregated tools from all servers.
  5. Record the run as an episode in AgentCore Memory.
  6. Write resolution (DDB for full-ticket mode, Jira comment for issue-key mode).
  7. On failure: mark ticket as Failed with error context.
"""

import json
import logging
import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from string import Template

import boto3
from boto3.dynamodb.conditions import Key
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from config import (
    TICKETS_TABLE,
    REGION,
    EVENT_BUS_NAME,
    GUARDRAIL_ID,
    GUARDRAIL_VERSION,
    JIRA_MCP_URL,
    JIRA_SITE_URL,
    JIRA_PROJECT_KEY,
)
from model.load import load_model
from mcp_client.client import get_all_mcp_clients_safe
from memory.session import get_memory_session_manager
from memory.enrichment import (
    retrieve_past_incidents,
    format_past_incidents_block,
)
from strands import Agent
from strands.hooks import BeforeToolCallEvent, AfterToolCallEvent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("it-incident-agent")

# ─── Tool call timing hooks ──────────────────────────────────────────────────
# These hooks log each tool invocation with start/end timing for bottleneck analysis.
# Using ContextVar to scope per-invocation and avoid cross-request contamination
# when the Runtime serves concurrent requests in the same container.
_tool_start_times: ContextVar[dict[str, float]] = ContextVar("_tool_start_times")


def _get_tool_times() -> dict[str, float]:
    """Get the per-request tool timing dict, creating one if needed."""
    try:
        return _tool_start_times.get()
    except LookupError:
        times: dict[str, float] = {}
        _tool_start_times.set(times)
        return times


def _on_before_tool_call(event: BeforeToolCallEvent) -> None:
    """Log when a tool call starts."""
    tool_name = event.tool_use.get("name", "unknown")
    times = _get_tool_times()
    times[event.tool_use.get("toolUseId", tool_name)] = time.time()
    logger.info("Tool call started: %s", tool_name)


def _on_after_tool_call(event: AfterToolCallEvent) -> None:
    """Log when a tool call completes with duration."""
    tool_name = event.tool_use.get("name", "unknown")
    tool_id = event.tool_use.get("toolUseId", tool_name)
    times = _get_tool_times()
    start = times.pop(tool_id, None)
    duration_ms = (time.time() - start) * 1000 if start else 0
    logger.info("Tool call completed: %s (%.0fms)", tool_name, duration_ms)


# Configuration is loaded from config.py (centralized env var resolution)

app = BedrockAgentCoreApp()

# DynamoDB resource for ticket status updates
_ddb = boto3.resource("dynamodb", region_name=REGION)
_events = boto3.client("events", region_name=REGION)
_bedrock_runtime = boto3.client("bedrock-runtime", region_name=REGION)

SYSTEM_PROMPT = """You are an IT Incident Response Agent.

You receive a ticket describing a user-reported IT problem. Your job is to
diagnose, take any necessary corrective action, and produce a clear
resolution comment.

Always work in this order:
  1. Call `lookup_user` with the requester to understand their context,
     quotas, and recent incident history. Recurring incidents (>= 2 in 30
     days) are a strong signal to escalate.
  2. If the ticket mentions a specific process / service / app, call
     `get_process_info` to understand its status and known issues.
  3. Call `query_kb` with a focused query to retrieve relevant runbook
     guidance from the IT knowledge base.
  4. If a corrective action is justified by the runbook, call
     `create_change_request` to record the action and stamp the user.
  5. Produce a short final resolution comment (3-6 sentences) that
     summarises what you found, what you did, and any follow-up the user
     should take.

Return only the resolution comment as your final message. Do not include
chain-of-thought, planning markers, or tool-call narration in the final
comment."""

SYSTEM_PROMPT_JIRA = """You are an IT Incident Response Agent.

You receive a Jira issue key. Your job is to:
  1. Fetch the issue from Jira (use the jira-prefixed tools — e.g.
     `jira___getIssue` or whichever the server exposes for reading an
     issue by key).
  2. Diagnose the incident using the IT-side tools available via the
     AgentCore Gateway:
       - lookup_user (requester profile + quotas)
       - get_process_info (status of named services / apps / assets)
       - query_kb (relevant runbook guidance)
       - create_change_request (only when the runbook justifies an action)
  3. Write a clear, concise resolution comment on the Jira issue (3-6
     sentences) using the jira-prefixed addComment tool.
  4. Transition the issue to a resolved/done state using the jira-prefixed
     transition tool.

Rules:
  - The site to operate on is $jira_site_url, project $jira_project_key.
  - Don't open a change request unless a runbook supports the action.
  - Keep the resolution comment user-facing — no chain-of-thought,
    no tool-call narration.
  - Treat the recurring-incident signal below as ground truth: if the
    requester has 2 or more past episodes, escalate (mention recurrence
    in the resolution comment and create a change request when a runbook
    supports it).

$past_incidents_block
Return the resolution comment as your final message.
"""


def _count_recent_incidents(requester_id: str, exclude_ticket_id: str = "") -> int:
    """Count this requester's incidents in the last 30 days (excluding the
    current ticket), querying the same ``byRequester`` GSI the ``lookup_user``
    tool uses.

    This is the authoritative, immediately-consistent recurrence signal. Unlike
    the SUMMARIZATION memory summaries (extracted asynchronously, so they lag a
    just-submitted ticket and read 0 on an immediate resubmit), the tickets
    table reflects prior incidents right away — keeping the reported count in
    sync with the recurrence the model narrates from ``lookup_user``.

    Non-fatal: returns 0 on any failure.
    """
    if not TICKETS_TABLE or not requester_id:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    try:
        items = (
            _ddb.Table(TICKETS_TABLE)
            .query(
                IndexName="byRequester",
                KeyConditionExpression=(Key("requester_id").eq(requester_id) & Key("created_at").gte(cutoff)),
                Limit=25,
                ScanIndexForward=False,
            )
            .get("Items", [])
        )
    except Exception:
        logger.exception("Failed to count recent incidents for %s (non-fatal)", requester_id)
        return 0
    return sum(1 for it in items if it.get("ticket_id") != exclude_ticket_id)


def _resolve_ticket(ticket_id: str, comment: str) -> None:
    """Mark ticket as Resolved with the agent's resolution comment.

    Non-fatal: if the DDB write fails, the resolution was still successful
    (the agent produced a valid response). Log the error but don't propagate.
    """
    if not TICKETS_TABLE:
        logger.warning("TICKETS_TABLE not set, skipping ticket update")
        return
    now = datetime.now(timezone.utc).isoformat()
    table = _ddb.Table(TICKETS_TABLE)
    try:
        table.update_item(
            Key={"ticket_id": ticket_id},
            UpdateExpression=("SET #s = :s, resolution_comment = :c, resolved_at = :t, updated_at = :t"),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "Resolved",
                ":c": comment,
                ":t": now,
            },
        )
    except Exception:
        logger.exception("Failed to write resolution to DDB for %s (non-fatal)", ticket_id)


def _fail_ticket(ticket_id: str, error: str) -> None:
    """Mark ticket as Failed with error context.

    Non-fatal: if the DDB write fails, log the error but don't propagate.
    """
    if not TICKETS_TABLE:
        return
    now = datetime.now(timezone.utc).isoformat()
    table = _ddb.Table(TICKETS_TABLE)
    try:
        table.update_item(
            Key={"ticket_id": ticket_id},
            UpdateExpression="SET #s = :s, error_message = :e, updated_at = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "Failed",
                ":e": error[:1000],
                ":t": now,
            },
        )
    except Exception:
        logger.exception("Failed to mark ticket %s as Failed in DDB (non-fatal)", ticket_id)


def _emit_resolution_event(ticket_id: str, resolution: str, requester_id: str) -> None:
    """Emit a TicketResolved event to EventBridge for downstream consumers.

    Completes the Trigger → Enrich → Reason → Act → **Emit** pattern.
    Downstream consumers (dashboards, notification systems, audit trails)
    can subscribe to this event via EventBridge rules.
    """
    try:
        _events.put_events(
            Entries=[
                {
                    "Source": "it-incident-agent",
                    "DetailType": "TicketResolved",
                    "Detail": json.dumps(
                        {
                            "ticket_id": ticket_id,
                            "requester_id": requester_id,
                            "resolution": resolution[:500],
                            "resolved_at": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                    "EventBusName": EVENT_BUS_NAME,
                }
            ]
        )
        logger.info("Emitted TicketResolved event for %s", ticket_id)
    except Exception:
        logger.exception("Failed to emit EventBridge event (non-fatal)")


def _apply_guardrail(text: str) -> str:
    """Apply Bedrock Guardrails to filter PII and inappropriate content.

    Event payloads can contain messy data — PII, profanity, or injection
    attempts. The guardrail sanitizes the input before it reaches the model.
    Returns the sanitized text, or original if no guardrail is configured.
    """
    if not GUARDRAIL_ID:
        return text  # No guardrail configured — pass through

    try:
        response = _bedrock_runtime.apply_guardrail(
            guardrailIdentifier=GUARDRAIL_ID,
            guardrailVersion=GUARDRAIL_VERSION,
            source="INPUT",
            content=[{"text": {"text": text, "qualifiers": ["query"]}}],
        )
        action = response.get("action", "NONE")
        if action == "GUARDRAIL_INTERVENED":
            # Guardrail blocked or modified the content
            outputs = response.get("outputs", [])
            if outputs:
                sanitized = outputs[0].get("text", text)
                logger.info("Guardrail intervened: replaced content (action=%s)", action)
                return sanitized
            logger.warning("Guardrail intervened but no output — using original")
        return text
    except Exception:
        logger.exception("Guardrail application failed (non-fatal, using original)")
        return text


def _build_query(ticket: dict) -> str:
    """Construct the agent query from the ticket payload."""
    return (
        f"Ticket {ticket['ticket_id']} from user {ticket['requester_id']} "
        f"(priority {ticket.get('priority', 'MEDIUM')}).\n"
        f"Title: {ticket.get('title', '')}\n"
        f"Description: {ticket.get('description', '')}\n\n"
        "Diagnose and resolve following the system instructions."
    )


# ─── Real-time pipeline stage events ─────────────────────────────────────────
# The Runtime streams every `yield` from this generator as an SSE `data:` line.
# We emit a structured stage marker at each real phase boundary so the UI can
# narrate genuine progress instead of guessing on a timer. Stage markers carry
# `"type": "stage"` so consumers that buffer the whole response and take the
# last JSON object (Lambda trigger, tests) still pick up the final result dict.


def _stage_event(stage: str, label: str, detail: str = "") -> str:
    """Serialize a real-time pipeline stage marker as a JSON line."""
    return json.dumps({"type": "stage", "stage": stage, "label": label, "detail": detail})


# Friendly (label, detail) for the tools the agent may call. These surface as
# live stages the moment the model decides to invoke each tool.
_TOOL_STAGES = {
    "lookup_user": (
        "Looking up the requester",
        "Fetching the requester's profile, quotas, and recent incident history",
    ),
    "get_process_info": (
        "Checking service status",
        "Looking up the status and known issues of the named service / process",
    ),
    "query_kb": (
        "Searching the knowledge base",
        "Retrieving matching runbook guidance from the IT knowledge base",
    ),
    "create_change_request": (
        "Creating a change request",
        "Recording the corrective action and stamping the requester",
    ),
}


def _tool_stage(name: str) -> tuple[str, str]:
    """Map a tool name to a human-readable (label, detail) for live status.

    Jira tools are prefixed (e.g. ``jira___getIssue``); anything not explicitly
    mapped falls back to a cleaned form of the raw tool name.
    """
    if name in _TOOL_STAGES:
        return _TOOL_STAGES[name]
    pretty = name.replace("jira___", "Jira: ").replace("_", " ").strip()
    return (f"Using {pretty}", "")


@app.entrypoint
async def invoke(payload, context):
    """Main entrypoint called by AgentCore Runtime."""
    logger.info("Invoking IT Incident Response Agent...")

    # Determine if this is a ticket processing request or a simple prompt
    if isinstance(payload, str):
        payload = json.loads(payload) if payload.startswith("{") else {"prompt": payload}

    # Simple prompt mode (for testing / dev server)
    if "prompt" in payload and "ticket_id" not in payload and "issue_key" not in payload:
        session_id = getattr(context, "session_id", "default-session")
        user_id = getattr(context, "user_id", "default-user")

        mcp_clients, tool_warnings = get_all_mcp_clients_safe()
        tools = mcp_clients if mcp_clients else []

        agent = None
        try:
            if tool_warnings:
                logger.warning(
                    "Running in degraded mode (some tools unavailable): %s",
                    "; ".join(tool_warnings),
                )

            agent = Agent(
                model=load_model(),
                session_manager=get_memory_session_manager(session_id, user_id),
                system_prompt=SYSTEM_PROMPT,
                tools=tools,
            )
            agent.add_hook(_on_before_tool_call)
            agent.add_hook(_on_after_tool_call)
        except Exception as agent_init_exc:
            logger.warning(
                "Agent initialization with tools failed (%s: %s). Falling back to LLM-only mode.",
                type(agent_init_exc).__name__,
                agent_init_exc,
                exc_info=True,
            )

            # Create agent without tools — if this ALSO fails, there is no agent
            # to stream from, so yield a structured error instead of crashing
            # with a NameError on the unbound `agent`.
            try:
                agent = Agent(
                    model=load_model(),
                    session_manager=get_memory_session_manager(session_id, user_id),
                    system_prompt=SYSTEM_PROMPT,
                    tools=[],
                )
            except Exception:
                logger.exception("Agent fallback initialization also failed")
                yield json.dumps({"status": "Failed", "error": "Agent initialization failed"})
                return

        stream = agent.stream_async(payload.get("prompt"))
        async for event in stream:
            if "data" in event and isinstance(event["data"], str):
                yield event["data"]
        return

    # ─── Detect mode: issue_key (Jira) vs ticket_id (DDB mock) ─────
    is_jira_mode = "issue_key" in payload and bool(JIRA_MCP_URL)
    ticket_id = payload.get("issue_key") if is_jira_mode else payload.get("ticket_id")
    if not ticket_id:
        # Malformed payload (no ticket_id / issue_key, and not prompt-mode).
        # Yield a structured failure instead of crashing the generator with a
        # KeyError — this runs before the main try/except below.
        logger.error("Invalid payload: missing 'ticket_id' (or 'issue_key' in Jira mode)")
        yield json.dumps({"status": "Failed", "error": "Missing ticket_id or issue_key in payload"})
        return
    requester_id = payload.get("requester_id", ticket_id)
    priority = payload.get("priority", "MEDIUM")

    # ─── OBSERVABILITY: Set ticket_id on the current OTEL span ─────
    # This makes the ticket_id queryable in CloudWatch Transaction Search
    # and links OTEL traces to the business-level ticket identifier.
    try:
        from opentelemetry import trace as otel_trace

        current_span = otel_trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.set_attribute("ticket.id", ticket_id)
            current_span.set_attribute("ticket.priority", priority)
            current_span.set_attribute("ticket.requester_id", requester_id)
            current_span.set_attribute("ticket.mode", "jira" if is_jira_mode else "ddb")
    except ImportError:
        logger.debug("OpenTelemetry not available — span attributes not set")

    logger.info(
        "Processing %s %s (priority=%s, mode=%s)",
        "issue" if is_jira_mode else "ticket",
        ticket_id,
        priority,
        "jira" if is_jira_mode else "ddb",
    )

    try:
        # STEP: GUARDRAIL — Sanitize event payload before model invocation.
        # Both title AND description reach the model via _build_query, so both
        # must be sanitized — PII in a ticket title would otherwise bypass the
        # guardrail.
        yield _stage_event(
            "guardrail",
            "Sanitizing ticket content",
            "Applying the PII / content guardrail before model invocation",
        )
        if not is_jira_mode:
            payload_for_agent = {
                **payload,
                "title": _apply_guardrail(payload.get("title", "")),
                "description": _apply_guardrail(payload.get("description", "")),
            }
        else:
            payload_for_agent = payload

        # STEP: MEMORY ENRICHMENT — Retrieve past incidents for this requester
        yield _stage_event(
            "memory",
            "Retrieving incident history",
            "Searching AgentCore Memory for this requester's past incidents",
        )
        past_incidents = retrieve_past_incidents(
            requester_id=requester_id,
            query=f"prior incidents for {requester_id}",
        )

        # STEP: BUILD SYSTEM PROMPT — Inject memory context
        if is_jira_mode:
            past_block = format_past_incidents_block(past_incidents)
            # Use string.Template ($ substitution) to avoid KeyError if
            # past_block contains { or } characters from memory content.
            system_prompt = Template(SYSTEM_PROMPT_JIRA).safe_substitute(
                jira_site_url=JIRA_SITE_URL,
                jira_project_key=JIRA_PROJECT_KEY,
                past_incidents_block=past_block,
            )
        else:
            # DDB mode — append past incidents context to the standard prompt
            if past_incidents:
                past_block = format_past_incidents_block(past_incidents)
                system_prompt = SYSTEM_PROMPT + f"\n\n{past_block}"
            else:
                system_prompt = SYSTEM_PROMPT

        # STEP: MULTI-MCP — Connect to Gateway + optionally Jira with safe fallback
        yield _stage_event(
            "tools",
            "Connecting to tools",
            "Establishing Gateway + MCP tool connections (user lookup, service status, runbooks)",
        )
        mcp_clients, tool_warnings = get_all_mcp_clients_safe()
        tools = mcp_clients if mcp_clients else []

        # Try to create the agent with available tools, gracefully degrade if tool loading fails
        try:
            if tool_warnings:
                logger.warning(
                    "Running in degraded mode (some tools unavailable). Failures: %s. Attempting with available tools.",
                    "; ".join(tool_warnings),
                )

            agent = Agent(
                model=load_model(priority),
                session_manager=get_memory_session_manager(ticket_id, requester_id),
                system_prompt=system_prompt,
                tools=tools,
            )
        except Exception as agent_init_exc:
            # Tool loading failed even with available clients — fall back to LLM-only
            exc_name = type(agent_init_exc).__name__
            logger.warning(
                "Agent initialization with tools failed (%s: %s). Falling back to LLM-only mode.",
                exc_name,
                agent_init_exc,
                exc_info=True,
            )
            tool_warnings.append(f"Agent tool initialization failed: {exc_name}: {agent_init_exc}")

            # Create agent without tools
            agent = Agent(
                model=load_model(priority),
                session_manager=get_memory_session_manager(ticket_id, requester_id),
                system_prompt=system_prompt,
                tools=[],
            )

        # STEP: ENRICH + REASON + ACT — Run the agent
        if is_jira_mode:
            user_query = (
                f"Resolve Jira issue {ticket_id} in project {JIRA_PROJECT_KEY} "
                f"on site {JIRA_SITE_URL}. Follow the system instructions."
            )
        else:
            user_query = _build_query(payload_for_agent)

        yield _stage_event(
            "diagnose",
            "Diagnosing the issue",
            "Reasoning over the symptoms and selecting the right tools",
        )

        # Stream the agent's event loop so real tool calls surface as live
        # stages. Each distinct tool invocation (tracked by toolUseId) is
        # announced once, the moment the model decides to call it. The terminal
        # AgentResultEvent carries the final AgentResult under "result".
        result = None
        seen_tool_ids: set[str] = set()
        async for event in agent.stream_async(user_query):
            tool_use = event.get("current_tool_use") or {}
            tool_id = tool_use.get("toolUseId")
            tool_name = tool_use.get("name")
            if tool_id and tool_name and tool_id not in seen_tool_ids:
                seen_tool_ids.add(tool_id)
                label, detail = _tool_stage(tool_name)
                yield _stage_event("tool", label, detail)
            if "result" in event:
                result = event["result"]

        # Extract resolution text defensively. The model may return an empty
        # content list or non-text content (a tool-only turn, a refusal, or a
        # guardrail block); guard against an unguarded [0]["text"] IndexError.
        content = (getattr(result, "message", None) or {}).get("content") or []
        resolution = content[0].get("text", "") if content and isinstance(content[0], dict) else ""
        if not resolution:
            # No usable text output. Do NOT auto-resolve with placeholder text —
            # raise so the outer handler marks the ticket Failed. A ticket with no
            # agent resolution must require human processing, not be silently closed.
            raise ValueError("Agent produced no text resolution — requires human review")

        # STEP: MEMORY — Persistence is handled by the AgentCoreMemorySessionManager
        # attached to the Agent above; it writes the conversation turn (and the
        # SUMMARIZATION strategy rolls it into the per-requester namespace).
        # No separate create_event call is needed here (avoids a double-write).

        # STEP: ACT — Write resolution to ticket store (DDB mode only)
        # In Jira mode, the agent already commented + transitioned via MCP tools.
        if not is_jira_mode:
            yield _stage_event(
                "persist",
                "Saving the resolution",
                "Writing the resolution to the ticket store",
            )
            _resolve_ticket(ticket_id, resolution)

        # STEP: EMIT — Publish downstream event for consumers
        yield _stage_event(
            "emit",
            "Publishing the resolution event",
            "Emitting the TicketResolved event for downstream consumers",
        )
        _emit_resolution_event(ticket_id, resolution, requester_id)

        logger.info("%s %s resolved successfully", "Issue" if is_jira_mode else "Ticket", ticket_id)
        # Recurrence count: prefer the immediate tickets-table signal; fall back
        # to the (async) memory-summary count when no DDB history exists (e.g.
        # Jira mode). This keeps the reported count consistent with the
        # recurrence the model detects via lookup_user.
        recurring_count = max(
            len(past_incidents),
            _count_recent_incidents(requester_id, exclude_ticket_id=ticket_id),
        )
        yield json.dumps(
            {
                "ticket_id": ticket_id,
                "status": "Resolved",
                "resolution": resolution,
                "mode": "jira" if is_jira_mode else "ddb",
                "recurring_incident_count": recurring_count,
            }
        )

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Failed to process %s", ticket_id)

        try:
            if not is_jira_mode:
                _fail_ticket(ticket_id, error_msg)
        except Exception:
            logger.exception("Failed to mark ticket %s as Failed", ticket_id)

        yield json.dumps(
            {
                "ticket_id": ticket_id,
                "status": "Failed",
                "error": error_msg,
                "mode": "jira" if is_jira_mode else "ddb",
            }
        )


if __name__ == "__main__":
    app.run()
