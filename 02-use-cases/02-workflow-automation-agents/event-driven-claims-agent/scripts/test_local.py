#!/usr/bin/env python3
"""Run the agent locally against a deployed Gateway.

Requires a running AgentCore dev server OR environment variables from .env.

Usage:
    # Start dev server first (separate terminal):
    #   cd app/claimsagent && agentcore dev --no-browser
    #
    # Then invoke:
    python3 scripts/test_local.py
    python3 scripts/test_local.py --prompt "File a claim for POL-12345. $3000 storm damage."
    python3 scripts/test_local.py --port 8080
"""

import argparse
import json
import os
import urllib.request
from pathlib import Path


def load_env():
    """Load .env file from project root if present."""
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


def invoke_local(port: int, prompt: str) -> str:
    """Invoke the local dev server and stream the response."""
    url = f"http://localhost:{port}/invocations"

    # B310: Validate URL scheme is http(s) only to prevent file:// or custom scheme abuse
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Unsupported URL scheme: {url}")

    payload = json.dumps({"prompt": prompt}).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Agentcore-Local": "true",
        },
    )

    parts = []
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310
            for line in resp:
                decoded = line.decode("utf-8").strip()
                if not decoded:
                    continue
                if decoded.startswith("data: "):
                    chunk = decoded[6:]
                    if chunk.startswith('"') and chunk.endswith('"'):
                        chunk = json.loads(chunk)
                    parts.append(chunk)
                    print(chunk, end="", flush=True)
    except urllib.error.URLError as e:
        print(f"\nError: Could not connect to dev server on port {port}.")
        print("Make sure the dev server is running:")
        print("  cd app/claimsagent && agentcore dev --no-browser")
        raise SystemExit(1) from e

    print()
    return "".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Invoke claims agent local dev server")
    parser.add_argument("--port", type=int, default=8080, help="Dev server port (default: 8080)")
    parser.add_argument(
        "--prompt",
        default=(
            "I need to file a claim under policy POL-12345. "
            "A storm caused a tree branch to fall on my car, damaging the roof. "
            "Estimated repair cost is $4,500."
        ),
        help="Claim prompt to send",
    )
    args = parser.parse_args()

    load_env()

    print(f"Invoking local dev server on port {args.port}...")
    print(f"Prompt: {args.prompt}")
    print("-" * 60)

    invoke_local(args.port, args.prompt)


if __name__ == "__main__":
    main()
