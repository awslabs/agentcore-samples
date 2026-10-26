"""Receipts IDP Agent on Amazon Bedrock AgentCore.

PHASE 4 (dual agent — the extraction-quality half of M2):
  receipt in S3 -> Textract OCR -> EXTRACTOR agent (structured output, using the
  deterministic line-item table parser) -> independent VALIDATOR agent (checks
  reconciliation/category/confidence, OWNS the auto-persist-vs-review decision)
  -> persist via save_expense, or route to human_review, through the Gateway.

Two sequential Strands agents beat a single self-checking agent's confirmation
bias (claims ADR-0002). The validator is isolated from the extractor's reasoning —
it only sees the extractor's structured output + the OCR. Runs on the default L0
model (the degradation ladder is Phase 6; the validator is a sheddable rung feature).
Auth to the Gateway is agent-as-principal M2M Cognito (spec §10).
"""

import json
import os
import random
import time
import uuid

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from config import (
    DEFAULT_MODEL_ID,
    DEFER_QUEUE_URL,
    GATEWAY_URL,
    IDENTITY_KEY_ID,
    REGION,
    RUN_EVENT_BUS,
)
from gateway_auth import get_gateway_token
from identity import verify_identity
from memory.session import get_memory_session_manager
from mcp.client.streamable_http import streamablehttp_client
from model.ladder import classify_model_error, get_active_rung, next_rung, rung_for
from model.load import load_model
from parsing import build_run_event, parse_payload
from strands import Agent
from strands.tools.mcp import MCPClient
from tools.ocr import analyze_receipt
from tools.structured_output import (
    get_last_expense,
    get_last_validation,
    reset_state,
    submit_expense,
    submit_validation,
)
from tools.table_parser import parse_line_items, parse_success_rate

app = BedrockAgentCoreApp()
log = app.logger

EXTRACTOR_PROMPT = """You are a receipts extraction agent for an expense system.

You are given OCR output (Amazon Textract AnalyzeExpense) for one receipt, the
user's expense profile, and PRE-PARSED line items from a deterministic table
parser. Your job:
1. Read the OCR summary fields. Trust the pre-parsed line items when provided;
   only re-derive line items yourself if the parser returned few/none.
2. Infer the expense category from the user's default category and history.
3. Produce a clean structured expense by calling submit_expense ONCE.

Rules:
- Normalize the merchant. Use ISO 8601 (YYYY-MM-DD) for the date.
- subtotal + tax + tip should reconcile to total. If the numbers are ambiguous or
  don't add up, still submit your best extraction but set a LOW confidence.
- Set confidence 0-100 honestly. line_items is a JSON array string of
  {description, qty, unitPrice, amount}.
- You MUST finish by calling submit_expense with every field filled in.
"""

VALIDATOR_PROMPT = """You are an independent validation agent for an expense system.

You receive the ORIGINAL OCR output and the extractor's structured expense. You did
NOT do the extraction — review it skeptically and independently. Check:
- Do subtotal + tax + tip reconcile to total?
- Is the category plausible for this merchant and the user's profile?
- Is the merchant specific (not vague/empty)? Is the date plausible?
- Is the extractor's confidence justified?

Decide routing:
- AUTO_PERSIST only when the extraction is clearly correct and reconciles.
- NEEDS_REVIEW when anything is off: totals don't reconcile, the merchant/category
  is questionable, large amounts with weak evidence, or low extractor confidence.
Be conservative: when in doubt, NEEDS_REVIEW.

You MUST finish by calling submit_validation with routing, confidence, notes, concerns.
"""

QUERY_PROMPT = """You are a helpful expense assistant. The user asks questions about
THEIR OWN expenses. Answer using ONLY the tools provided — get_user_profile,
get_recent_expenses, lookup_merchant — which read this user's data.

Rules:
- Always look up real data with the tools before answering. NEVER invent merchants,
  amounts, dates, or counts. If the tools return nothing, say so plainly.
- When the user asks about spending at a merchant, list the matching expenses and sum
  them. When they ask about recent activity, summarize the most recent expenses.
- Be concise and concrete: name merchants, amounts (with currency), and dates.
- You can only READ. You cannot create, edit, or delete an expense — if asked to, say
  that isn't something you can do.
"""


def _mcp_client() -> MCPClient:
    def _transport():
        token = get_gateway_token()
        headers = {"Authorization": f"Bearer {token}"} if token else None
        return streamablehttp_client(GATEWAY_URL, headers=headers)

    return MCPClient(_transport)


def _tool_name(tools, suffix: str, default: str) -> str:
    for t in tools:
        tn = getattr(t, "tool_name", "")
        if tn == suffix or tn.endswith(suffix) or suffix in tn:
            return tn
    return default


@app.entrypoint
def invoke(payload, context):
    """Entrypoint wrapper: run the pipeline, then emit ONE run-ledger event capturing
    the outcome — every fate, including errors and unhandled exceptions. The emit is
    best-effort (never changes what the caller gets back), so the operational ledger
    (the ProcessingRuns table, fed via EventBridge) records what happened to every
    receipt without the agent's hot path depending on it. This is what makes
    'what happened to receipt X?' a one-lookup question instead of a log dig."""
    parsed = parse_payload(payload)
    s3_uri = parsed.get("s3_uri")
    user_id = parsed.get("user_id", "anonymous")
    try:
        result = _process(payload)
    except Exception as exc:  # noqa: BLE001 — record the failure, then re-raise
        log.error("unhandled processing error: %s", exc)
        _emit_run_ledger(s3_uri, user_id, {"status": "error", "error": str(exc)})
        raise
    _emit_run_ledger(s3_uri, user_id, result)
    return result


def _process(payload, context=None):
    """Dual-agent receipt processing (extractor -> independent validator)."""
    payload = parse_payload(payload)
    s3_uri = payload.get("s3_uri")
    user_id = payload.get("user_id", "anonymous")

    # Conversational query mode: a payload with a `question` (and no receipt) asks
    # about the user's OWN expenses. SECURITY: the user_id is taken from a VERIFIED
    # signed identity token (KMS HMAC), NEVER from the request body — editing the body
    # user_id cannot retrieve another user's data (the IDOR guard). The agent answers
    # from the Gateway READ tools only (structurally can't write), pinned server-side
    # to the verified id.
    question = payload.get("question") or payload.get("query")
    if question and not s3_uri:
        identity_token = payload.get("identity_token") or payload.get("_identity")
        try:
            verified_user = verify_identity(identity_token or "", IDENTITY_KEY_ID, REGION)
        except Exception as exc:  # noqa: BLE001 — fail closed: no valid identity, no data
            log.warning("query identity rejected: %s", exc)
            return {"mode": "query", "error": "unauthorized: missing or invalid identity token"}
        return _answer_query(verified_user, str(question))

    # Degradation ladder (spec §6): resolve the active rung from AppConfig (cached;
    # safe L0 default if unavailable). The rung sets the model + which features run.
    active = get_active_rung()
    rung = active["rung"]
    features = active["features"]
    # Tag the trace span with the rung up front so even a defer/OCR-fail trace is
    # marked with the rung it ran on (spec §6.4). Re-tagged after any step-down.
    _tag_span_rung(rung, active["model"])

    if not s3_uri:
        return {"error": "s3_uri is required", "received": payload}

    # L4 — defer: no model call. Queue the receipt for replay and return (spec §6.1).
    if active["defer"]:
        deferred = _defer_receipt(s3_uri, user_id, rung)
        return {"status": "deferred", "rung": rung, "deferred": deferred, "needs_review": True, "s3_uri": s3_uri}

    reset_state()
    session_id = f"receipt-{user_id}-{uuid.uuid4().hex}"

    # 1) OCR.
    try:
        ocr = analyze_receipt(s3_uri)
    except Exception as exc:
        log.error("OCR failed: %s", exc)
        return {"error": f"OCR failed: {exc}", "s3_uri": s3_uri, "rung": rung}

    # Deterministic line-item table parse (hybrid: parser first, LLM fallback).
    parsed_items = parse_line_items(ocr["line_items"])
    parse_rate = parse_success_rate(ocr["line_items"], parsed_items)

    # Memory only if the rung allows reads (sheddable feature, spec §6.1).
    session_manager = None
    if features.get("memoryRead"):
        try:
            session_manager = get_memory_session_manager(session_id, user_id)
        except Exception as exc:
            log.warning("Memory unavailable: %s", exc)

    extractor_prompt = (
        f"User id: {user_id}\n\n"
        f"OCR (Textract AnalyzeExpense), overall confidence {ocr['overall_confidence']}:\n"
        f"{ocr['raw_text']}\n\n"
        f"Pre-parsed line items (parser success rate {parse_rate}):\n"
        f"{json.dumps(parsed_items)}\n\n"
        "Extract the expense and call submit_expense."
    )

    with _mcp_client() as gateway:
        gateway_tools = gateway.list_tools_sync()

        # 2) Extractor agent, run inside the in-agent 503 step-down loop (spec §6.3).
        # A persistent 503 (model capacity) steps to the next rung's model FOR THIS
        # RUN; 429/500 back off + retry the SAME model. A test hook can inject a 503.
        run_rung = rung
        run_model = active["model"]
        step_downs = []
        sim_503 = _sim_503_count(payload)
        # NOTE (deferred, see tests/test_e2e_stepdown_live.py): the live 503 sim
        # surfaced a reporting check to revisit — confirm the returned `rung`/`model`
        # always reflect the rung the extraction SUCCEEDED on after a step-down. The
        # step-down decision logic itself is unit-tested (classify_model_error/next_rung).

        while True:
            try:
                extractor = Agent(
                    model=load_model(model_id=run_model, model_config={"cache_prompt": "default"}),
                    system_prompt=EXTRACTOR_PROMPT,
                    tools=[submit_expense],
                    session_manager=session_manager,
                )
                if sim_503 > 0:  # fault injection (env-gated) — simulate a 503 this attempt
                    sim_503 -= 1
                    raise _fake_503(run_model)
                extractor(extractor_prompt)
                break  # success on run_model
            except Exception as exc:
                action = classify_model_error(exc)
                if action == "backoff":
                    time.sleep(_backoff_jitter(len(step_downs)))
                    continue
                if action == "step":
                    nxt = next_rung(run_rung)
                    nxt_rung = rung_for(nxt) if nxt else None
                    if not nxt_rung or nxt_rung["defer"]:
                        # bottomed out -> defer the receipt (spec §6.1 L4)
                        deferred = _defer_receipt(s3_uri, user_id, run_rung)
                        return {
                            "status": "deferred",
                            "rung": run_rung,
                            "deferred": deferred,
                            "needs_review": True,
                            "step_downs": step_downs,
                            "reason": "503 persisted to the bottom of the ladder",
                            "s3_uri": s3_uri,
                        }
                    step_downs.append({"from": run_rung, "to": nxt, "cause": "503"})
                    _emit_step_down_metric(run_rung, nxt)
                    run_rung, run_model = nxt, nxt_rung["model"]
                    continue
                raise  # not a ladder error — propagate

        rung = run_rung  # the rung that actually produced the extraction
        if step_downs:  # re-tag the span if a 503 stepped us to a different rung/model
            _tag_span_rung(rung, run_model)
        expense = get_last_expense()
        if not expense:
            return {"error": "extractor did not submit an expense", "rung": rung, "step_downs": step_downs}

        # 3) Independent validator agent — a sheddable rung feature (spec §6.1).
        # When the rung runs no validator (L2 down) or forces review, everything
        # routes to human_review (degrade-safe, never auto-persist unchecked).
        if features.get("validator"):
            validator = Agent(
                model=load_model(model_id=active["model"]),
                system_prompt=VALIDATOR_PROMPT,
                tools=[submit_validation],
            )
            validator(
                f"Original OCR:\n{ocr['raw_text']}\n\n"
                f"Extractor's structured expense:\n{json.dumps(expense, default=str)}\n\n"
                "Validate it and call submit_validation."
            )
            validation = get_last_validation()
            routing = validation.get("routing", "NEEDS_REVIEW")  # fail safe
        else:
            validation = {"routing": "NEEDS_REVIEW", "notes": f"validator shed at rung {rung}", "confidence": 0}
            routing = "NEEDS_REVIEW"

        needs_review = routing != "AUTO_PERSIST" or features.get("forceReview", False)

        # 4) The validator owns the decision. Persist or route to review.
        save_name = _tool_name(gateway_tools, "save_expense", "save_expense")
        review_name = _tool_name(gateway_tools, "human_review", "human_review")
        common = {
            "user_id": user_id,
            "merchant": expense["merchant"],
            "transaction_date": expense["transaction_date"],
            "currency": expense["currency"],
            "total": expense["total"],
            "category": expense["category"],
            "line_items": expense["line_items"],
            "rung": rung,
            "source_receipt_s3": s3_uri,
        }

        cedar_blocked = False
        if not needs_review:
            # Try to persist. Cedar may DENY this at the gateway (e.g. total over
            # the threshold) — a deterministic guardrail independent of the agents
            # (spec §5.5). If denied, fall back to human_review.
            save_result = gateway.call_tool_sync(
                tool_use_id=uuid.uuid4().hex,
                name=save_name,
                arguments={
                    **common,
                    "subtotal": expense["subtotal"],
                    "tax": expense["tax"],
                    "tip": expense["tip"],
                    "payment_method": expense["payment_method"],
                    "status": "processed",
                },
            )
            if _is_denied(save_result):
                cedar_blocked = True
                needs_review = True
            else:
                result = save_result
                status = "processed"

        if needs_review:
            reason = (
                "blocked by policy (amount over threshold)"
                if cedar_blocked
                else (validation.get("concerns") or "validator routed to review")
            )
            result = gateway.call_tool_sync(
                tool_use_id=uuid.uuid4().hex,
                name=review_name,
                arguments={**common, "reason": reason},
            )
            status = "needs_review"

    return {
        "status": status,
        "rung": rung,
        "needs_review": needs_review,
        "cedar_blocked": cedar_blocked,
        "step_downs": step_downs,
        "model": run_model,
        "extractor_confidence": expense["confidence"],
        "validator": validation,
        "parse_rate": parse_rate,
        "expense": expense,
        "tool_result": _stringify(result),
    }


def _answer_query(user_id: str, question: str) -> dict:
    """Conversational, read-only: answer the user's question about THEIR OWN expenses.

    SECURITY — the user_id is the VERIFIED one (from the signed token, not the body),
    and it is PINNED server-side: the tools the agent sees take NO user_id argument, so
    the model physically cannot request another user's partition (defense-in-depth on
    top of the verified identity — even a prompt-injected 'show me user-012' can't
    escape). Read-only tool belt: no save_expense/human_review, so a query can't write."""
    from strands import tool

    with _mcp_client() as gateway:
        gw_tools = gateway.list_tools_sync()
        profile_tool = _tool_name(gw_tools, "get_user_profile", "get_user_profile")
        recent_tool = _tool_name(gw_tools, "get_recent_expenses", "get_recent_expenses")
        merchant_tool = _tool_name(gw_tools, "lookup_merchant", "lookup_merchant")

        def _call(name: str, args: dict) -> str:
            """Call a Gateway tool and return the CLEAN payload the tool produced — the
            text inside the MCP result's content blocks, not the raw envelope. Handing
            the model the whole `MCPToolResult` (status/content/toolUseId) as multiply-
            escaped JSON made it unreliable at reading its own tool output (it sometimes
            declared 'no data' over data it received). Extract content[].text instead."""
            res = gateway.call_tool_sync(tool_use_id=uuid.uuid4().hex, name=name, arguments=args)
            content = res.get("content") if isinstance(res, dict) else getattr(res, "content", None)
            if content:
                texts = []
                for block in content:
                    t = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
                    if not t:
                        continue
                    # The tool Lambda returns json.dumps(...), and the MCP layer wraps
                    # that again — so content[].text is often a JSON-string-OF-a-JSON
                    # string (verified in the live tool_result). Peel one extra layer
                    # when present so the model gets clean JSON, not escaped soup.
                    try:
                        inner = json.loads(t)
                        t = inner if isinstance(inner, str) else json.dumps(inner)
                    except (ValueError, TypeError):
                        pass
                    texts.append(t)
                if texts:
                    return "\n".join(texts)
            return _stringify(res)  # fallback: never hide an unexpected shape

        # Local wrappers that CLOSE OVER the verified user_id. The agent never supplies
        # it — these signatures expose only query-relevant args.
        @tool
        def my_profile() -> str:
            """Get the current user's expense profile (cost center, default category, currency)."""
            return _call(profile_tool, {"user_id": user_id})

        @tool
        def my_recent_expenses(limit: int = 20) -> str:
            """List the current user's most recent expenses (newest first). Use this to
            answer questions about spending, merchants, totals, or recent activity."""
            return _call(recent_tool, {"user_id": user_id, "limit": max(1, min(int(limit), 100))})

        @tool
        def lookup_merchant(name: str) -> str:
            """Normalize/look up a merchant name against the catalog."""
            return _call(merchant_tool, {"name": name})

        agent = Agent(
            model=load_model(model_id=DEFAULT_MODEL_ID),
            system_prompt=QUERY_PROMPT,
            tools=[my_profile, my_recent_expenses, lookup_merchant],
        )
        reply = agent(f"User {user_id} asks: {question}")

    return {"mode": "query", "user_id": user_id, "answer": str(reply)}


def _backoff_jitter(attempt: int) -> float:
    """Exponential backoff with jitter for 429/500 retries (spec §6.3). Capped."""
    return min(0.5 * (2**attempt) + random.uniform(0, 0.25), 4.0)


def _tag_span_rung(rung: str, model: str, needs_review: bool | None = None) -> None:
    """Tag the current OTel span with the ladder rung so degraded runs are visible in
    traces (spec §6.4 — 'degrade safe, not silent'). The managed Runtime configures
    ADOT/OTel; we just stamp attributes on the active span. Best-effort: never break a
    receipt run over telemetry, and stay importable without opentelemetry installed."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span is None:
            return
        span.set_attribute("receipts.ladder.rung", rung)
        span.set_attribute("receipts.ladder.model", model)
        span.set_attribute("receipts.ladder.degraded", rung not in ("L0",))
        if needs_review is not None:
            span.set_attribute("receipts.needs_review", needs_review)
    except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
        log.warning("span rung-tag failed: %s", exc)


def _emit_step_down_metric(from_rung: str, to_rung: str) -> None:
    """Emit the account-level ladder signal (spec §6.3 path 2). A 503 the agent
    recovers from is a SUCCESSFUL Runtime invocation, so it never appears as a
    Runtime System Error metric. This custom ModelStepDowns metric is the honest
    signal the controller's alarm watches to step activeRung down for everyone.
    Best-effort: a metric failure must never break receipt processing."""
    try:
        import boto3

        boto3.client("cloudwatch", region_name=REGION).put_metric_data(
            Namespace="ReceiptsAgent/Ladder",
            MetricData=[
                {
                    "MetricName": "ModelStepDowns",
                    "Value": 1,
                    "Unit": "Count",
                    "Dimensions": [{"Name": "FromRung", "Value": from_rung}],
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
        log.warning("ModelStepDowns metric emit failed: %s", exc)


def _emit_run_ledger(s3_uri, user_id, result) -> None:
    """Emit ONE run-ledger event per receipt to EventBridge (operational audit). A
    writer Lambda upserts the ProcessingRuns table from it, and an error rule pushes
    to SNS — so an admin SEES failures/reviews and can look up any receipt in one
    query, instead of digging across log groups. Best-effort + fully decoupled: if
    the bus is unset (local dev) or PutEvents fails, the receipt result is unaffected."""
    if not RUN_EVENT_BUS:
        return
    try:
        import boto3

        detail = build_run_event(s3_uri, user_id, result)
        boto3.client("events", region_name=REGION).put_events(
            Entries=[
                {
                    "Source": "receipts.agent",
                    "DetailType": "ReceiptProcessed",
                    "Detail": json.dumps(detail, default=str),
                    "EventBusName": RUN_EVENT_BUS,
                }
            ]
        )
    except Exception as exc:  # noqa: BLE001 — audit emit is best-effort
        log.warning("run-ledger emit failed: %s", exc)


# Fault injection for live e2e of the 503 step-down. Gated by an env flag so it is
# NOT a production backdoor: only honored when ALLOW_FAULT_INJECTION=true.
_FAULT_INJECTION = os.getenv("ALLOW_FAULT_INJECTION", "").lower() == "true"


def _sim_503_count(payload: dict) -> int:
    """How many leading model attempts to fail with a simulated 503 (test hook)."""
    if not _FAULT_INJECTION:
        return 0
    try:
        return max(0, int(payload.get("simulate_503", 0)))
    except (TypeError, ValueError):
        return 0


def _fake_503(model_id: str) -> Exception:
    """A botocore-shaped ServiceUnavailable error for the step-down test hook."""
    exc = Exception(f"ServiceUnavailableException (simulated) for {model_id}")
    exc.response = {"Error": {"Code": "ServiceUnavailableException"}}  # type: ignore[attr-defined]
    return exc


def _defer_receipt(s3_uri: str, user_id: str, rung: str) -> bool:
    """L4 defer (spec §6.1): queue the receipt to SQS for replay when the model
    tier recovers. Returns True if queued. Never drops the document — if the queue
    isn't configured, report not-queued so the caller surfaces it."""
    if not DEFER_QUEUE_URL:
        log.warning("L4 defer but no DEFER_QUEUE_URL configured")
        return False
    import boto3

    boto3.client("sqs", region_name=REGION).send_message(
        QueueUrl=DEFER_QUEUE_URL,
        MessageBody=json.dumps({"s3_uri": s3_uri, "user_id": user_id, "deferred_at_rung": rung}),
    )
    return True


def _is_denied(result) -> bool:
    """True if an MCP tool call was denied/errored (e.g. blocked by a Cedar policy).

    A gateway policy denial surfaces as an error-status ToolResult rather than a
    raised exception, so we inspect status + content text defensively.
    """
    try:
        status = result.get("status") if isinstance(result, dict) else getattr(result, "status", None)
    except Exception:
        status = None
    if status == "error":
        return True
    blob = _stringify(result).lower()
    return any(k in blob for k in ("denied", "not authorized", "forbidden", "policy"))


def _stringify(result) -> str:
    try:
        return json.dumps(result, default=str)[:2000]
    except Exception:
        return str(result)[:2000]


if __name__ == "__main__":
    app.run()
