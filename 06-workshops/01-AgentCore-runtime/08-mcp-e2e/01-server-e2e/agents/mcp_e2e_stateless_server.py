import json
from datetime import datetime
from mcp.server.fastmcp import FastMCP
from mcp.types import PromptMessage, TextContent
from dynamo_utils import FinanceDB


mcp = FastMCP(
    name="Stateless-MCP-Server",
    host="0.0.0.0",  # nosec B104
    stateless_http=True,
)  # Stateless mode - no session persistence

db = FinanceDB()  # Dynamo DB helper


@mcp.tool()
def add_expense(user_alias: str, amount: float, description: str, category: str = "other") -> str:
    """Add a new expense transaction

    Args:
        user_alias: User identifier
        amount: Expense amount (positive number)
        description: Description of the expense
        category: Expense category (food, transport, entertainment, bills, other)
    """
    return db.add_transaction(user_alias, "expense", -abs(amount), description, category)


@mcp.tool()
def add_income(user_alias: str, amount: float, description: str, source: str = "salary") -> str:
    """Add a new income transaction

    Args:
        user_alias: User identifier
        amount: Income amount (positive number)
        description: Description of the income
        source: Income source (salary, freelance, investment, other)
    """
    return db.add_transaction(user_alias, "income", abs(amount), description, source)


@mcp.tool()
def set_budget(user_alias: str, category: str, monthly_limit: float) -> str:
    """Set monthly budget limit for a category

    Args:
        user_alias: User identifier
        category: Budget category (food, transport, entertainment, bills, other)
        monthly_limit: Monthly spending limit for this category
    """
    return db.set_budget(user_alias, category, monthly_limit)


@mcp.tool()
def get_balance(user_alias: str) -> str:
    """Get current account balance

    Args:
        user_alias: User identifier
    """
    balance_data = db.get_balance(user_alias)
    return f"Balance: ${balance_data['balance']:.2f}\nTotal Income: ${balance_data['income']:.2f}\nTotal Expenses: ${balance_data['expenses']:.2f}"


@mcp.prompt()
def budget_analysis(user_alias: str, time_period: str = "current_month") -> PromptMessage:
    """Analyze spending patterns and budget performance

    Args:
        user_alias: User identifier
        time_period: Time period to analyze (current_month, last_month, last_3_months)
    """
    # Get current spending data from DynamoDB
    transactions = db.get_transactions(user_alias)
    budgets = db.get_budgets(user_alias)

    current_spending = {}
    for transaction in transactions:
        if transaction["type"] == "expense":
            category = transaction["category"]
            current_spending[category] = current_spending.get(category, 0) + abs(float(transaction["amount"]))

    spending_summary = "\n".join([f"- {cat}: ${amount:.2f}" for cat, amount in current_spending.items()])
    budget_summary = "\n".join(
        [f"- {budget['category']}: ${float(budget['monthly_limit']):.2f}/month" for budget in budgets]
    )

    return PromptMessage(
        role="user",
        content=TextContent(
            type="text",
            text=f"""Please analyze my financial data for {time_period} and provide insights:

CURRENT SPENDING BY CATEGORY:
{spending_summary or "No expenses recorded"}

BUDGET LIMITS:
{budget_summary or "No budgets set"}

Please provide:
1. Budget vs actual spending comparison
2. Categories where I'm overspending
3. Recommendations for better budget management
4. Trends and patterns you notice
""",
        ),
    )


@mcp.prompt()
def savings_plan(user_alias: str, target_amount: float, target_months: int = 12) -> PromptMessage:
    """Generate a personalized savings plan

    Args:
        user_alias: User identifier
        target_amount: Target savings amount
        target_months: Number of months to reach the target (default 12)
    """
    # Calculate current financial situation from DynamoDB
    balance_data = db.get_balance(user_alias)
    total_income = balance_data["income"]
    total_expenses = balance_data["expenses"]
    current_balance = balance_data["balance"]

    monthly_target = target_amount / target_months

    return PromptMessage(
        role="user",
        content=TextContent(
            type="text",
            text=f"""Help me create a savings plan based on my financial situation:

SAVINGS GOAL:
- Target Amount: ${target_amount:.2f}
- Time Frame: {target_months} months
- Monthly Savings Needed: ${monthly_target:.2f}

CURRENT FINANCIAL SITUATION:
- Current Balance: ${current_balance:.2f}
- Total Income: ${total_income:.2f}
- Total Expenses: ${total_expenses:.2f}

Please provide:
1. Assessment of whether this savings goal is realistic
2. Specific strategies to reduce expenses
3. Ways to increase income if needed
4. Monthly action plan to reach the target
5. Emergency fund recommendations
""",
        ),
    )


@mcp.resource("finance://monthly/{user_alias}")
def get_monthly_summary(user_alias: str) -> str:
    """Get monthly financial summary as JSON"""
    now = datetime.now()
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Get transactions from DynamoDB
    all_transactions = db.get_transactions(user_alias)
    monthly_transactions = [t for t in all_transactions if datetime.fromisoformat(t["date"]) >= current_month_start]

    monthly_income = sum(float(t["amount"]) for t in monthly_transactions if t["type"] == "income")
    monthly_expenses = sum(abs(float(t["amount"])) for t in monthly_transactions if t["type"] == "expense")

    # Group expenses by category
    expenses_by_category = {}
    for t in monthly_transactions:
        if t["type"] == "expense":
            category = t["category"]
            expenses_by_category[category] = expenses_by_category.get(category, 0) + abs(float(t["amount"]))

    summary = {
        "user": user_alias,
        "month": now.strftime("%Y-%m"),
        "income": monthly_income,
        "expenses": monthly_expenses,
        "net": monthly_income - monthly_expenses,
        "expenses_by_category": expenses_by_category,
        "transaction_count": len(monthly_transactions),
        "generated_at": datetime.now().isoformat(),
    }

    return json.dumps(summary, indent=2)


@mcp.resource("finance://budgets/{user_alias}")
def get_budget_status(user_alias: str) -> str:
    """Get current budget status and performance as JSON"""
    # Get data from DynamoDB
    all_transactions = db.get_transactions(user_alias)
    all_budgets = db.get_budgets(user_alias)

    budget_status = {}

    # Calculate current month spending by category
    now = datetime.now()
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    monthly_spending = {}
    for transaction in all_transactions:
        if transaction["type"] == "expense" and datetime.fromisoformat(transaction["date"]) >= current_month_start:
            category = transaction["category"]
            monthly_spending[category] = monthly_spending.get(category, 0) + abs(float(transaction["amount"]))

    # Compare with budgets
    for budget in all_budgets:
        category = budget["category"]
        budget_limit = float(budget["monthly_limit"])
        spent = monthly_spending.get(category, 0)
        remaining = budget_limit - spent
        usage_percent = (spent / budget_limit) * 100 if budget_limit > 0 else 0

        budget_status[category] = {
            "budget_limit": budget_limit,
            "spent_this_month": spent,
            "remaining": remaining,
            "usage_percent": usage_percent,
            "status": "over_budget" if spent > budget_limit else "within_budget",
            "set_date": budget["set_date"],
        }

    # Add categories with spending but no budget
    for category, spent in monthly_spending.items():
        if category not in budget_status:
            budget_status[category] = {
                "budget_limit": None,
                "spent_this_month": spent,
                "remaining": None,
                "usage_percent": None,
                "status": "no_budget_set",
                "set_date": None,
            }

    return json.dumps(
        {
            "user": user_alias,
            "month": now.strftime("%Y-%m"),
            "budget_status": budget_status,
            "generated_at": datetime.now().isoformat(),
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
