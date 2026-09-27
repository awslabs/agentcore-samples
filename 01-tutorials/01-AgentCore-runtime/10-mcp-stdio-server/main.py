from fastmcp import FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.server.proxy import ProxyClient
from starlette.responses import JSONResponse
import os

# Create a proxy directly from a config dictionary
transport = StdioTransport(
    command="uv",
    args=["run", "awslabs.aws-documentation-mcp-server"],
)

# Create a proxy to the configured server (auto-creates ProxyClient)
proxy = FastMCP.as_proxy(ProxyClient(transport), name="Proxy", stateless_http=True)


@proxy.custom_route("/ping", ["GET"])
def ping(req):
    return JSONResponse({"status": "healthy"})


# Run the proxy with stdio transport for local access
if __name__ == "__main__":
    # Default to localhost for security, but allow override via environment variable
    # In Docker, set HOST=0.0.0.0 to bind to all interfaces
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    proxy.run(transport="streamable-http", host=host, port=port)
