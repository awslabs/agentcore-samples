import asyncio
import httpx
import os
import threading
import time
import webbrowser
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Patch httpx at the request level to inject User-Agent header
# This ensures ALL HTTP requests have the User-Agent header, including OAuth discovery calls
_original_httpx_request = httpx.Request.__init__

def _patched_httpx_request_init(self, method, url, *args, **kwargs):
    """Patched Request.__init__ that injects User-Agent header into all HTTP requests."""
    # Get or create headers
    headers = kwargs.get('headers')
    if headers is None:
        headers = {}
        kwargs['headers'] = headers
    
    # Convert to mutable dict if needed
    if not isinstance(headers, dict):
        headers = dict(headers)
        kwargs['headers'] = headers
    
    # Inject User-Agent if not present (case-insensitive check)
    if 'User-Agent' not in headers and 'user-agent' not in headers:
        headers['User-Agent'] = 'python-mcp-sdk/1.0 (BedrockAgentCore-Runtime)'
    
    # Call original __init__
    _original_httpx_request(self, method, url, *args, **kwargs)

# Apply the patch globally before importing MCP modules
httpx.Request.__init__ = _patched_httpx_request_init

# Now import MCP modules - they will use patched httpx
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken


class InMemoryTokenStorage(TokenStorage):
    """Simple in-memory token storage implementation."""

    def __init__(self):
        self._tokens: OAuthToken | None = None
        self._client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self._tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._client_info = client_info


class CallbackHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler to capture OAuth callback."""

    def __init__(self, request, client_address, server, callback_data):
        """Initialize with callback data storage."""
        self.callback_data = callback_data
        super().__init__(request, client_address, server)

    def do_GET(self):
        """Handle GET request from OAuth redirect."""
        parsed = urlparse(self.path)
        query_params = parse_qs(parsed.query)
        #print(f'Query Params parsed: {query_params}')

        if "code" in query_params:
            self.callback_data["authorization_code"] = query_params["code"][0]
            self.callback_data["state"] = query_params.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
            <html>
            <body>
                <h1>Authorization Successful!</h1>
                <p>You can close this window and return to the terminal.</p>
                <script>setTimeout(() => window.close(), 2000);</script>
            </body>
            </html>
            """)
        elif "error" in query_params:
            self.callback_data["error"] = query_params["error"][0]
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                f"""
            <html>
            <body>
                <h1>Authorization Failed</h1>
                <p>Error: {query_params["error"][0]}</p>
                <p>You can close this window and return to the terminal.</p>
            </body>
            </html>
            """.encode()
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


class CallbackServer:
    """Simple server to handle OAuth callbacks."""

    def __init__(self, port=3030):
        self.port = port
        self.server = None
        self.thread = None
        self.callback_data = {"authorization_code": None, "state": None, "error": None}

    def _create_handler_with_data(self):
        """Create a handler class with access to callback data."""
        callback_data = self.callback_data

        class DataCallbackHandler(CallbackHandler):
            def __init__(self, request, client_address, server):
                super().__init__(request, client_address, server, callback_data)

        return DataCallbackHandler

    def start(self):
        """Start the callback server in a background thread."""
        handler_class = self._create_handler_with_data()
        self.server = HTTPServer(("localhost", self.port), handler_class)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"🖥️  Started callback server on http://localhost:{self.port}")

    def stop(self):
        """Stop the callback server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=1)

    def wait_for_callback(self, timeout=300):
        """Wait for OAuth callback with timeout."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.callback_data["authorization_code"]:
                return self.callback_data["authorization_code"]
            elif self.callback_data["error"]:
                raise Exception(f"OAuth error: {self.callback_data['error']}")
            time.sleep(0.1)
        raise Exception("Timeout waiting for OAuth callback")

    def get_state(self):
        """Get the received state parameter."""
        return self.callback_data["state"]


def add_auth0_audience_parameter(authorization_url: str, audience: str) -> str:
    """
    Add Auth0 'audience' parameter to authorization URL.
    
    Auth0 requires the 'audience' parameter to identify which API's token settings
    to use. Without it, Auth0 returns opaque tokens or JWE instead of JWT.
    
    This function properly adds the audience parameter while preserving all existing
    query parameters (including the OAuth 'resource' parameter).
    
    Args:
        authorization_url: The authorization URL from the OAuth flow
        audience: The Auth0 API identifier (e.g., "runtime-api")
    
    Returns:
        Modified URL with audience parameter added
    
    Reference:
        https://auth0.com/docs/secure/tokens/access-tokens/get-access-tokens
    """
    # Only apply to Auth0 URLs that don't already have audience
    if 'auth0.com' not in authorization_url or 'audience=' in authorization_url:
        return authorization_url
    
    # Parse URL and query parameters
    parsed = urlparse(authorization_url)
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    
    # Add audience parameter
    query_params['audience'] = [audience]
    
    # Rebuild URL with new parameter
    new_query = urlencode(query_params, doseq=True)
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment
    ))


class SimpleAuthClient:
    """Simple MCP client with Auth0 OAuth support."""

    def __init__(
        self,
        server_url: str,
        transport_type: str = "streamable-http",
        auth0_audience: str | None = None,
    ):
        self.server_url = server_url
        self.transport_type = transport_type
        self.auth0_audience = auth0_audience
        self.session: ClientSession | None = None

    async def connect(self):
        """Connect to the MCP server."""
        print(f"🔗 Attempting to connect to {self.server_url}...")

        try:
            callback_server = CallbackServer(port=3030)
            callback_server.start()

            async def callback_handler() -> tuple[str, str | None]:
                """Wait for OAuth callback and return auth code and state."""
                print("⏳ Waiting for authorization callback...")
                try:
                    auth_code = callback_server.wait_for_callback(timeout=300)
                    return auth_code, callback_server.get_state()
                finally:
                    callback_server.stop()

            client_metadata_dict = {
                "client_name": "MCP Auth0 Client",
                "redirect_uris": ["http://localhost:3030/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            }

            async def redirect_handler(authorization_url: str) -> None:
                """Redirect handler that opens the URL in a browser with Auth0 audience parameter."""
                # Add Auth0 audience parameter if configured
                if self.auth0_audience:
                    authorization_url = add_auth0_audience_parameter(
                        authorization_url,
                        self.auth0_audience
                    )
                
                webbrowser.open(authorization_url)

            print("\n🔧 Creating OAuth client provider...")
            # Create OAuth authentication handler
            # Note: httpx.AsyncClient is globally patched to inject User-Agent header
            oauth_auth = OAuthClientProvider(
                server_url=self.server_url,
                client_metadata=OAuthClientMetadata.model_validate(client_metadata_dict),
                storage=InMemoryTokenStorage(),
                redirect_handler=redirect_handler,
                callback_handler=callback_handler,
            )
            print("🔧 OAuth client provider created successfully")

            # Create transport with auth handler based on transport type
            if self.transport_type == "sse":
                print("📡 Opening SSE transport connection with auth...")
                async with sse_client(
                    url=self.server_url,
                    auth=oauth_auth,
                    timeout=60,
                ) as (read_stream, write_stream):
                    await self._run_session(read_stream, write_stream, None)
            else:
                print("📡 Opening StreamableHTTP transport connection with auth...")
                async with streamablehttp_client(
                    url=self.server_url,
                    auth=oauth_auth,
                    timeout=timedelta(seconds=60),
                ) as (read_stream, write_stream, get_session_id):
                    await self._run_session(read_stream, write_stream, get_session_id)

        except Exception as e:
            print(f"❌ Failed to connect: {e}")
            import traceback
            traceback.print_exc()

    async def _run_session(self, read_stream, write_stream, get_session_id):
        """Run the MCP session with the given streams."""
        print("🤝 Initializing MCP session...")
        async with ClientSession(read_stream, write_stream) as session:
            self.session = session
            print("⚡ Starting session initialization...")
            await session.initialize()
            print("✨ Session initialization complete!")

            print(f"\n✅ Connected to MCP server at {self.server_url}")
            if get_session_id:
                session_id = get_session_id()
                if session_id:
                    print(f"Session ID: {session_id}")

            # Run interactive loop
            #await self.interactive_loop()
            await self.invoke_mcp_server()

    async def list_tools(self):
        """List available tools from the server."""
        if not self.session:
            print("❌ Not connected to server")
            return

        try:
            result = await self.session.list_tools()
            if hasattr(result, "tools") and result.tools:
                print("\n📋 Available tools:")
                for i, tool in enumerate(result.tools, 1):
                    print(f"{i}. {tool.name}")
                    if tool.description:
                        print(f"   Description: {tool.description}")
                    print()
            else:
                print("No tools available")
        except Exception as e:
            print(f"❌ Failed to list tools: {e}")

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None):
        """Call a specific tool."""
        if not self.session:
            print("❌ Not connected to server")
            return

        try:
            result = await self.session.call_tool(tool_name, arguments or {})
            print(f"\n🔧 Tool '{tool_name}' result:")
            if hasattr(result, "content"):
                for content in result.content:
                    if content.type == "text":
                        print(content.text)
                    else:
                        print(content)
            else:
                print(result)
        except Exception as e:
            print(f"❌ Failed to call tool '{tool_name}': {e}")

    async def invoke_mcp_server(self):
        """Invoke MCP server and tools"""
        print("Showing available tools: ")
        await self.list_tools()
        
        tool_name = "add_numbers"
        arguments= {'a':2, 'b':2}
        print(f"Invoking {tool_name} tool, with parameters {arguments}.")
        await self.call_tool(tool_name, arguments)


        tool_name = "multiply_numbers"
        arguments= {'a':2, 'b':4}
        print(f"Invoking {tool_name} tool, with parameters {arguments}.")
        await self.call_tool(tool_name, arguments)

        tool_name = "greet_user"
        arguments= {'name': 'Somebody'}
        print(f"Invoking {tool_name} tool, with parameters {arguments}.")
        await self.call_tool(tool_name, arguments)


async def main(agent_arn, base_endpoint, auth0_audience):
    """Main entry point."""
    
    if not agent_arn:
        print("❌ Please set AGENT_ARN environment variable")
        print("Example: export AGENT_ARN='arn:aws:bedrock:us-west-2:123456789012:agent/ABCD1234'")
        return

    # Encode the ARN for use in URL
    encoded_arn = agent_arn.replace(':', '%3A').replace('/', '%2F')
    
    # Construct MCP URL from encoded ARN (no qualifier - SDK discovers it from PRM API)
    server_url = f"{base_endpoint}/runtimes/{encoded_arn}/invocations"
    
    # Get optional transport type
    transport_type = os.getenv("MCP_TRANSPORT_TYPE", "streamable-http")

    print("🚀 MCP Auth0 Client")
    print(f"Agent ARN: {agent_arn}")
    print(f"Endpoint: {base_endpoint}")
    print(f"Connecting to: {server_url}")
    print(f"Transport type: {transport_type}")
    if auth0_audience:
        print(f"Auth0 audience: {auth0_audience}")

    # Start connection flow - OAuth will be handled automatically
    client = SimpleAuthClient(
        server_url,
        transport_type,
        auth0_audience,
    )
    await client.connect()


def run_test():
    """CLI entry point for uv script."""
    asyncio.run(main())


if __name__ == "__main__":
    run_test()
