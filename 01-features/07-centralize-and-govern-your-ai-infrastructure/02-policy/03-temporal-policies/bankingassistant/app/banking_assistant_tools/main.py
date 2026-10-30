"""
Banking Assistant MCP server.

A single MCP server exposing all banking and portfolio tools for the
temporal-policy workshop. Deployed to AgentCore Runtime; the gateway discovers
tools via tools/list and invokes them via tools/call.

Each tool declares an explicit output_schema so the gateway can map return
fields to output.* for temporal policy evaluation (e.g. output.account_id).
"""

import random
import string
from datetime import datetime

from fastmcp import FastMCP

mcp = FastMCP(name="banking_assistant_tools")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_id(prefix: str) -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}-{suffix}"


def _timestamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Simulated banking data
# ---------------------------------------------------------------------------

ACCOUNTS: dict[str, dict] = {
    "ACC-1001": {"owner": "Alice Johnson", "balance": 85_000.00, "frozen": False},
    "ACC-2002": {"owner": "Bob Smith", "balance": 12_500.00, "frozen": False},
    "ACC-3003": {"owner": "Carol White", "balance": 250_000.00, "frozen": False},
    "ACC-4004": {"owner": "David Lee", "balance": 3_400.00, "frozen": False},
    "ACC-5005": {"owner": "Eve Martinez", "balance": 99_000.00, "frozen": True},
}

TRANSACTIONS: list[dict] = [
    {
        "id": "TXN-0001",
        "from": "ACC-1001",
        "to": "ACC-2002",
        "amount": 500.00,
        "ts": "2026-08-04T10:00:00Z",
        "status": "completed",
    },
    {
        "id": "TXN-0002",
        "from": "ACC-3003",
        "to": "ACC-1001",
        "amount": 1200.00,
        "ts": "2026-08-04T11:30:00Z",
        "status": "completed",
    },
    {
        "id": "TXN-0003",
        "from": "ACC-2002",
        "to": "ACC-4004",
        "amount": 300.00,
        "ts": "2026-08-04T14:00:00Z",
        "status": "completed",
    },
]

BANKING_APPROVALS: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Simulated portfolio data
# ---------------------------------------------------------------------------

CLIENTS: dict[str, dict] = {
    "CLIENT-001": {
        "name": "Alice Johnson",
        "risk_tolerance": "moderate",
        "restrictions": ["no_tobacco", "no_weapons"],
        "portfolio_ids": ["PORT-8821", "PORT-8822"],
    },
    "CLIENT-002": {
        "name": "Bob Smith",
        "risk_tolerance": "aggressive",
        "restrictions": [],
        "portfolio_ids": ["PORT-3347"],
    },
    "CLIENT-003": {
        "name": "Carol White",
        "risk_tolerance": "conservative",
        "restrictions": ["esg_only"],
        "portfolio_ids": ["PORT-5501", "PORT-5502"],
    },
}

PORTFOLIOS: dict[str, dict] = {
    "PORT-8821": {
        "client_id": "CLIENT-001",
        "holdings": [
            {"symbol": "AAPL", "shares": 100, "avg_cost": 155.00},
            {"symbol": "MSFT", "shares": 50, "avg_cost": 310.00},
            {"symbol": "AMZN", "shares": 20, "avg_cost": 3200.00},
        ],
        "cash": 12_500.00,
    },
    "PORT-8822": {
        "client_id": "CLIENT-001",
        "holdings": [{"symbol": "GOOGL", "shares": 10, "avg_cost": 2800.00}],
        "cash": 5_000.00,
    },
    "PORT-3347": {
        "client_id": "CLIENT-002",
        "holdings": [
            {"symbol": "TSLA", "shares": 200, "avg_cost": 220.00},
            {"symbol": "NVDA", "shares": 30, "avg_cost": 450.00},
        ],
        "cash": 8_000.00,
    },
    "PORT-5501": {
        "client_id": "CLIENT-003",
        "holdings": [{"symbol": "VTI", "shares": 300, "avg_cost": 220.00}],
        "cash": 25_000.00,
    },
    "PORT-5502": {"client_id": "CLIENT-003", "holdings": [], "cash": 50_000.00},
}

MARKET_PRICES: dict[str, float] = {
    "AAPL": 178.50,
    "MSFT": 335.00,
    "AMZN": 3_450.00,
    "GOOGL": 2_950.00,
    "TSLA": 195.00,
    "NVDA": 520.00,
    "VTI": 235.00,
    "SPY": 445.00,
    "QQQ": 380.00,
}

TRADE_LOG: list[dict] = []
TRADE_APPROVALS: dict[str, dict] = {}

# ===========================================================================
# BANKING TOOLS
# ===========================================================================


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "account_id": {
                "type": "string",
                "description": "The verified account identifier",
            },
            "owner": {"type": "string"},
            "balance": {"type": "number"},
            "frozen": {"type": "boolean"},
            "currency": {"type": "string"},
            "asOf": {"type": "string"},
        },
    }
)
def get_account_balance(account_id: str) -> dict:
    """Look up an account and return its current balance, owner, and frozen status."""
    if account_id not in ACCOUNTS:
        return {"error": f"Account {account_id} not found"}
    acct = ACCOUNTS[account_id]
    return {
        "account_id": account_id,
        "owner": acct["owner"],
        "balance": acct["balance"],
        "frozen": acct["frozen"],
        "currency": "USD",
        "asOf": _timestamp(),
    }


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "transactionId": {"type": "string"},
            "status": {"type": "string"},
            "fromAccount": {"type": "string"},
            "toAccount": {"type": "string"},
            "amount": {"type": "integer"},
            "memo": {"type": "string"},
            "timestamp": {"type": "string"},
        },
    }
)
def transfer_funds(
    from_account: str, to_account: str, amount: int, memo: str = ""
) -> dict:
    """Transfer funds between two accounts."""
    if from_account not in ACCOUNTS:
        return {"error": f"Source account {from_account} not found"}
    if to_account not in ACCOUNTS:
        return {"error": f"Destination account {to_account} not found"}
    src = ACCOUNTS[from_account]
    dst = ACCOUNTS[to_account]
    if src["frozen"]:
        return {"error": f"Account {from_account} is frozen"}
    if dst["frozen"]:
        return {"error": f"Account {to_account} is frozen"}
    if amount <= 0:
        return {"error": "Transfer amount must be positive"}
    if src["balance"] < amount:
        return {"error": f"Insufficient funds: balance is ${src['balance']:.2f}"}
    src["balance"] -= amount
    dst["balance"] += amount
    txn_id = _generate_id("TXN")
    txn = {
        "id": txn_id,
        "from": from_account,
        "to": to_account,
        "amount": amount,
        "memo": memo,
        "ts": _timestamp(),
        "status": "completed",
    }
    TRANSACTIONS.append(txn)
    return {
        "transactionId": txn_id,
        "status": "completed",
        "fromAccount": from_account,
        "toAccount": to_account,
        "amount": amount,
        "memo": memo,
        "timestamp": txn["ts"],
    }


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "account_id": {"type": "string"},
            "transactions": {"type": "array"},
            "count": {"type": "integer"},
        },
    }
)
def get_transaction_history(account_id: str, limit: int = 10) -> dict:
    """Return recent transactions for an account."""
    if account_id not in ACCOUNTS:
        return {"error": f"Account {account_id} not found"}
    acct_txns = [
        t for t in TRANSACTIONS if t["from"] == account_id or t["to"] == account_id
    ]
    recent = sorted(acct_txns, key=lambda t: t["ts"], reverse=True)[:limit]
    return {"account_id": account_id, "transactions": recent, "count": len(recent)}


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "account_id": {"type": "string"},
            "frozen": {"type": "boolean"},
            "reason": {"type": "string"},
            "timestamp": {"type": "string"},
        },
    }
)
def freeze_account(account_id: str, reason: str = "") -> dict:
    """Freeze an account, preventing all transfers to or from it."""
    if account_id not in ACCOUNTS:
        return {"error": f"Account {account_id} not found"}
    ACCOUNTS[account_id]["frozen"] = True
    return {
        "account_id": account_id,
        "frozen": True,
        "reason": reason,
        "timestamp": _timestamp(),
    }


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "account_id": {"type": "string"},
            "frozen": {"type": "boolean"},
            "timestamp": {"type": "string"},
        },
    }
)
def unfreeze_account(account_id: str) -> dict:
    """Remove the freeze flag from an account."""
    if account_id not in ACCOUNTS:
        return {"error": f"Account {account_id} not found"}
    ACCOUNTS[account_id]["frozen"] = False
    return {"account_id": account_id, "frozen": False, "timestamp": _timestamp()}


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "transferId": {"type": "string"},
            "status": {"type": "string"},
            "approvedBy": {"type": "string"},
            "notes": {"type": "string"},
            "timestamp": {"type": "string"},
        },
    }
)
def approve_transfer(transfer_id: str, approved_by: str, notes: str = "") -> dict:
    """Record a human approval for a pending high-value transfer."""
    BANKING_APPROVALS[transfer_id] = {
        "status": "approved",
        "approvedBy": approved_by,
        "notes": notes,
        "timestamp": _timestamp(),
    }
    return {
        "transferId": transfer_id,
        "status": "approved",
        "approvedBy": approved_by,
        "notes": notes,
        "timestamp": BANKING_APPROVALS[transfer_id]["timestamp"],
    }


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "transferId": {"type": "string"},
            "status": {"type": "string"},
            "rejectedBy": {"type": "string"},
            "reason": {"type": "string"},
            "timestamp": {"type": "string"},
        },
    }
)
def reject_transfer(transfer_id: str, rejected_by: str, reason: str = "") -> dict:
    """Record a human rejection for a pending transfer."""
    BANKING_APPROVALS[transfer_id] = {
        "status": "rejected",
        "rejectedBy": rejected_by,
        "reason": reason,
        "timestamp": _timestamp(),
    }
    return {
        "transferId": transfer_id,
        "status": "rejected",
        "rejectedBy": rejected_by,
        "reason": reason,
        "timestamp": BANKING_APPROVALS[transfer_id]["timestamp"],
    }


# ===========================================================================
# PORTFOLIO TOOLS
# ===========================================================================


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "clientId": {"type": "string"},
            "name": {"type": "string"},
            "riskTolerance": {"type": "string"},
            "restrictions": {"type": "array", "items": {"type": "string"}},
            "portfolio_ids": {"type": "array", "items": {"type": "string"}},
            "asOf": {"type": "string"},
        },
    }
)
def get_client_profile(client_id: str) -> dict:
    """Retrieve a client's risk tolerance, investment policy, account restrictions, and associated portfolio IDs."""
    if client_id not in CLIENTS:
        return {"error": f"Client {client_id} not found"}
    client = CLIENTS[client_id]
    return {
        "clientId": client_id,
        "name": client["name"],
        "riskTolerance": client["risk_tolerance"],
        "restrictions": client["restrictions"],
        "portfolio_ids": client["portfolio_ids"],
        "asOf": _timestamp(),
    }


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "portfolioId": {"type": "string"},
            "clientId": {"type": "string"},
            "holdings": {"type": "array"},
            "cash": {"type": "number"},
            "totalValue": {"type": "number"},
            "asOf": {"type": "string"},
        },
    }
)
def load_portfolio(portfolio_id: str) -> dict:
    """Retrieve a portfolio's current holdings, positions, and cash balance."""
    if portfolio_id not in PORTFOLIOS:
        return {"error": f"Portfolio {portfolio_id} not found"}
    port = PORTFOLIOS[portfolio_id]
    holdings_with_value = []
    for h in port["holdings"]:
        price = MARKET_PRICES.get(h["symbol"], h["avg_cost"])
        market_value = price * h["shares"]
        unrealized_pnl = (price - h["avg_cost"]) * h["shares"]
        holdings_with_value.append(
            {
                "symbol": h["symbol"],
                "shares": h["shares"],
                "avgCost": h["avg_cost"],
                "currentPrice": price,
                "marketValue": round(market_value, 2),
                "unrealizedPnL": round(unrealized_pnl, 2),
            }
        )
    total_value = sum(h["marketValue"] for h in holdings_with_value) + port["cash"]
    return {
        "portfolioId": portfolio_id,
        "clientId": port["client_id"],
        "holdings": holdings_with_value,
        "cash": port["cash"],
        "totalValue": round(total_value, 2),
        "asOf": _timestamp(),
    }


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "price": {"type": "number"},
            "currency": {"type": "string"},
            "asOf": {"type": "string"},
        },
    }
)
def get_market_price(symbol: str) -> dict:
    """Fetch the current market price for a security."""
    symbol = symbol.upper()
    if symbol not in MARKET_PRICES:
        return {"error": f"Symbol {symbol} not found"}
    return {
        "symbol": symbol,
        "price": MARKET_PRICES[symbol],
        "currency": "USD",
        "asOf": _timestamp(),
    }


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "tradeId": {"type": "string"},
            "portfolioId": {"type": "string"},
            "symbol": {"type": "string"},
            "action": {"type": "string"},
            "shares": {"type": "integer"},
            "price": {"type": "number"},
            "cost": {"type": "integer"},
            "timestamp": {"type": "string"},
            "status": {"type": "string"},
        },
    }
)
def execute_trade(
    portfolio_id: str, symbol: str, action: str, shares: int, cost: int
) -> dict:
    """Execute a buy or sell order against a portfolio."""
    if portfolio_id not in PORTFOLIOS:
        return {"error": f"Portfolio {portfolio_id} not found"}
    symbol = symbol.upper()
    action = action.upper()
    if action not in ("BUY", "SELL"):
        return {"error": "action must be BUY or SELL"}
    port = PORTFOLIOS[portfolio_id]
    price = MARKET_PRICES.get(symbol)
    if price is None:
        return {"error": f"Symbol {symbol} not found"}
    total = shares * price
    if action == "BUY":
        if port["cash"] < total:
            return {
                "error": f"Insufficient cash: have ${port['cash']:.2f}, need ${total:.2f}"
            }
        port["cash"] -= total
        holding = next((h for h in port["holdings"] if h["symbol"] == symbol), None)
        if holding:
            new_shares = holding["shares"] + shares
            holding["avg_cost"] = (
                holding["avg_cost"] * holding["shares"] + total
            ) / new_shares
            holding["shares"] = new_shares
        else:
            port["holdings"].append(
                {"symbol": symbol, "shares": shares, "avg_cost": price}
            )
    else:
        holding = next((h for h in port["holdings"] if h["symbol"] == symbol), None)
        if not holding or holding["shares"] < shares:
            available = holding["shares"] if holding else 0
            return {"error": f"Insufficient shares: have {available}, need {shares}"}
        holding["shares"] -= shares
        if holding["shares"] == 0:
            port["holdings"].remove(holding)
        port["cash"] += total
    trade_id = _generate_id("TRD")
    trade = {
        "tradeId": trade_id,
        "portfolioId": portfolio_id,
        "symbol": symbol,
        "action": action,
        "shares": shares,
        "price": price,
        "cost": round(total, 2),
        "timestamp": _timestamp(),
        "status": "executed",
    }
    TRADE_LOG.append(trade)
    return trade


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "rebalanceId": {"type": "string"},
            "portfolioId": {"type": "string"},
            "totalPortfolioValue": {"type": "number"},
            "proposedActions": {"type": "array"},
            "timestamp": {"type": "string"},
            "status": {"type": "string"},
        },
    }
)
def rebalance_portfolio(portfolio_id: str, target_allocations: list) -> dict:
    """Adjust portfolio allocations to match target percentages."""
    if portfolio_id not in PORTFOLIOS:
        return {"error": f"Portfolio {portfolio_id} not found"}
    port = PORTFOLIOS[portfolio_id]
    total_value = (
        sum(
            MARKET_PRICES.get(h["symbol"], h["avg_cost"]) * h["shares"]
            for h in port["holdings"]
        )
        + port["cash"]
    )
    rebalance_id = _generate_id("RBL")
    actions = []
    for alloc in target_allocations:
        sym = alloc["symbol"].upper()
        target_pct = alloc["target_pct"]
        target_value = total_value * (target_pct / 100.0)
        price = MARKET_PRICES.get(sym, 0)
        if price == 0:
            continue
        target_shares = int(target_value / price)
        current = next((h for h in port["holdings"] if h["symbol"] == sym), None)
        current_shares = current["shares"] if current else 0
        delta = target_shares - current_shares
        if delta != 0:
            actions.append(
                {
                    "symbol": sym,
                    "action": "BUY" if delta > 0 else "SELL",
                    "shares": abs(delta),
                    "estimatedCost": round(abs(delta) * price, 2),
                }
            )
    return {
        "rebalanceId": rebalance_id,
        "portfolioId": portfolio_id,
        "totalPortfolioValue": round(total_value, 2),
        "proposedActions": actions,
        "timestamp": _timestamp(),
        "status": "proposed",
    }


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "tradeRequestId": {"type": "string"},
            "status": {"type": "string"},
            "approvedBy": {"type": "string"},
            "notes": {"type": "string"},
            "timestamp": {"type": "string"},
        },
    }
)
def approve_trade(
    trade_request_id: str, approved_by: str, status: str = "approved", notes: str = ""
) -> dict:
    """Record advisor approval for a large trade."""
    TRADE_APPROVALS[trade_request_id] = {
        "status": status,
        "approvedBy": approved_by,
        "notes": notes,
        "timestamp": _timestamp(),
    }
    return {
        "tradeRequestId": trade_request_id,
        "status": status,
        "approvedBy": approved_by,
        "notes": notes,
        "timestamp": TRADE_APPROVALS[trade_request_id]["timestamp"],
    }


@mcp.tool(
    output_schema={
        "type": "object",
        "properties": {
            "advisorId": {"type": "string"},
            "action": {"type": "string"},
            "notes": {"type": "string"},
            "timestamp": {"type": "string"},
            "message": {"type": "string"},
        },
    }
)
def interact_advisor(
    advisor_id: str, action: str = "check_in", notes: str = ""
) -> dict:
    """Record an advisor interaction to reset the 15-minute trust-decay clock."""
    return {
        "advisorId": advisor_id,
        "action": action,
        "notes": notes,
        "timestamp": _timestamp(),
        "message": "Advisor interaction recorded. Write access restored for 15 minutes.",
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000, stateless_http=True)
