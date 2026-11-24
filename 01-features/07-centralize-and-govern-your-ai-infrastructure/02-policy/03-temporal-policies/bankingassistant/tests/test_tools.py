"""In-process FastMCP tests, not gateway/temporal-enforcement tests.

Run with the app's locked dependencies installed. No HTTP listener or AWS calls.
"""

import copy
import importlib.util
import socket
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastmcp import Client
except ImportError:
    Client = None


@unittest.skipIf(Client is None, "install app/banking_assistant_tools locked dependencies")
class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.guard = patch.object(socket.socket, "connect", side_effect=AssertionError("No network"))
        self.guard.start()
        path = Path(__file__).resolve().parents[1] / "app/banking_assistant_tools/main.py"
        spec = importlib.util.spec_from_file_location("banking_tools_test", path)
        self.tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tools)
        self.client = Client(self.tools.mcp)
        await self.client.__aenter__()

    async def asyncTearDown(self):
        await self.client.__aexit__(None, None, None)
        self.guard.stop()

    async def test_fourteen_tools_and_integer_policy_inputs(self):
        tools = {tool.name: tool for tool in await self.client.list_tools()}
        self.assertEqual(len(tools), 14)
        self.assertEqual(tools["transfer_funds"].input_schema["properties"]["amount"]["type"], "integer")
        self.assertEqual(tools["execute_trade"].input_schema["properties"]["cost"]["type"], "integer")
        self.assertTrue(all(tool.output_schema for tool in tools.values()))

    async def test_balance_transfer_freeze_and_approval_records(self):
        # Direct calls intentionally have no temporal gate: the Gateway owns it.
        result = await self.client.call_tool(
            "transfer_funds", {"from_account": "ACC-1001", "to_account": "ACC-2002", "amount": 100}
        )
        self.assertEqual(result.structured_content["status"], "completed")
        balance = await self.client.call_tool("get_account_balance", {"account_id": "ACC-2002"})
        self.assertEqual(balance.structured_content["balance"], 12600)
        await self.client.call_tool("freeze_account", {"account_id": "ACC-2002"})
        denied = await self.client.call_tool(
            "transfer_funds", {"from_account": "ACC-1001", "to_account": "ACC-2002", "amount": 100}
        )
        self.assertIn("frozen", denied.structured_content["error"])
        approval = await self.client.call_tool(
            "approve_transfer", {"transfer_id": "REQ-001", "approved_by": "simulated-manager"}
        )
        self.assertEqual(approval.structured_content["status"], "approved")

    async def test_nonpositive_trade_shares_cannot_mutate_balances(self):
        before = copy.deepcopy(self.tools.PORTFOLIOS)
        for action in ("buy", "sell"):
            for shares in (0, -1, -100):
                with self.subTest(action=action, shares=shares):
                    result = await self.client.call_tool(
                        "execute_trade",
                        {
                            "portfolio_id": "PORT-8821",
                            "symbol": "AAPL",
                            "action": action,
                            "shares": shares,
                            "cost": -500,
                        },
                    )
                    self.assertIn("positive", result.structured_content["error"])
                    self.assertEqual(self.tools.PORTFOLIOS, before)
                    self.assertEqual(self.tools.TRADE_LOG, [])

    async def test_portfolio_workflow_keeps_signed_cost(self):
        await self.client.call_tool("get_client_profile", {"client_id": "CLIENT-001"})
        await self.client.call_tool("load_portfolio", {"portfolio_id": "PORT-8821"})
        await self.client.call_tool("get_market_price", {"symbol": "AAPL"})
        buy = await self.client.call_tool(
            "execute_trade", {"portfolio_id": "PORT-8821", "symbol": "AAPL", "action": "buy", "shares": 1, "cost": 179}
        )
        self.assertEqual(buy.structured_content["status"], "executed")
        sell = await self.client.call_tool(
            "execute_trade",
            {"portfolio_id": "PORT-8821", "symbol": "AAPL", "action": "sell", "shares": 1, "cost": -500},
        )
        self.assertEqual(sell.structured_content["status"], "executed")
        advisor = await self.client.call_tool("interact_advisor", {"advisor_id": "ADV-001"})
        self.assertEqual(advisor.structured_content["advisorId"], "ADV-001")


if __name__ == "__main__":
    unittest.main()
