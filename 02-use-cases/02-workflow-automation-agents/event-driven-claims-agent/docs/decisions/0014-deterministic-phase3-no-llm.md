# ADR-0014: Deterministic Phase 3 Execution — Direct MCP Tool Calls (No LLM)

## Status

Accepted

## Context

The dual-agent pipeline originally used 3 sequential LLM invocations:
1. Phase 1 (Processor): evaluate claim, call `lookup_policy`, produce decision
2. Phase 2 (Validator): review decision, assign confidence, determine routing
3. Phase 3 (Executor): call `create_claim`, `request_human_review`, `send_notification`

Phase 3 was the most wasteful — it asked Sonnet to orchestrate tool calls where all parameters were already known from Phase 1's structured output. The LLM added no reasoning value; it was just reformatting data into tool call arguments. This added 6–16s latency and ~$0.01 per invocation.

## Decision

Replace Phase 3's LLM invocation with direct `MCPClient.call_tool_async()` calls. The routing decision (REJECT / AUTO_APPROVE / HUMAN_REVIEW) is deterministic from `routing.py`, and all tool parameters come from the structured decision captured by `submit_decision` in Phase 1.

The implementation:
- Uses `MCPClient.call_tool_async(tool_use_id, name, arguments)` to call Gateway tools directly
- Wraps every tool call in try/except with non-fatal semantics (log + continue)
- Extracts `claim_id` from `create_claim` result to pass to `request_human_review`
- Streams progress markers (✅, 📧, 🔍, ⚠️) for observability

## Alternatives Rejected

1. **Keep Phase 3 LLM call but use Haiku** — Saves cost but not latency meaningfully. Still requires model inference for a deterministic operation. Adds risk of Haiku misinterpreting parameters.

2. **Use Strands `@tool` with a co-located orchestrator tool** — Would still go through the Agent loop (1 LLM call to select and invoke the tool). Adds unnecessary indirection.

3. **EventBridge-based async execution** — Decouple Phase 3 entirely via EventBridge rules. Rejected because: (a) adds infrastructure complexity, (b) loses the streaming progress feedback to the caller, (c) makes the E2E test flow harder to validate.

## Consequences

- **Latency:** Phase 3 drops from 6–16s (LLM inference + tool calls) to 200–500ms (direct tool calls only). Total request latency reduced by ~30%.
- **Cost:** Eliminates one Sonnet invocation per request (~$0.01 saved).
- **Reliability:** Removes LLM non-determinism from execution. Tool calls always use correct parameters from structured output. No risk of the model hallucinating wrong field values.
- **Observability trade-off:** Phase 3 no longer produces natural language explanation of what it's doing. Mitigated by structured progress markers in the stream.
- **Fallback handling:** If `submit_decision` was never called (Phase 1 failure), structured_decision is empty. Phase 3 handles this gracefully with `.get()` defaults — but the routing logic already defaults to REJECT in this case, so only `send_notification` would fire.
- **MCP client lifecycle:** Depends on the MCPClient being started by Phase 1's Agent usage. If Gateway is unavailable, all Phase 3 actions are skipped (non-fatal).
