"""Finance tracker MCP server: tools, resources, and prompts. Answers MCP 2026-07-28 and earlier clients."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from dynamo_utils import FinanceDB
from mcp.server import CacheHint, MCPServer
from pydantic import BaseModel, Field
from runtime_utils import add_ping, serve

Category = Literal["food", "transport", "entertainment", "bills", "other"]
User = Annotated[str, Field(description="User identifier, for example alice")]

mcp = MCPServer(
    name="finance-tracker",
    # 2026-07-28 list results carry ttlMs and cacheScope, so clients can cache them.
    cache_hints={
        "tools/list": CacheHint(ttl_ms=300_000, scope="public"),
        "prompts/list": CacheHint(ttl_ms=300_000, scope="public"),
        "resources/templates/list": CacheHint(ttl_ms=300_000, scope="public"),
    },
)
add_ping(mcp)  # AgentCore Runtime health-checks GET /ping; 2026-07-28 removed the MCP ping method
db = FinanceDB()


# Typed results give each tool an outputSchema, and clients get structuredContent.
class Transaction(BaseModel):
    type: Literal["income", "expense"]
    amount: float
    description: str
    category: str


class Budget(BaseModel):
    category: str
    monthly_limit: float


class Balance(BaseModel):
    balance: float
    income: float
    expenses: float


# ------------------------------------------------------------------- tools
@mcp.tool()
def add_expense(
    user_alias: User, amount: Annotated[float, Field(gt=0)], description: str, category: Category = "other"
) -> Transaction:
    """Record an expense."""
    return Transaction(**db.add_transaction(user_alias, "expense", -amount, description, category))


@mcp.tool()
def add_income(
    user_alias: User,
    amount: Annotated[float, Field(gt=0)],
    description: str,
    source: Literal["salary", "freelance", "investment", "other"] = "salary",
) -> Transaction:
    """Record income."""
    return Transaction(**db.add_transaction(user_alias, "income", amount, description, source))


@mcp.tool()
def set_budget(user_alias: User, category: Category, monthly_limit: Annotated[float, Field(gt=0)]) -> Budget:
    """Set the monthly budget for a category."""
    return Budget(**db.set_budget(user_alias, category, monthly_limit))


@mcp.tool()
def get_balance(user_alias: User) -> Balance:
    """Return the balance, total income, and total expenses."""
    return Balance(**db.get_balance(user_alias))


# ------------------------------------------------------------------- resources
def month_start() -> str:
    return datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


@mcp.resource("finance://monthly/{user_alias}", mime_type="application/json")
def monthly_summary(user_alias: str) -> str:
    """This month's income, expenses, and spending by category."""
    since = month_start()
    month = [t for t in db.get_transactions(user_alias) if t["date"] >= since]
    income = sum(float(t["amount"]) for t in month if t["type"] == "income")
    spending = db.spending_by_category(user_alias, since)
    return json.dumps(
        {
            "user": user_alias,
            "month": since[:7],
            "income": income,
            "expenses": sum(spending.values()),
            "by_category": spending,
        }
    )


@mcp.resource("finance://budgets/{user_alias}", mime_type="application/json")
def budget_status(user_alias: str) -> str:
    """Each budget with this month's spending against it."""
    spending = db.spending_by_category(user_alias, month_start())
    budgets = {b["category"]: float(b["monthly_limit"]) for b in db.get_budgets(user_alias)}
    return json.dumps(
        {
            "user": user_alias,
            "budgets": {
                category: {
                    "limit": limit,
                    "spent": spending.get(category, 0.0),
                    "status": "over_budget" if spending.get(category, 0.0) > limit else "within_budget",
                }
                for category, limit in budgets.items()
            },
        }
    )


# ------------------------------------------------------------------- prompts
@mcp.prompt()
def budget_analysis(user_alias: str) -> str:
    """Ask the model to compare spending with budgets."""
    spending = db.spending_by_category(user_alias, month_start())
    budgets = {b["category"]: float(b["monthly_limit"]) for b in db.get_budgets(user_alias)}
    return (
        f"Compare this month's spending with my budgets and suggest where to cut.\n"
        f"Spending: {json.dumps(spending)}\nBudgets: {json.dumps(budgets)}"
    )


@mcp.prompt()
def savings_plan(
    user_alias: str, target_amount: Annotated[float, Field(gt=0)], target_months: Annotated[int, Field(ge=1)] = 12
) -> str:
    """Ask the model for a monthly savings plan."""
    balance = db.get_balance(user_alias)
    return (
        f"Make a plan to save ${target_amount:.2f} in {target_months} months "
        f"(${target_amount / target_months:.2f} a month). My finances: {json.dumps(balance)}"
    )


if __name__ == "__main__":
    serve(mcp)
