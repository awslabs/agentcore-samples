"""
Call the deployed MCP server with an MCP client, over ONE session.

    pip install -r requirements.txt
    python invoke.py
    python invoke.py --tool greet --args '{"name": "Ada"}'
    python invoke.py --new-session

One `ClientSession` is opened and every call goes through it, so the MCP
handshake happens once and the transport echoes `Mcp-Session-Id` back on each
request by itself. The AgentCore session id is cached in `.mcp_session.json` and
reused across runs, so only the first run pays the cold start.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import anyio
import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "cp_config.json"
SESSION_FILE = HERE / ".mcp_session.json"

# Cold starts of minutes are normal: an EC2 instance has to be launched and
# seeded before the server exists.
TIMEOUT = httpx.Timeout(900.0, connect=30.0)

# httpx owns these; signing them would break the signature when it rewrites them.
UNSIGNED = {"authorization", "host", "content-length", "connection",
            "user-agent", "accept-encoding"}


class SigV4(httpx.Auth):
    """Sign every request the MCP transport makes."""

    requires_request_body = True

    def __init__(self, credentials, region: str) -> None:
        self.credentials, self.region = credentials, region

    def auth_flow(self, request):
        body = request.read()
        signable = AWSRequest(
            method=request.method, url=str(request.url), data=body,
            headers={k: v for k, v in request.headers.items() if k.lower() not in UNSIGNED},
        )
        SigV4Auth(self.credentials.get_frozen_credentials(),
                  "bedrock-agentcore", self.region).add_auth(signable)
        for key, value in signable.headers.items():
            request.headers[key] = value
        yield request


def session_id(arn: str, fresh: bool) -> tuple[str, bool]:
    """The AgentCore session id from the last run, or a new one (min 33 chars)."""
    if fresh:
        SESSION_FILE.unlink(missing_ok=True)
    else:
        try:
            cached = json.loads(SESSION_FILE.read_text())
            if cached["arn"] == arn and time.time() - cached["at"] < 900:
                return cached["id"], True
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            pass
    return f"mcp-{uuid.uuid4().hex}", False


def text(result) -> str:
    return "\n".join(b.text for b in result.content if getattr(b, "type", None) == "text")


async def run(url: str, region: str, sid: str, args) -> None:
    client = httpx.AsyncClient(
        headers={"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sid},
        timeout=TIMEOUT,
        auth=SigV4(boto3.Session(region_name=region).get_credentials(), region),
    )
    async with client, \
            streamable_http_client(url, http_client=client, terminate_on_close=False) as (r, w, _), \
            ClientSession(r, w) as mcp:
        started = time.time()
        info = await mcp.initialize()
        print(f"initialize   [{time.time() - started:.1f}s] "
              f"{info.serverInfo.name} {info.serverInfo.version}")

        if args.tool:
            started = time.time()
            result = await mcp.call_tool(args.tool, json.loads(args.args))
            print(f"{args.tool}  [{time.time() - started:.1f}s] {text(result)}")
            return

        started = time.time()
        tools = await mcp.list_tools()
        print(f"tools/list   [{time.time() - started:.1f}s] "
              f"{', '.join(t.name for t in tools.tools)}")

        started = time.time()
        result = await mcp.call_tool("add_numbers", {"a": 40, "b": 2})
        print(f"add_numbers  [{time.time() - started:.1f}s] 40 + 2 = {text(result)}")

        # Same process_id every time means one instance served the whole session.
        for i in range(1, args.calls + 1):
            started = time.time()
            result = await mcp.call_tool("whoami", {})
            who = json.loads(text(result))
            print(f"whoami #{i}    [{time.time() - started:.1f}s] "
                  f"{who['process_id']}  {who['hostname']}  {who['instance_type']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", help="call one tool and exit")
    parser.add_argument("--args", default="{}", help="JSON arguments for --tool")
    parser.add_argument("--calls", type=int, default=3, help="whoami calls (default: 3)")
    parser.add_argument("--new-session", action="store_true", help="start cold on purpose")
    args = parser.parse_args()

    if not CONFIG.is_file():
        sys.exit(f"{CONFIG.name} not found — run `python deploy.py` first.")
    config = json.loads(CONFIG.read_text())
    region = config["region"]
    arn = config["runtimes"]["mcp"]["arn"]
    sid, reused = session_id(arn, args.new_session)

    endpoint = boto3.client("bedrock-agentcore", region_name=region).meta.endpoint_url
    url = f"{endpoint.rstrip('/')}/runtimes/{quote(arn, safe='')}/invocations?qualifier=DEFAULT"

    print(f"session      : {sid}  ({'reused — expect warm' if reused else 'new — first call is cold'})\n")
    anyio.run(run, url, region, sid, args)
    SESSION_FILE.write_text(json.dumps({"id": sid, "arn": arn, "at": time.time()}))


if __name__ == "__main__":
    main()
