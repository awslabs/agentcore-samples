"""One MCP Python SDK v2 server for both protocol versions, deployed twice: stateless and stateful.

A 2026-07-28 client needs nothing from the session: its elicitation is a multi round-trip request
(02-client-e2e), sealed with a key every microVM shares. An MCP 2025-11-25 client is different. Its elicitation is a request the server sends
back during the tool call, which needs a session, so it only works when the runtime serves earlier
clients in stateful mode (MCP_LEGACY_STATEFUL=true). Progress notifications travel on the request's
own response stream, so they work in either mode.

Expenses are kept in this microVM's memory; storage isn't the point here.
"""

from __future__ import annotations

import os
from typing import Annotated, Literal

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import AcceptedElicitation, Context, Elicit, ElicitationResult, Resolve
from pydantic import BaseModel, Field
from runtime_utils import add_ping, instance_id, request_state_security, serve
from starlette.datastructures import Headers

# The rounds of a 2026-07-28 elicitation can reach different microVMs, so requestState uses a shared key.
mcp = MCPServer(name="finance-compat", request_state_security=request_state_security())
add_ping(mcp)

SESSION_HEADER = "x-amzn-bedrock-agentcore-runtime-session-id"
EXPENSES: list[dict[str, object]] = [{"amount": 230.0, "category": "food"}, {"amount": 45.0, "category": "transport"}]


class WhoAmI(BaseModel):
    instance_id: str  # which microVM answered
    protocol_version: str | None
    legacy_mode: Literal["stateless", "stateful"]  # how this runtime serves earlier clients
    runtime_session_id: str | None
    mcp_session_id: str | None


class Total(BaseModel):
    expenses: int
    total: float


class Confirmation(BaseModel):
    confirm: bool = Field(description="Record this expense?")


class ExpenseResult(BaseModel):
    recorded: bool
    expenses: int
    instance_id: str


class Export(BaseModel):
    expenses: int
    batches: int
    instance_id: str


def legacy_mode() -> Literal["stateless", "stateful"]:
    return "stateful" if os.getenv("MCP_LEGACY_STATEFUL", "false").lower() == "true" else "stateless"


@mcp.tool()
def whoami(ctx: Context) -> WhoAmI:
    """Report which microVM answered, the protocol version it saw, and this runtime's legacy mode."""
    headers = Headers(headers=dict(ctx.headers or {}))
    return WhoAmI(
        instance_id=instance_id(),
        protocol_version=ctx.protocol_version,
        legacy_mode=legacy_mode(),
        runtime_session_id=headers.get(SESSION_HEADER),
        mcp_session_id=headers.get("mcp-session-id"),
    )


@mcp.tool()
def get_total() -> Total:
    """Total the expenses. An ordinary tool: it works for every client, in either mode."""
    return Total(expenses=len(EXPENSES), total=sum(float(e["amount"]) for e in EXPENSES))


def ask_confirmation(amount: float, category: str) -> Elicit[Confirmation]:
    return Elicit(f"Record ${amount:.2f} for {category}?", Confirmation)


@mcp.tool()
def add_expense(
    amount: Annotated[float, Field(gt=0)],
    category: Literal["food", "transport", "other"],
    confirmation: Annotated[ElicitationResult[Confirmation], Resolve(ask_confirmation)],
) -> ExpenseResult:
    """Record an expense after asking the user to confirm. Earlier clients need stateful mode for this."""
    if not (isinstance(confirmation, AcceptedElicitation) and confirmation.data.confirm):
        return ExpenseResult(recorded=False, expenses=len(EXPENSES), instance_id=instance_id())
    EXPENSES.append({"amount": amount, "category": category})
    return ExpenseResult(recorded=True, expenses=len(EXPENSES), instance_id=instance_id())


@mcp.tool()
async def export_expenses(ctx: Context, batches: Annotated[int, Field(ge=1, le=10)] = 3) -> Export:
    """Export the expenses, reporting progress after each batch. Works in either mode."""
    for batch in range(1, batches + 1):
        await anyio.sleep(0.3)
        # On the response stream of this request, so no session is needed for it.
        await ctx.report_progress(batch, batches, f"exported batch {batch} of {batches}")
    return Export(expenses=len(EXPENSES), batches=batches, instance_id=instance_id())


if __name__ == "__main__":
    serve(mcp)
