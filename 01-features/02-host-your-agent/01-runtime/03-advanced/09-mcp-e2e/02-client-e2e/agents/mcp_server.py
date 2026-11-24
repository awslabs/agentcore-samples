"""Finance assistant: elicitation and sampling as MCP 2026-07-28 multi round-trip requests (MRTR).

A 2026-07-28 client gets `resultType: input_required` with the questions, answers them, and retries the
same call with `inputResponses` and the sealed `requestState`. No session or open stream is needed
between rounds, so each round can reach a different microVM. Earlier clients get the questions as
server-to-client requests mid-call, which needs stateful mode (05-compatibility-e2e).
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from dynamo_utils import FinanceDB
from mcp import types
from mcp.server import MCPServer
from mcp.server.mcpserver import AcceptedElicitation, Elicit, ElicitationResult, Resolve, Sample
from pydantic import BaseModel, Field
from runtime_utils import add_ping, instance_id, request_state_security, serve

# requestState is sealed with a key every microVM shares (AWS Secrets Manager, read on first use).
mcp = MCPServer(name="finance-assistant", request_state_security=request_state_security())
add_ping(mcp)
db = FinanceDB()

Category = Literal["food", "transport", "entertainment", "bills", "other"]


class ExpenseDetails(BaseModel):
    amount: float = Field(description="How much did you spend?")
    description: str = Field(description="What was it for?")
    category: Category = Field(default="other", description="Category")


class Confirmation(BaseModel):
    confirm: bool = Field(description="Record this expense?")


class ExpenseResult(BaseModel):
    recorded: bool
    amount: float | None = None
    description: str | None = None
    category: str | None = None
    instance_id: str  # which microVM ran the last round


class SpendingAnalysis(BaseModel):
    analysis: str  # written by the client's model: treat as untrusted input
    model: str
    instance_id: str


# ------------------------------------------------------------------- elicitation
def ask_details(user_alias: str) -> Elicit[ExpenseDetails]:
    """Round 1: ask for the expense."""
    return Elicit(f"New expense for {user_alias}", ExpenseDetails)


def ask_confirmation(details: Annotated[ExpenseDetails, Resolve(ask_details)]) -> Elicit[Confirmation]:
    """Round 2: depends on the round 1 answers, so the SDK asks it only after they arrive."""
    return Elicit(f"Record ${details.amount:.2f} for {details.description} ({details.category})?", Confirmation)


@mcp.tool()
def add_expense_interactive(
    user_alias: str,
    details: Annotated[ExpenseDetails, Resolve(ask_details)],
    confirmation: Annotated[ElicitationResult[Confirmation], Resolve(ask_confirmation)],
) -> ExpenseResult:
    """Record an expense, asking the user for the details and a confirmation."""
    if not (isinstance(confirmation, AcceptedElicitation) and confirmation.data.confirm):
        return ExpenseResult(recorded=False, instance_id=instance_id())
    db.add_transaction(user_alias, "expense", -abs(details.amount), details.description, details.category)
    return ExpenseResult(recorded=True, **details.model_dump(), instance_id=instance_id())


@mcp.tool()
def add_expense(
    user_alias: str, amount: Annotated[float, Field(gt=0)], description: str, category: Category = "other"
) -> dict:
    """Record an expense without asking anything."""
    return db.add_transaction(user_alias, "expense", -amount, description, category)


@mcp.tool()
def set_budget(user_alias: str, category: Category, monthly_limit: Annotated[float, Field(gt=0)]) -> dict:
    """Set the monthly budget for a category."""
    return db.set_budget(user_alias, category, monthly_limit)


# ------------------------------------------------------------------- sampling (deprecated)
def spending_prompt(user_alias: str) -> Sample:
    """Ask the client's LLM for an analysis. The prompt must be the same on every round."""
    spending = db.spending_by_category(user_alias)
    budgets = {b["category"]: float(b["monthly_limit"]) for b in db.get_budgets(user_alias)}
    text = (
        f"Spending by category: {json.dumps(spending)}. Monthly budgets: {json.dumps(budgets)}. "
        "In three short bullet points, say where this person is over budget and what to change."
    )
    return Sample(
        [types.SamplingMessage(role="user", content=types.TextContent(type="text", text=text))],
        max_tokens=300,
        system_prompt="You are a concise personal finance assistant.",
    )


@mcp.tool()
def analyze_spending(
    user_alias: str,
    analysis: Annotated[types.CreateMessageResult, Resolve(spending_prompt)],
) -> SpendingAnalysis:
    """Analyze spending with the client's LLM (sampling, deprecated in MCP 2026-07-28)."""
    # MCP 2026-07-28 deprecates sampling (SEP-2577): new tools should call an LLM API directly.
    # The text comes from the client's model, so treat it as untrusted input.
    blocks = analysis.content if isinstance(analysis.content, list) else [analysis.content]
    text = "".join(b.text for b in blocks if isinstance(b, types.TextContent))
    return SpendingAnalysis(analysis=text, model=analysis.model, instance_id=instance_id())


if __name__ == "__main__":
    serve(mcp)
