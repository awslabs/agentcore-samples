import os
import asyncio
from fastmcp import FastMCP, Context
from dynamo_utils import FinanceDB

mcp = FastMCP(name="Progress-MCP-Server")

_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
db = FinanceDB(region_name=_region)


@mcp.tool()
async def generate_report(user_alias: str, ctx: Context) -> str:
    """Generate a monthly financial report in 5 steps, streaming a progress
    notification to the client at the start of each stage.

    Args:
        user_alias: User identifier
    """
    total = 5

    # Step 1: Fetch transactions
    await ctx.report_progress(progress=1, total=total)
    await asyncio.sleep(0.5)
    transactions = db.get_transactions(user_alias)
    if not transactions:
        return f"No transactions found for {user_alias}."

    # Step 2: Group by category
    await ctx.report_progress(progress=2, total=total)
    await asyncio.sleep(0.5)
    by_category = {}
    for t in transactions:
        cat = t["category"]
        by_category[cat] = by_category.get(cat, 0) + abs(float(t["amount"]))

    # Step 3: Fetch budgets
    await ctx.report_progress(progress=3, total=total)
    await asyncio.sleep(0.5)
    budgets = {b["category"]: float(b["monthly_limit"]) for b in db.get_budgets(user_alias)}

    # Step 4: Compare spending vs budgets
    await ctx.report_progress(progress=4, total=total)
    await asyncio.sleep(0.5)
    lines = []
    for cat, spent in sorted(by_category.items(), key=lambda x: -x[1]):
        limit = budgets.get(cat)
        if limit:
            pct = (spent / limit) * 100
            status = "OVER" if spent > limit else "OK"
            lines.append(f"  {cat:<15} ${spent:>8.2f} / ${limit:.2f}  [{pct:.0f}%] {status}")
        else:
            lines.append(f"  {cat:<15} ${spent:>8.2f}  (no budget set)")

    # Step 5: Format report
    await ctx.report_progress(progress=5, total=total)
    await asyncio.sleep(0.2)
    total_spent = sum(by_category.values())
    report = (
        f"Monthly Report for {user_alias}\n"
        f"{'=' * 50}\n"
        f"  {'Category':<15} {'Spent':>10}   {'Budget':>8}  Status\n"
        f"{'-' * 50}\n" + "\n".join(lines) + f"\n{'-' * 50}\n"
        f"  {'TOTAL':<15} ${total_spent:>8.2f}\n"
    )
    return report


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",  # nosec B104
        port=8000,
        stateless_http=False,
    )
