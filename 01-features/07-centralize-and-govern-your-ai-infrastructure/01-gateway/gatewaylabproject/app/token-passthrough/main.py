from mcp.server.fastmcp import FastMCP

mcp = FastMCP(host="0.0.0.0", stateless_http=True)  # nosec B104


@mcp.tool()
def whoami(message: str) -> str:
    """Returns info about the request. Used to verify token passthrough."""
    return f"Token passthrough MCP server received: {message}"


@mcp.tool()
def get_profile() -> str:
    """Get user profile — requires the original user token for authorization."""
    return "Profile: demo-user (token verified by MCP server)"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
