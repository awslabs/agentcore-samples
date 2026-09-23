#!/usr/bin/env python3
"""
LangChain Voice Agent Client

Serves the HTML client and manages WebSocket connection details.
Follows the same pattern as the Strands client.
"""

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


class LangChainClientHandler(BaseHTTPRequestHandler):
    """HTTP request handler that serves the LangChain voice agent client."""

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
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")

    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path in ("/", "/index.html"):
            self.serve_client_page()
        elif parsed_path.path == "/api/connection":
            self.serve_connection_info()
        elif parsed_path.path == "/api/profiles":
            self.serve_profiles()
        else:
            self.send_error(404, "File not found")

    def do_POST(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/api/regenerate":
            self.regenerate_url()
        else:
            self.send_error(404, "Endpoint not found")

    def serve_client_page(self):
        try:
            html_path = os.path.join(os.path.dirname(__file__), "langchain-client.html")
            with open(html_path, "r", encoding="utf-8") as f:
                html_content = f.read()

            if self.websocket_url:
                html_content = html_content.replace(
                    'id="presignedUrl" placeholder="wss://endpoint/runtimes/arn/ws?..."',
                    f'id="presignedUrl" placeholder="wss://endpoint/runtimes/arn/ws?..." value="{self.websocket_url}"',
                )

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.send_header("Content-Length", len(html_content.encode()))
            self.end_headers()
            self.wfile.write(html_content.encode())
        except FileNotFoundError:
            self.send_error(404, "langchain-client.html not found")
        except Exception as e:
            self.send_error(500, f"Internal server error: {str(e)}")

    def serve_connection_info(self):
        response = {
            "websocket_url": self.websocket_url or "",
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

    def serve_profiles(self):
        try:
            profiles_path = os.path.join(os.path.dirname(__file__), "profiles.json")
            with open(profiles_path, "r", encoding="utf-8") as f:
                profiles_content = f.read()
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Content-Length", len(profiles_content.encode()))
            self.end_headers()
            self.wfile.write(profiles_content.encode())
        except FileNotFoundError:
            empty = "[]"
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Content-Length", len(empty.encode()))
            self.end_headers()
            self.wfile.write(empty.encode())
        except Exception as e:
            self.send_error(500, f"Internal server error: {str(e)}")

    def regenerate_url(self):
        try:
            if not self.runtime_arn:
                error_response = {
                    "status": "error",
                    "message": "Not using presigned URL mode",
                }
                response_json = json.dumps(error_response)
                self.send_response(400)
                self.send_header("Content-type", "application/json")
                self.send_header("Content-Length", len(response_json.encode()))
                self.end_headers()
                self.wfile.write(response_json.encode())
                return

            base_url = (
                f"wss://bedrock-agentcore.{self.region}.amazonaws.com"
                f"/runtimes/{self.runtime_arn}/ws?qualifier={self.qualifier}"
            )
            new_url = create_presigned_url(base_url, region=self.region, service=self.service, expires=self.expires)
            LangChainClientHandler.websocket_url = new_url

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
        description="Start web service for LangChain Voice Agent client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Local WebSocket server (no authentication)
  python client.py --ws-url ws://localhost:8080/ws

  # AWS Bedrock with presigned URL
  python client.py --runtime-arn arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/RUNTIMEID

  # Custom port
  python client.py --ws-url ws://localhost:8080/ws --port 8000
""",
    )
    parser.add_argument("--runtime-arn", help="Runtime ARN for AWS Bedrock connection")
    parser.add_argument(
        "--ws-url",
        help="WebSocket server URL for local connections (e.g., ws://localhost:8080/ws)",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION", "us-east-1"),
        help="AWS region (default: us-east-1)",
    )
    parser.add_argument("--service", default="bedrock-agentcore", help="AWS service name")
    parser.add_argument(
        "--expires",
        type=int,
        default=3600,
        help="URL expiration time in seconds (default: 3600)",
    )
    parser.add_argument("--qualifier", default="DEFAULT", help="Runtime qualifier (default: DEFAULT)")
    parser.add_argument("--port", type=int, default=8000, help="Web server port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open browser")

    args = parser.parse_args()

    if not args.runtime_arn and not args.ws_url:
        parser.error("Either --runtime-arn or --ws-url must be specified")
    if args.runtime_arn and args.ws_url:
        parser.error("Cannot specify both --runtime-arn and --ws-url")

    # Extract region from runtime ARN if provided
    if args.runtime_arn:
        arn_parts = args.runtime_arn.split(":")
        if len(arn_parts) >= 4:
            arn_region = arn_parts[3]
            if arn_region and arn_region != args.region:
                args.region = arn_region

    print("=" * 70)
    print("🎙️ LangChain Voice Agent Client")
    print("=" * 70)

    websocket_url = None
    session_id = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(50))
    is_presigned = False

    try:
        if args.runtime_arn:
            base_url = (
                f"wss://bedrock-agentcore.{args.region}.amazonaws.com"
                f"/runtimes/{args.runtime_arn}/ws?qualifier={args.qualifier}"
            )
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
        else:
            websocket_url = args.ws_url
            print(f"🔗 WebSocket URL: {websocket_url}")
            print("💡 Using local WebSocket connection (no authentication)")

        print(f"🌐 Web Server Port: {args.port}")
        print()

        LangChainClientHandler.websocket_url = websocket_url
        LangChainClientHandler.session_id = session_id
        LangChainClientHandler.is_presigned = is_presigned

        if args.runtime_arn:
            LangChainClientHandler.runtime_arn = args.runtime_arn
            LangChainClientHandler.region = args.region
            LangChainClientHandler.service = args.service
            LangChainClientHandler.expires = args.expires
            LangChainClientHandler.qualifier = args.qualifier

        server_address = ("", args.port)
        httpd = HTTPServer(server_address, LangChainClientHandler)
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

        if not args.no_browser:
            print("🌐 Opening browser...")
            webbrowser.open(server_url)
            print()

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
