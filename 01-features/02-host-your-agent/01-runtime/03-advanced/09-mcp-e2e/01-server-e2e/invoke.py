"""Call the finance tracker with MCP 2026-07-28 requests through boto3: tools, resources, and prompts.

There is no initialize handshake: every request carries its protocol version in _meta and headers.
Usage: python invoke.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "helpers"))
from deploy_utils import load_config
from dynamo_utils import FinanceDB
from mcp_client import EARLIER, McpClient, new_session_id, show

USER = "alice"

if __name__ == "__main__":
    config = load_config(HERE)
    FinanceDB(config["table"]).clear_user(USER)  # start every run from the same data
    mcp = McpClient(config["runtime_arn"], config["region"], session_id=new_session_id())

    discover = mcp.request("server/discover")
    show("server/discover", discover, ("supportedVersions", "capabilities"))

    tools = mcp.request("tools/list")
    print(
        f"── tools/list  HTTP {tools.status}  ttlMs={tools.result.get('ttlMs')} cacheScope={tools.result.get('cacheScope')}"
    )
    print(f"   {[t['name'] for t in tools.result.get('tools', [])]}")

    show(
        "tools/call add_income",
        mcp.call_tool("add_income", {"user_alias": USER, "amount": 3000, "description": "Salary"}),
    )
    show(
        "tools/call add_expense",
        mcp.call_tool("add_expense", {"user_alias": USER, "amount": 42.5, "description": "Lunch", "category": "food"}),
    )
    show(
        "tools/call set_budget",
        mcp.call_tool("set_budget", {"user_alias": USER, "category": "food", "monthly_limit": 400}),
    )
    show("tools/call get_balance", mcp.call_tool("get_balance", {"user_alias": USER}))

    templates = mcp.request("resources/templates/list")
    print(f"── resources/templates/list  {[t['uriTemplate'] for t in templates.result.get('resourceTemplates', [])]}")
    budgets = mcp.request("resources/read", {"uri": f"finance://budgets/{USER}"}, name=f"finance://budgets/{USER}")
    print(f"── resources/read finance://budgets/{USER}\n   {budgets.result['contents'][0]['text']}")

    prompts = mcp.request("prompts/list")
    print(f"── prompts/list  {[p['name'] for p in prompts.result.get('prompts', [])]}")
    prompt = mcp.request(
        "prompts/get", {"name": "budget_analysis", "arguments": {"user_alias": USER}}, name="budget_analysis"
    )
    print(f"── prompts/get budget_analysis\n   {prompt.result['messages'][0]['content']['text'][:200]}")

    # Errors keep their HTTP status in 2026-07-28: an unknown resource is HTTP 400 with -32602.
    show(
        "resources/read finance://unknown (expected error)",
        mcp.request("resources/read", {"uri": "finance://unknown/x"}, name="finance://unknown/x"),
    )

    # The same server still answers an MCP 2025-11-25 client.
    earlier = McpClient(config["runtime_arn"], config["region"], session_id=new_session_id())
    init = earlier.initialize()
    print(f"── {EARLIER} initialize  protocolVersion={init.result.get('protocolVersion')}")
    show(f"{EARLIER} tools/call get_balance", earlier.call_tool("get_balance", {"user_alias": USER}, version=EARLIER))
