#!/usr/bin/env python3
import argparse
import os
import sys
import webbrowser
import json
import secrets
import string
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# Import from utils folder websocket_helpers
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../utils"))
from websocket_helpers import create_presigned_url


class SonicClientHandler(BaseHTTPRequestHandler):
    """HTTP request handler that serves the Nova Sonic client"""

    # Class variables to store connection details
    websocket_url = None
    session_id = None
    is_presigned = False

    # Store config for regenerating URLs
    runtime_arn = None
    region = None
    service = None
    expires = None
    qualifier = None

    def log_message(self, format, *args):
        """Override to provide cleaner logging"""
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")

    def do_GET(self):
        """Handle GET requests"""
        parsed_path = urlparse(self.path)

        if parsed_path.path == "/" or parsed_path.path == "/index.html":
            self.serve_client_page()
        elif parsed_path.path == "/api/connection":
            self.serve_connection_info()
        else:
            self.send_error(404, "File not found")

    def do_POST(self):
        """Handle POST requests"""
        parsed_path = urlparse(self.path)

        if parsed_path.path == "/api/regenerate":
            self.regenerate_url()
        else:
            self.send_error(404, "Endpoint not found")

    def serve_client_page(self):
        """Serve the HTML client with pre-configured connection"""
        try:
            # Read the HTML template
            html_path = os.path.join(os.path.dirname(__file__), "sonic-client.html")
            with open(html_path, "r", encoding="utf-8") as f:
                html_content = f.read()

            # Inject the WebSocket URL if provided
            if self.websocket_url:
                html_content = html_content.replace(
                    'id="websocketUrl" placeholder="ws://localhost:8081/ws" value="ws://localhost:8081/ws"',
                    f'id="websocketUrl" placeholder="ws://localhost:8081/ws" value="{self.websocket_url}"',
                )

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.send_header("Content-Length", len(html_content.encode()))
            self.end_headers()
            self.wfile.write(html_content.encode())

        except FileNotFoundError:
            self.send_error(404, "sonic-client.html not found")
        except Exception as e:
            self.send_error(500, f"Internal server error: {str(e)}")

    def serve_connection_info(self):
        """Serve the connection information as JSON"""
        response = {
            "websocket_url": self.websocket_url or "ws://localhost:8081/ws",
            "session_id": self.session_id,
            "is_presigned": self.is_presigned,
            "can_regenerate": self.runtime_arn is not None,
            "status": "ok" if self.websocket_url else "no_connection",
        }

        response_json = json.dumps(response, indent=2)

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Content-Length", len(response_json.encode()))
        self.end_headers()
        self.wfile.write(response_json.encode())

    def regenerate_url(self):
        """Regenerate the presigned URL"""
        try:
            if not self.runtime_arn:
                error_response = {
                    "status": "error",
                    "message": "Cannot regenerate URL - not using presigned URL mode",
                }
                response_json = json.dumps(error_response)
                self.send_response(400)
                self.send_header("Content-type", "application/json")
                self.send_header("Content-Length", len(response_json.encode()))
                self.end_headers()
                self.wfile.write(response_json.encode())
                return

            # Generate new presigned URL
            base_url = f"wss://bedrock-agentcore.{self.region}.amazonaws.com/runtimes/{self.runtime_arn}/ws?qualifier={self.qualifier}"

            new_url = create_presigned_url(base_url, region=self.region, service=self.service, expires=self.expires)

            # Update the class variable
            SonicClientHandler.websocket_url = new_url

            response = {
                "status": "ok",
                "websocket_url": new_url,
                "expires_in": self.expires,
                "message": "URL regenerated successfully",
            }

            response_json = json.dumps(response, indent=2)

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Content-Length", len(response_json.encode()))
            self.end_headers()
            self.wfile.write(response_json.encode())

            print(f"✅ Regenerated presigned URL (expires in {self.expires} seconds)")

        except Exception as e:
            error_response = {"status": "error", "message": str(e)}
            response_json = json.dumps(error_response)
            self.send_response(500)
            self.send_header("Content-type", "application/json")
            self.send_header("Content-Length", len(response_json.encode()))
            self.end_headers()
            self.wfile.write(response_json.encode())


def main():
    parser = argparse.ArgumentParser(
        description="Start web service for Nova Sonic WebSocket client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Local WebSocket server (no authentication)
  python web_service.py --ws-url ws://localhost:8081/ws
  
  # AWS Bedrock with presigned URL
  python web_service.py --runtime-arn arn:aws:bedrock:us-west-2:123456789012:agent/AGENTID
  
  # Specify custom port
  python web_service.py --runtime-arn arn:aws:bedrock:us-west-2:123456789012:agent/AGENTID --port 8080
  
  # Custom region
  python web_service.py --runtime-arn arn:aws:bedrock:us-west-2:123456789012:agent/AGENTID \\
    --region us-east-1
""",
    )

    parser.add_argument(
        "--runtime-arn",
        help="Runtime ARN for AWS Bedrock connection (e.g., arn:aws:bedrock:region:account:agent/id)",
    )

    parser.add_argument(
        "--ws-url",
        help="WebSocket server URL for local connections (e.g., ws://localhost:8081/ws)",
    )

    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION"),
        help="AWS region (required if using --runtime-arn, from AWS_REGION env var)",
    )

    parser.add_argument(
        "--service",
        default="bedrock-agentcore",
        help="AWS service name (default: bedrock-agentcore)",
    )

    parser.add_argument(
        "--expires",
        type=int,
        default=3600,
        help="URL expiration time in seconds for presigned URLs (default: 3600 = 1 hour)",
    )

    parser.add_argument("--qualifier", default="DEFAULT", help="Runtime qualifier (default: DEFAULT)")

    parser.add_argument("--port", type=int, default=8000, help="Web server port (default: 8000)")

    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open browser")

    args = parser.parse_args()

    # Validate arguments
    if not args.runtime_arn and not args.ws_url:
        parser.error("Either --runtime-arn or --ws-url must be specified")

    if args.runtime_arn and args.ws_url:
        parser.error("Cannot specify both --runtime-arn and --ws-url")

    # Validate required parameters for AWS Bedrock connection
    if args.runtime_arn:
        if not args.region:
            parser.error("--region or AWS_REGION env var is required when using --runtime-arn")

    print("=" * 70)
    print("🎙️ Nova Sonic Client Web Service")
    print("=" * 70)

    websocket_url = None
    session_id = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(50))
    is_presigned = False

    try:
        # Generate presigned URL for AWS Bedrock
        if args.runtime_arn:
            base_url = f"wss://bedrock-agentcore.{args.region}.amazonaws.com/runtimes/{args.runtime_arn}/ws?qualifier={args.qualifier}"

            print(f"📡 Base URL: {base_url}")
            print(f"🔑 Runtime ARN: {args.runtime_arn}")
            print(f"🌍 Region: {args.region}")
            print(f"🆔 Session ID: {session_id}")
            print(f"⏰ URL expires in: {args.expires} seconds ({args.expires / 60:.1f} minutes)")
            print()
            print("🔐 Generating pre-signed URL...")

            websocket_url = create_presigned_url(
                base_url, region=args.region, service=args.service, expires=args.expires
            )
            is_presigned = True
            print("✅ Pre-signed URL generated successfully!")

        # Use provided WebSocket URL for local connections
        else:
            websocket_url = args.ws_url
            print(f"🔗 WebSocket URL: {websocket_url}")
            print("💡 Using local WebSocket connection (no authentication)")

        print(f"🌐 Web Server Port: {args.port}")
        print()

        # Set connection details in the handler class
        SonicClientHandler.websocket_url = websocket_url
        SonicClientHandler.session_id = session_id
        SonicClientHandler.is_presigned = is_presigned

        # Store config for regenerating URLs
        if args.runtime_arn:
            SonicClientHandler.runtime_arn = args.runtime_arn
            SonicClientHandler.region = args.region
            SonicClientHandler.service = args.service
            SonicClientHandler.expires = args.expires
            SonicClientHandler.qualifier = args.qualifier

        # Start web server
        server_address = ("", args.port)
        httpd = HTTPServer(server_address, SonicClientHandler)

        server_url = f"http://localhost:{args.port}"

        print("=" * 70)
        print("🌐 Web Server Started")
        print("=" * 70)
        print(f"📍 Server URL: {server_url}")
        print(f"🔗 Client Page: {server_url}/")
        print(f"📊 API Endpoint: {server_url}/api/connection")
        print()
        if is_presigned:
            print("💡 The pre-signed WebSocket URL is pre-populated in the client")
        else:
            print("💡 The WebSocket URL is pre-populated in the client")
        print("💡 Press Ctrl+C to stop the server")
        print("=" * 70)
        print()

        # Open browser automatically
        if not args.no_browser:
            print("🌐 Opening browser...")
            webbrowser.open(server_url)
            print()

        # Start serving
        httpd.serve_forever()

    except KeyboardInterrupt:
        print("\n\n👋 Shutting down server...")
        return 0
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
