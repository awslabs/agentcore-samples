import random

from fastmcp import FastMCP

mcp = FastMCP("Stock Price MCP Server")

# Mock stock data
STOCKS = {
    "AAPL": {"name": "Apple Inc.", "base_price": 195.50},
    "GOOGL": {"name": "Alphabet Inc.", "base_price": 141.80},
    "AMZN": {"name": "Amazon.com Inc.", "base_price": 185.60},
    "MSFT": {"name": "Microsoft Corp.", "base_price": 420.30},
    "TSLA": {"name": "Tesla Inc.", "base_price": 245.20},
}


@mcp.tool()
def get_stock_price(symbol: str) -> dict:
    """Get the current stock price for a given ticker symbol."""
    symbol = symbol.upper()
    if symbol not in STOCKS:
        return {"error": f"Unknown symbol: {symbol}", "available": list(STOCKS.keys())}
    stock = STOCKS[symbol]
    price = round(stock["base_price"] * (1 + random.uniform(-0.05, 0.05)), 2)
    change = round(price - stock["base_price"], 2)
    change_pct = round((change / stock["base_price"]) * 100, 2)
    return {
        "symbol": symbol,
        "name": stock["name"],
        "price": price,
        "change": change,
        "change_percent": change_pct,
        "volume": random.randint(1_000_000, 50_000_000),
    }


@mcp.tool()
def get_market_summary() -> dict:
    """Get a summary of major market indices."""
    return {
        "indices": [
            {
                "name": "S&P 500",
                "value": round(5200 + random.uniform(-50, 50), 2),
                "change_percent": round(random.uniform(-1.5, 1.5), 2),
            },
            {
                "name": "NASDAQ",
                "value": round(16400 + random.uniform(-150, 150), 2),
                "change_percent": round(random.uniform(-2.0, 2.0), 2),
            },
            {
                "name": "DOW",
                "value": round(39200 + random.uniform(-200, 200), 2),
                "change_percent": round(random.uniform(-1.0, 1.0), 2),
            },
        ]
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8001, path="/stock-mcp/")  # nosec B104
