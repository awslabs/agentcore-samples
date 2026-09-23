"""MCP server used to test AgentCore Gateway streaming + sessions + elicitation.

Each tool maps to a feature in `MCP_feature_test_plan_for_AgentCore_Gateway...md`:
  * streaming_demo       → Feature 1 (Streamable HTTP) + Feature 8 (progress)
  * session_counter      → Feature 2 (session continuity)
  * book_room            → Feature 3.3 (form elicitation)
  * cancel_with_confirm  → Feature 3.3 (boolean confirm)
  * log_expense          → Feature 3.7 (sequential elicitation)
"""

import asyncio
import uuid  # noqa: F401  -- used by URL-mode elicitation tools below
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Literal

from fastmcp import Context, FastMCP
from pydantic import BaseModel, Field


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Per-process state store. Counters are keyed by `Mcp-Session-Id` so the
    gateway's session-mapping behaviour is observable end-to-end."""
    yield {
        "session_counters": defaultdict(int),
        # Per-session call counter for Flow 4.3 (URLElicitationRequiredError retry).
        "protected_resource_calls": defaultdict(int),
    }


mcp = FastMCP(
    name="labstateful",
    lifespan=lifespan,
)


# --- Request logging middleware -----------------------------------------
# Logs every MCP message plus (best-effort) the underlying HTTP request
# headers. Useful for debugging what AgentCore Gateway forwards to the runtime.
# Wrapped in try/except so an API mismatch can't crash the server on boot.

try:
    from fastmcp.server.middleware import Middleware, MiddlewareContext

    class HeaderLogMiddleware(Middleware):
        @staticmethod
        def _tool_name(context):
            # tools/call params live in context.message; shape depends on fastmcp version
            msg = getattr(context, "message", None)
            for path in ("name", "params.name", "tool"):
                obj = msg
                for part in path.split("."):
                    obj = getattr(obj, part, None) if obj is not None else None
                if isinstance(obj, str):
                    return obj
            return None

        async def on_message(self, context: MiddlewareContext, call_next):
            tool = self._tool_name(context) if context.method == "tools/call" else None
            tool_suffix = f" tool={tool!r}" if tool else ""
            # Dump params for initialize so we can audit gateway capability negotiation.
            params_suffix = ""
            if context.method == "initialize":
                msg = getattr(context, "message", None)
                params = getattr(msg, "params", None) if msg is not None else None
                if params is not None:
                    try:
                        dumped = params.model_dump(mode="json", exclude_none=True)
                    except Exception:
                        dumped = repr(params)[:300]
                    params_suffix = f" params={dumped}"
            try:
                req = context.fastmcp_context.request_context.request
                print(
                    f"[mcp] {req.method} {req.url.path} | "
                    f"{context.source}:{context.type} method={context.method!r}{tool_suffix}{params_suffix}",
                    flush=True,
                )
                for k, v in req.headers.items():
                    if k.lower() == "authorization" and len(v) > 28:
                        v = v[:25] + "..."
                    print(f"    {k}: {v[:120]}", flush=True)
            except Exception:
                # Notifications and some intermediate messages have no HTTP context.
                print(
                    f"[mcp] {context.source}:{context.type} method={context.method!r}{tool_suffix}{params_suffix}",
                    flush=True,
                )
            return await call_next(context)

    mcp.add_middleware(HeaderLogMiddleware())
    print("[init] HeaderLogMiddleware registered", flush=True)
except (ImportError, AttributeError) as e:
    print(f"[init] header logging disabled: {e}", flush=True)


def _session_id(ctx: Context) -> str:
    """Best-effort session ID extraction across fastmcp versions."""
    return (
        getattr(ctx, "session_id", None)
        or getattr(getattr(ctx, "session", None), "session_id", None)
        or "unknown"
    )


# --- Streaming / progress ------------------------------------------------


@mcp.tool()
async def streaming_demo(ctx: Context, steps: int = 5) -> str:
    """Emit `steps` progress notifications over ~`steps/2` seconds, then return."""
    for i in range(steps):
        await ctx.report_progress(
            progress=i + 1, total=steps, message=f"Step {i + 1}/{steps}"
        )
        await asyncio.sleep(0.5)
    return f"Completed {steps} steps."


# --- Session-stateful counter -------------------------------------------


@mcp.tool()
async def session_counter(ctx: Context) -> dict:
    """Per-session counter — calls within the same `Mcp-Session-Id` see incrementing values."""
    counters = ctx.request_context.lifespan_context["session_counters"]
    sid = _session_id(ctx)
    counters[sid] += 1
    return {"session_id": sid, "count": counters[sid]}


# --- Elicitation: form mode --------------------------------------------


class BookingDetails(BaseModel):
    room_type: Literal["single", "double", "suite"]
    nights: int = Field(ge=1, le=30)
    breakfast: bool = False


@mcp.tool()
async def book_room(ctx: Context) -> str:
    """Form elicitation — server pauses for booking details."""
    result = await ctx.elicit("Please provide booking details", BookingDetails)
    if result.action == "accept":
        d = result.data  # BookingDetails instance
        return (
            f"Booked {d.room_type} for {d.nights} night(s); breakfast: {d.breakfast}."
        )
    return f"Booking {result.action}ed."


class Confirmation(BaseModel):
    confirm: bool


@mcp.tool()
async def cancel_with_confirm(ctx: Context, order_id: int) -> str:
    """Single-field boolean elicitation before a destructive action."""
    result = await ctx.elicit(
        f"Cancel order {order_id}? This is irreversible.",
        Confirmation,
    )
    if result.action == "accept" and result.data.confirm:
        return f"Order {order_id} cancelled."
    return f"Order {order_id} kept (action={result.action})."


# --- Elicitation: sequential -------------------------------------------


class _Category(BaseModel):
    category: Literal["food", "travel", "office", "other"]


class _Description(BaseModel):
    description: str


class _Submit(BaseModel):
    submit: bool


@mcp.tool()
async def log_expense(ctx: Context, amount: float) -> str:
    """Three sequential elicitations within one tool call (category → description → confirm)."""
    cat = await ctx.elicit("Select expense category", _Category)
    if cat.action != "accept":
        return f"Stopped at category step ({cat.action})."

    desc = await ctx.elicit(
        f"Describe this {cat.data.category} expense (${amount:.2f})",
        _Description,
    )
    if desc.action != "accept":
        return f"Stopped at description step ({desc.action})."

    conf = await ctx.elicit(
        (
            f"Submit ${amount:.2f} {cat.data.category} expense: "
            f"'{desc.data.description}'?"
        ),
        _Submit,
    )
    if conf.action == "accept" and conf.data.submit:
        return f"Logged: ${amount:.2f} {cat.data.category} — {desc.data.description}"
    return f"Not submitted (action={conf.action})."


# --- Keep-alive timeout probe -----------------------------------------
# Sleep for `duration_seconds`, optionally emitting a progress notification
# every `interval_seconds`. Used to characterise the gateway's session /
# streaming timeout behaviour at various durations and with/without
# progress notifications acting as keep-alives.


@mcp.tool()
async def keepalive_demo(
    ctx: Context,
    duration_seconds: int = 60,
    interval_seconds: int = 30,
    emit_progress: bool = True,
) -> dict:
    """Sleep for `duration_seconds`, optionally emitting progress notifications.

    With `emit_progress=True` the tool acts as a keep-alive — every
    `interval_seconds` it sends a `notifications/progress` SSE frame so the
    gateway sees the connection is alive. With `emit_progress=False` no
    progress flows during the sleep, exposing the gateway's bare
    request-timeout.
    """
    started = asyncio.get_event_loop().time()
    iterations = 0
    while True:
        elapsed = asyncio.get_event_loop().time() - started
        if elapsed >= duration_seconds:
            break
        if emit_progress:
            await ctx.report_progress(
                progress=int(elapsed),
                total=duration_seconds,
                message=f"alive at {int(elapsed)}s/{duration_seconds}s",
            )
        iterations += 1
        sleep_for = min(interval_seconds, duration_seconds - elapsed)
        if sleep_for <= 0:
            break
        await asyncio.sleep(sleep_for)
    return {
        "duration_seconds_requested": duration_seconds,
        "elapsed_seconds": round(asyncio.get_event_loop().time() - started, 1),
        "iterations": iterations,
        "emit_progress": emit_progress,
    }


# --- Long-running optimization with human-in-the-loop confirmation ----
# Use case: enterprise AI assistant runs a long compute (>5 min), keeps the
# session alive via 30-second progress notifications, then surfaces an
# elicitation prompt before any write. Tests Streamable HTTP keep-alive +
# bidirectional elicitation in one tool call.


class _ApplyConfirmation(BaseModel):
    confirm: bool


@mcp.tool()
async def optimize_and_apply(
    ctx: Context,
    duration_seconds: int = 30,
    interval_seconds: int = 5,
) -> str:
    """Long-running optimization, then a confirmation prompt before applying.

    Mirrors AI-assistant workloads: spin for `duration_seconds`, emit a
    progress notification every `interval_seconds` to keep the SSE channel
    alive, then ask the user to approve a destructive action via
    `elicitation/create`, then "apply" or skip based on the response.
    """
    iterations = max(1, duration_seconds // interval_seconds)
    for i in range(iterations):
        await ctx.report_progress(
            progress=(i + 1) * interval_seconds,
            total=duration_seconds,
            message=f"Optimizing — iteration {i + 1}/{iterations}",
        )
        await asyncio.sleep(interval_seconds)

    recommendations = ["INIT-101", "INIT-102", "INIT-103"]
    confirm = await ctx.elicit(
        (
            f"Optimization complete after {duration_seconds}s. "
            f"Apply {len(recommendations)} recommendations "
            f"({', '.join(recommendations)})?"
        ),
        _ApplyConfirmation,
    )
    if confirm.action == "accept" and confirm.data.confirm:
        return f"✅ Applied {len(recommendations)} recommendations after user approval."
    return f"❌ User {confirm.action}ed; recommendations not applied."


@mcp.tool()
async def apply_recommendations(ctx: Context, change_ids: list[str]) -> str:
    """Stand-alone elicitation gate: pause for human approval before any write."""
    msg = (
        f"Apply {len(change_ids)} change(s): {', '.join(change_ids)}? "
        f"This modifies production data."
    )
    result = await ctx.elicit(msg, _ApplyConfirmation)
    if result.action == "accept" and result.data.confirm:
        return f"✅ Applied {len(change_ids)} change(s)."
    return f"❌ No changes applied (action={result.action})."


# --- Mid-stream error & logging & sampling ----------------------------


@mcp.tool()
async def failing_demo(ctx: Context, steps: int = 3) -> str:
    """Emit `steps` progress notifications then raise — tests mid-stream error path.

    Per the AgentCore Gateway streaming announcement: if an error occurs mid-stream
    it is delivered as a JSON-RPC error inside an SSE frame (HTTP status was already
    sent on the first event)."""
    for i in range(steps):
        await ctx.report_progress(
            progress=i + 1, total=steps, message=f"Step {i + 1}/{steps}"
        )
        await asyncio.sleep(0.3)
    raise RuntimeError("intentional failure for mid-stream error test")


@mcp.tool()
async def logging_demo(ctx: Context) -> str:
    """Emit one log event at each severity (`notifications/message` events)."""
    await ctx.debug("debug message")
    await ctx.info("info message")
    await ctx.warning("warning message")
    await ctx.error("error message")
    return "logged"


@mcp.tool()
async def sampling_demo(ctx: Context, prompt: str) -> str:
    """Ask the connected client's LLM (via `sampling/createMessage`) to echo the prompt."""
    result = await ctx.sample(
        messages=prompt,
        system_prompt="Echo the user's message verbatim.",
        max_tokens=64,
    )
    # fastmcp 3.x returns a TextContent-like object; .text attribute holds the string
    return getattr(result, "text", str(result))


# --- Trivial sync tools (sanity checks) --------------------------------


@mcp.tool()
def getOrder() -> int:
    """Get an order."""
    return 123


@mcp.tool()
def updateOrder(orderId: int) -> int:
    """Update an existing order."""
    return 456


# --- URL-mode elicitation (MCP spec 2025-11-25) -----------------------
# fastmcp 3.2.4's ctx.elicit() is form-mode only — URL mode is reached via
# the underlying ServerSession on ctx.request_context.session.


@mcp.tool()
async def connect_external_account(
    ctx: Context, completion_delay_s: float = 2.0
) -> dict:
    """Flow 4.2: server-initiated URL elicitation, then completion notification.

    Sends `elicitation/create` with `mode: url`, awaits the client's
    `{action: "accept"|"decline"|"cancel"}`, sleeps `completion_delay_s`, then
    sends `notifications/elicitation/complete`. Returns what happened.
    """
    eid = str(uuid.uuid4())
    url = f"https://example.com/connect?eid={eid}"
    session = ctx.request_context.session
    related_id = ctx.request_context.request_id

    elicit_result = await session.elicit_url(
        message="Connect your demo account to continue.",
        url=url,
        elicitation_id=eid,
        related_request_id=related_id,
    )
    action = getattr(elicit_result, "action", str(elicit_result))

    completion_sent = False
    if action == "accept":
        await asyncio.sleep(completion_delay_s)
        await session.send_elicit_complete(
            elicitation_id=eid, related_request_id=related_id
        )
        completion_sent = True

    return {
        "action": action,
        "elicitation_id": eid,
        "url": url,
        "completion_sent": completion_sent,
    }


@mcp.tool()
async def protected_resource(ctx: Context) -> dict:
    """Flow 4.3: per-session call counter — first call raises
    `UrlElicitationRequiredError(-32042)` with one URL elicitation; second call
    succeeds. Tests round-tripping of the structured error and client retry.

    fastmcp 3.2.4's `_call_tool` wraps generic `Exception` into `ToolError`
    (which surfaces as `result.isError=true`), but its `except FastMCPError:
    raise` clause preserves any subclass. So we subclass both
    `UrlElicitationRequiredError` and `FastMCPError` — fastmcp lets it through,
    then mcp lowlevel's explicit `except UrlElicitationRequiredError: raise`
    converts it to a JSON-RPC `-32042` frame.
    """
    from fastmcp.exceptions import FastMCPError
    from mcp.shared.exceptions import UrlElicitationRequiredError
    from mcp.types import ElicitRequestURLParams

    class _UrlElicitFastMCP(UrlElicitationRequiredError, FastMCPError):
        pass

    counters = ctx.request_context.lifespan_context["protected_resource_calls"]
    sid = _session_id(ctx)
    counters[sid] += 1
    n = counters[sid]

    if n == 1:
        eid = str(uuid.uuid4())
        raise _UrlElicitFastMCP(
            [
                ElicitRequestURLParams(
                    message="Authorization required to access this resource.",
                    url=f"https://example.com/authorize?eid={eid}",
                    elicitationId=eid,
                )
            ]
        )

    return {"session_id": sid, "call": n, "status": "ok"}


if __name__ == "__main__":
    # stateless_http=False keeps the SSE push-back channel (required for
    # elicitation + progress streaming).
    mcp.run(transport="streamable-http", host="0.0.0.0", stateless_http=False)  # nosec B104 - AgentCore Runtime container requires bind to all interfaces
