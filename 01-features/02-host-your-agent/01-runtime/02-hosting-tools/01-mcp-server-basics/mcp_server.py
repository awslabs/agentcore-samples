"""
Basic MCP Server — hosted on AgentCore Runtime.

Exposes three tools via the Model Context Protocol:
- add_numbers: adds two numbers
- multiply_numbers: multiplies two numbers
- greet: generates a greeting message

This is NOT a BedrockAgentCoreApp. An HTTP agent uses the SDK's app object and
answers POST /invocations. An MCP server speaks JSON-RPC over the MCP
streamable-HTTP transport instead, so it is a plain FastMCP server and
`bedrock_agentcore` is not involved at all.
"""

from typing import Literal

from mcp.server.fastmcp import FastMCP

# host, port, path and stateless_http are the AgentCore Runtime service contract,
# not preferences:
#   - Port 8000 is fixed for MCP (HTTP agents use 8080, A2A uses 9000).
#   - Host 0.0.0.0, or the runtime health check cannot reach the server.
#   - stateless_http=True, because the runtime does not guarantee that two requests
#     in a session reach the same process. FastMCP defaults to stateful, which fails
#     behind a load balancer with "Missing session ID".
#   - Path /mcp is where FastMCP's streamable-http transport mounts by default.
#
# json_response=True is this sample's own choice, NOT a runtime requirement: it makes
# every reply a plain JSON body so invoke.py can json.loads() it directly. The runtime
# accepts text/event-stream too, but then the client has to unwrap the event stream.
# Note the trade-off — in JSON mode anything that is not a response or error is
# dropped, so progress notifications and server-initiated requests (sampling,
# elicitation) never reach the client.
mcp = FastMCP(
    "basic-tools",
    host="0.0.0.0",  # nosec B104
    port=8000,
    stateless_http=True,
    json_response=True,
)


# Keep tool docstrings to a single line. FastMCP publishes the whole docstring as the
# tool's `description`, so Args:/Returns: blocks are sent to every client on every
# tools/list — duplicating what the generated inputSchema already says.
@mcp.tool()
def add_numbers(a: float, b: float) -> float:
    """Add two numbers together."""
    return a + b


@mcp.tool()
def multiply_numbers(a: float, b: float) -> float:
    """Multiply two numbers."""
    return a * b


# `language` is a Literal, not a bare str, so the published inputSchema carries an
# enum of the accepted values. With a plain `str` the model gets no hint about what
# is valid and an unknown language silently falls back to English.
@mcp.tool()
def greet(name: str, language: Literal["english", "spanish", "french"] = "english") -> str:
    """Greet someone in English, Spanish or French."""
    greetings = {
        "english": f"Hello, {name}! Welcome!",
        "spanish": f"¡Hola, {name}! ¡Bienvenido!",
        "french": f"Bonjour, {name}! Bienvenue!",
    }
    return greetings[language]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
