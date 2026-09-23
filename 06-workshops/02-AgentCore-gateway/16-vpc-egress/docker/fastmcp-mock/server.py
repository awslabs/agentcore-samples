from fastmcp import FastMCP
from datetime import datetime

mcp = FastMCP("Mock MCP Server")


@mcp.tool()
def echo(message: str) -> str:
    """Echo back the provided message."""
    return message


@mcp.tool()
def add(a: float, b: float) -> float:
    """Add two numbers together."""
    return a + b


@mcp.tool()
def get_time() -> str:
    """Get the current server time ISO format."""
    return datetime.now().isoformat()


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)  # nosec B104
