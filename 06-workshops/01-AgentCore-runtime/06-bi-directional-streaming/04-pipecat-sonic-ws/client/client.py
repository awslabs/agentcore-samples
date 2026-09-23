#!/usr/bin/env python3
"""
Lightweight signing server for the Pipecat Vite client.

When connecting to an AgentCore-deployed runtime, the browser client
cannot perform SigV4 signing.  This script:

1. Generates a SigV4 presigned wss:// URL for the given runtime ARN.
2. Exposes a POST /start endpoint that returns {"ws_url": "<presigned>"}.
3. The Vite dev server proxies /start here so the browser app can fetch it.

For local development (no AgentCore), you don't need this — the pipecat
websocket server itself serves /start with ws://localhost:8081/ws.
"""

import argparse
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../utils"))
from websocket_helpers import create_presigned_url


class SigningHandler(BaseHTTPRequestHandler):
    runtime_arn = None
    region = None
    expires = 3600
    qualifier = "DEFAULT"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[signing-server] {fmt % args}\n")

    def do_POST(self):
        if self.path == "/start":
            self._handle_start()
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path == "/ping":
            self._json_response({"status": "ok"})
        else:
            self.send_error(404)

    def _handle_start(self):
        base_url = (
            f"wss://bedrock-agentcore.{self.region}.amazonaws.com"
            f"/runtimes/{self.runtime_arn}/ws?qualifier={self.qualifier}"
        )
        try:
            signed = create_presigned_url(
                base_url,
                region=self.region,
                service="bedrock-agentcore",
                expires=self.expires,
            )
            print(f"✅ Generated presigned URL (expires in {self.expires}s)")
            self._json_response({"ws_url": signed})
        except Exception as e:
            print(f"❌ Signing error: {e}")
            self._json_response({"error": str(e)}, status=500)

    def _json_response(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description="Signing server for Pipecat client")
    parser.add_argument("--runtime-arn", required=True, help="AgentCore runtime ARN")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--port", type=int, default=8081, help="Port (default: 8081)")
    parser.add_argument("--expires", type=int, default=3600, help="URL expiry seconds")
    parser.add_argument("--qualifier", default="DEFAULT")
    args = parser.parse_args()

    SigningHandler.runtime_arn = args.runtime_arn
    SigningHandler.region = args.region
    SigningHandler.expires = args.expires
    SigningHandler.qualifier = args.qualifier

    print("=" * 60)
    print("🔐 Pipecat Signing Server")
    print("=" * 60)
    print(f"  Runtime ARN: {args.runtime_arn}")
    print(f"  Region:      {args.region}")
    print(f"  Port:        {args.port}")
    print(f"  Expiry:      {args.expires}s")
    print()
    print("  The Vite client proxies /start to this server.")
    print("  Press Ctrl+C to stop.")
    print("=" * 60)

    httpd = HTTPServer(("", args.port), SigningHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Signing server stopped.")


if __name__ == "__main__":
    main()
