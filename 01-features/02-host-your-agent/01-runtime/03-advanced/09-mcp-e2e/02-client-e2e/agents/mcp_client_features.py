import os
from mcp.server.fastmcp import FastMCP, Context
from dynamo_utils import FinanceDB

mcp = FastMCP(name="ElicitationMCP", host="0.0.0.0", stateless_http=True)  # nosec B104

_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
db = FinanceDB(region_name=_region)


@mcp.tool()
async def add_expense_interactive(user_alias: str, ctx: Context) -> str:
    """Interactively add a new expense using elicitation
    Args:
        user_alias: User identifier
    """
    # Pydantic models defined inside the function so they are available
    # after pip install completes (not needed at module load time)
    from pydantic import BaseModel

    class AmountInput(BaseModel):
        amount: float

    class DescriptionInput(BaseModel):
        description: str

    class CategoryInput(BaseModel):
        category: str  # one of: food, transport, bills, entertainment, other

    class ConfirmInput(BaseModel):
        confirm: str  # Yes or No

    # Step 1: Ask for the amount
    result = await ctx.elicit("How much did you spend?", AmountInput)
    if not (hasattr(result, "action") and result.action == "accept"):
        return "Expense entry cancelled."
    amount = result.data.amount

    result = await ctx.elicit("What was it for?", DescriptionInput)
    if not (hasattr(result, "action") and result.action == "accept"):
        return "Expense entry cancelled."
    description = result.data.description

    result = await ctx.elicit(
        "Select a category (food, transport, bills, entertainment, other):",
        CategoryInput,
    )
    if not (hasattr(result, "action") and result.action == "accept"):
        return "Expense entry cancelled."
    category = result.data.category

    confirm_msg = f"Confirm: add ${amount:.2f} for {description} ({category})? Reply Yes or No"
    result = await ctx.elicit(confirm_msg, ConfirmInput)
    if not (hasattr(result, "action") and result.action == "accept") or result.data.confirm != "Yes":
        return "Expense entry cancelled."

    return db.add_transaction(user_alias, "expense", -abs(amount), description, category)


@mcp.tool()
def add_expense(user_alias: str, amount: float, description: str, category: str = "other") -> str:
    """Add a new expense transaction.
    Args:
        user_alias: User identifier
        amount: Expense amount (positive number)
        description: Description of the expense
        category: Expense category (food, transport, bills, entertainment, other)
    """
    return db.add_transaction(user_alias, "expense", -abs(amount), description, category)


@mcp.tool()
async def analyze_spending(user_alias: str, ctx: Context) -> str:
    """Fetch expenses from DynamoDB and use the client LLM to analyse spending.
    Args:
        user_alias: User identifier
    """
    transactions = db.get_transactions(user_alias)
    if not transactions:
        return f"No transactions found for {user_alias}."

    lines = "\n".join(f"- {t['description']} (${abs(float(t['amount'])):.2f}, {t['category']})" for t in transactions)
    prompt = (
        f"Here are the recent expenses for a user:\n{lines}\n\n"
        f"Please analyse the spending patterns and give 3 concise, "
        f"actionable recommendations to improve their finances. "
        f"Keep the response under 120 words."
    )

    ai_analysis = "Analysis unavailable."
    try:
        response = await ctx.sample(messages=prompt, max_tokens=300)
        if hasattr(response, "text") and response.text:
            ai_analysis = response.text
    except Exception:
        pass

    return f"Spending Analysis for {user_alias}:\n\n{ai_analysis}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
