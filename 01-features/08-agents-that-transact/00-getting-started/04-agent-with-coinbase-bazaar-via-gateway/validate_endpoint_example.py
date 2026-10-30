"""
Validate an x402 Endpoint — optional Bazaar seller-readiness example.

Demonstrates the Bazaar's third tool, `validate_endpoint`: read-only diagnostics that
probe an x402 URL and report whether it is correctly configured for Bazaar discovery
(HTTPS, returns 402, valid x402 v2 payload, accepts[] fields, discovery extension, and
whether the facilitator would index it). It does NOT make a payment or index anything —
useful if you're publishing your own paid tool and want to check it's Bazaar-ready.

Usage:
    python validate_endpoint_example.py <https-url> [http-method]
    # or set X402_URL / X402_METHOD in the environment

Example:
    python validate_endpoint_example.py https://api.example.com/weather GET

Requires: GATEWAY_URL in the shared .env (00-getting-started/.env). CUSTOM_JWT gateways
also need CLIENT_ID / CLIENT_SECRET / TOKEN_URL (auto-detected, same as the other scripts).
"""

import json
import os
import sys
from datetime import timedelta

from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(ENV_FILE, override=True)

TARGET = os.environ.get("BAZAAR_TARGET_NAME", "CoinbaseBazaar")
VALIDATE_TOOL = f"{TARGET}___validate_endpoint"


def _extract_json(tool_result):
    for block in tool_result.get("content", []) or []:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except (ValueError, TypeError):
                return {"raw": text}
    return {}


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("X402_URL")
    method = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("X402_METHOD", "GET")
    if not url:
        print("Usage: python validate_endpoint_example.py <https-url> [http-method]")
        print("Tip: pass the URL of an x402 endpoint you want to check for Bazaar readiness.")
        sys.exit(1)

    gateway_url = os.environ.get("GATEWAY_URL", "")
    if not gateway_url:
        print("ERROR: GATEWAY_URL not set in .env. Deploy the Gateway first (README Step 1).")
        sys.exit(1)

    # Gateway auth — auto-detect (same logic as bazaar_gateway_agent.py)
    gateway_headers = {}
    client_id = os.environ.get("CLIENT_ID")
    client_secret = os.environ.get("CLIENT_SECRET")
    token_url = os.environ.get("TOKEN_URL")
    if client_id and client_secret and token_url:
        from utils import get_oauth_token

        token = get_oauth_token(token_url, client_id, client_secret)
        gateway_headers = {"Authorization": f"Bearer {token}"}
        print("Gateway auth: CUSTOM_JWT (OAuth token acquired)")
    else:
        print("Gateway auth: NONE (no CLIENT_ID/CLIENT_SECRET/TOKEN_URL in .env)")

    print(f"Gateway: {gateway_url}")
    print(f"Validating endpoint: {method} {url}\n")

    mcp_client = MCPClient(
        lambda: streamablehttp_client(gateway_url, headers=gateway_headers, timeout=timedelta(seconds=120))
    )

    with mcp_client:
        result = mcp_client.call_tool_sync(
            tool_use_id="validate-endpoint-1",
            name=VALIDATE_TOOL,
            arguments={"url": url, "method": method},
        )

    report = _extract_json(result)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
