"""What an MCP 2025-11-25 client sees: the same server in stateless mode, then in stateful mode.

This script needs MCP Python SDK v1, so it runs in its own virtual environment:

    python -m venv .venv-earlier
    .venv-earlier/bin/pip install -r requirements-earlier-client.txt
    .venv-earlier/bin/python invoke_earlier.py

An earlier client's elicitation is a request the server sends back during the tool call, over the
session, so it needs the runtime to serve earlier clients in stateful mode. Progress notifications
travel on the request's own response stream, so they work in either mode.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

HERE = Path(__file__).resolve().parent
UNSIGNED = frozenset({"authorization", "host", "content-length", "connection", "user-agent", "accept-encoding"})


class SigV4(httpx.Auth):
    """Sign each request with your AWS credentials (IAM inbound auth). httpx, because SDK v1 uses it."""

    requires_request_body = True

    def __init__(self, region: str) -> None:
        self.region = region
        self.credentials = boto3.Session().get_credentials()

    def auth_flow(self, request: httpx.Request):  # type: ignore[override]
        signed = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers={k: v for k, v in request.headers.items() if k.lower() not in UNSIGNED},
        )
        SigV4Auth(self.credentials.get_frozen_credentials(), "bedrock-agentcore", self.region).add_auth(signed)
        request.headers.update(dict(signed.headers))
        yield request


async def confirm(context: Any, params: types.ElicitRequestParams) -> types.ElicitResult:
    print(f"   server asks (elicitation/create): {params.message}")
    return types.ElicitResult(action="accept", content={"confirm": True})


async def run(arn: str, region: str, title: str, *, stateful: bool) -> bool:
    """Run the same three calls against one runtime and report whether it behaved as expected."""
    print(f"\n── {title}")
    url = (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{quote(arn, safe='')}/invocations?qualifier=DEFAULT"
    )
    # The runtime session ID must be at least 33 characters, or AgentCore Runtime answers HTTP 400.
    session_id = f"mcp-e2e-earlier-{'stateful' if stateful else 'stateless'}-{uuid.uuid4().hex}"
    session_header = {"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id}
    http = httpx.AsyncClient(auth=SigV4(region), headers=session_header, timeout=httpx.Timeout(30, read=120))
    progress: list[float] = []

    async def on_progress(value: float, total: float | None, message: str | None) -> None:
        progress.append(value)
        print(f"   progress {value:g}/{total:g}: {message}")

    ok = True
    transport = streamable_http_client(url, http_client=http, terminate_on_close=False)
    async with (
        http,
        transport as (read, write, get_id),
        ClientSession(read, write, elicitation_callback=confirm) as session,
    ):
        init = await session.initialize()
        print(f"   initialize: protocolVersion={init.protocolVersion}, Mcp-Session-Id={get_id()}")

        total = await session.call_tool("get_total", {})
        print(f"   get_total: {json.dumps(total.structuredContent)}")
        ok &= total.structuredContent is not None

        print("   add_expense (elicitation):")
        try:
            result = await session.call_tool("add_expense", {"amount": 12.5, "category": "food"})
            recorded = bool((result.structuredContent or {}).get("recorded"))
            print(f"   add_expense: {json.dumps(result.structuredContent)}")
        except Exception as error:  # noqa: BLE001 - expected in stateless mode
            print(f"   add_expense failed: {str(error)[:160]}")
            recorded = False
        ok &= recorded == stateful

        print("   export_expenses (progress):")
        export = await session.call_tool("export_expenses", {"batches": 3}, progress_callback=on_progress)
        print(f"   export_expenses: {json.dumps(export.structuredContent)}")
        ok &= len(progress) == 3  # progress needs no session, so it works in either mode
    if not stateful:
        print("   Elicitation needs a session, which this runtime doesn't keep for earlier clients.")
    return ok


async def main() -> int:
    config = json.loads((HERE / "runtime_config.json").read_text())
    region = config["region"]
    ok = await run(config["stateless"]["runtime_arn"], region, "Stateless runtime (the default)", stateful=False)
    ok &= await run(
        config["stateful"]["runtime_arn"], region, "Stateful runtime (MCP_LEGACY_STATEFUL=true)", stateful=True
    )
    print(f"\nAs expected: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    print("═══ An MCP 2025-11-25 client (MCP Python SDK v1) ═══")
    sys.exit(asyncio.run(main()))
