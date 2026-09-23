"""MCP server for the elicitation + sampling notebook (06-elicitation.ipynb).

Demonstrates:
  - Form-mode elicitation: single, boolean, sequential   (book_room, cancel_with_confirm, log_expense)
  - Sampling (`sampling/createMessage`)                  (sampling_demo)
  - Long-compute + form-mode confirmation                (optimize_and_apply)
  - URL-mode elicitation Flow 4.2                        (connect_external_account)
  - URL Required Error Flow 4.3                          (protected_resource)

This server is deployed to AgentCore Runtime and surfaced through a gateway
configured with BOTH `streamingConfiguration.enableResponseStreaming: true`
AND `sessionConfiguration` (sessions enabled). Elicitation requires both.
No Lambda interceptor.
"""

import asyncio
import uuid  # noqa: F401  -- used by URL-elicitation tools below
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Literal

from fastmcp import Context, FastMCP
from pydantic import BaseModel, Field


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Per-process state. `protected_resource_calls` is keyed by session id
    for the Flow 4.3 retry pattern."""
    yield {"protected_resource_calls": defaultdict(int)}


mcp = FastMCP(name="labelicitation", lifespan=lifespan)


def _session_id(ctx: Context) -> str:
    return (
        getattr(ctx, "session_id", None)
        or getattr(getattr(ctx, "session", None), "session_id", None)
        or "unknown"
    )


# --- Form-mode elicitation ---------------------------------------------


class BookingDetails(BaseModel):
    room_type: Literal["single", "double", "suite"]
    nights: int = Field(ge=1, le=30)
    breakfast: bool = False


@mcp.tool()
async def book_room(ctx: Context) -> str:
    """Form elicitation — server pauses for booking details."""
    result = await ctx.elicit("Please provide booking details", BookingDetails)
    if result.action == "accept":
        d = result.data
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
        f"Cancel order {order_id}? This is irreversible.", Confirmation
    )
    if result.action == "accept" and result.data.confirm:
        return f"Order {order_id} cancelled."
    return f"Order {order_id} kept (action={result.action})."


class _Category(BaseModel):
    category: Literal["food", "travel", "office", "other"]


class _Description(BaseModel):
    description: str


class _Submit(BaseModel):
    submit: bool


@mcp.tool()
async def log_expense(ctx: Context, amount: float) -> str:
    """Three sequential elicitations (category → description → confirm)."""
    cat = await ctx.elicit("Select expense category", _Category)
    if cat.action != "accept":
        return f"Stopped at category step ({cat.action})."

    desc = await ctx.elicit(
        f"Describe this {cat.data.category} expense (${amount:.2f})", _Description
    )
    if desc.action != "accept":
        return f"Stopped at description step ({desc.action})."

    conf = await ctx.elicit(
        f"Submit ${amount:.2f} {cat.data.category} expense: '{desc.data.description}'?",
        _Submit,
    )
    if conf.action == "accept" and conf.data.submit:
        return f"Logged: ${amount:.2f} {cat.data.category} — {desc.data.description}"
    return f"Not submitted (action={conf.action})."


# --- Long-compute + elicitation gate -----------------------------------


class _ApplyConfirmation(BaseModel):
    confirm: bool


@mcp.tool()
async def optimize_and_apply(
    ctx: Context, duration_seconds: int = 30, interval_seconds: int = 5
) -> str:
    """Long-running optimization, then a confirmation prompt before applying."""
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
        f"Optimization complete after {duration_seconds}s. "
        f"Apply {len(recommendations)} recommendations "
        f"({', '.join(recommendations)})?",
        _ApplyConfirmation,
    )
    if confirm.action == "accept" and confirm.data.confirm:
        return f"Applied {len(recommendations)} recommendations after user approval."
    return f"User {confirm.action}ed; recommendations not applied."


@mcp.tool()
async def apply_recommendations(ctx: Context, change_ids: list[str]) -> str:
    """Stand-alone elicitation gate — pause for human approval."""
    result = await ctx.elicit(
        f"Apply {len(change_ids)} change(s): {', '.join(change_ids)}? "
        f"This modifies production data.",
        _ApplyConfirmation,
    )
    if result.action == "accept" and result.data.confirm:
        return f"Applied {len(change_ids)} change(s)."
    return f"No changes applied (action={result.action})."


# --- Sampling ----------------------------------------------------------


@mcp.tool()
async def sampling_demo(ctx: Context, prompt: str) -> str:
    """Ask the connected client's LLM (via `sampling/createMessage`) to echo the prompt."""
    result = await ctx.sample(
        messages=prompt,
        system_prompt="Echo the user's message verbatim.",
        max_tokens=64,
    )
    return getattr(result, "text", str(result))


# --- URL-mode elicitation (MCP 2025-11-25) -----------------------------
# fastmcp 3.2.4's `ctx.elicit()` is form-mode only — URL mode is reached via
# the underlying `ServerSession` on `ctx.request_context.session`.


@mcp.tool()
async def connect_external_account(
    ctx: Context, completion_delay_s: float = 2.0
) -> dict:
    """Flow 4.2 — server-initiated URL elicitation, then completion notification."""
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
    """Flow 4.3 — first call raises `UrlElicitationRequiredError(-32042)` with
    one URL elicitation; second call succeeds.

    fastmcp 3.2.4's `_call_tool` (server.py:1247) wraps generic `Exception`
    as `ToolError`, masking `UrlElicitationRequiredError`. Workaround:
    subclass the raised exception from BOTH `UrlElicitationRequiredError` and
    `FastMCPError` so fastmcp's `except FastMCPError: raise` clause lets it
    through to mcp lowlevel, where `except UrlElicitationRequiredError: raise`
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
    # elicitation + sampling).
    mcp.run(transport="streamable-http", host="0.0.0.0", stateless_http=False)  # nosec B104 - AgentCore Runtime container requires bind to all interfaces
