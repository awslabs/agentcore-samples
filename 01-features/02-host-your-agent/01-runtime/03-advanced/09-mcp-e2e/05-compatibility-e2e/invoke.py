"""What an MCP Python SDK v2 client sees: the new server, and an existing SDK v1 server.

Part 1 calls the SDK v2 server as a 2026-07-28 client and as a 2025-11-25 client, on the same
stateless runtime, and shows what server/discover reports. The 2026-07-28 client's elicitation runs as
a multi round-trip request with each round on a new runtime session.
Part 2 calls the unchanged SDK v1 server with the SDK v2 client in its default mode="auto", which
falls back to the initialize handshake, and then pinned to 2026-07-28, which fails.

For what an MCP 2025-11-25 client sees, run invoke_earlier.py (it needs SDK v1, in its own
virtual environment).

Usage: python invoke.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "helpers"))
from deploy_utils import load_config
from mcp_client import EARLIER, LATEST, McpClient, new_session_id, runtime_url, sdk_http_client, show


def first_error(error: BaseException) -> BaseException:
    """The SDK raises connection failures inside an ExceptionGroup; return the first leaf."""
    while isinstance(error, BaseExceptionGroup):
        error = error.exceptions[0]
    return error


def add_expense_rounds(arn: str, region: str) -> None:
    """add_expense as a 2026-07-28 client: two rounds, each on a new runtime session, so a new microVM."""
    capabilities = {"elicitation": {"form": {}}}
    arguments = {"amount": 12.5, "category": "food"}
    first = McpClient(arn, region, new_session_id(), capabilities).call_tool("add_expense", arguments)
    if first.result.get("resultType") != "input_required":
        show(f"{LATEST} add_expense round 1 (expected input_required)", first)
        return
    asked = {key: request["params"]["message"] for key, request in first.result["inputRequests"].items()}
    print(f"── {LATEST} add_expense round 1  input_required  {list(asked.values())}")
    answers = {key: {"action": "accept", "content": {"confirm": True}} for key in asked}
    retry = McpClient(arn, region, new_session_id(), capabilities).call_tool(
        "add_expense", arguments, inputResponses=answers, requestState=first.result["requestState"]
    )
    show(f"{LATEST} add_expense round 2, another session", retry)


async def against_v1_server(arn: str, region: str, mode: str) -> dict[str, Any]:
    """Call the SDK v1 server with an SDK v2 client. mode="auto" falls back; "2026-07-28" doesn't."""
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    statuses: list[str] = []

    async def record(response: Any) -> None:
        statuses.append(str(response.status_code))

    outcome: dict[str, Any] = {"mode": mode}
    http = sdk_http_client(region, new_session_id())
    http.event_hooks = {"response": [record]}
    try:
        transport = streamable_http_client(runtime_url(arn, region), http_client=http, terminate_on_close=False)
        async with http, Client(transport, mode=mode) as client:
            outcome["protocol_version"] = client.protocol_version
            result = await client.call_tool("get_total", {})
            outcome["get_total"] = result.structured_content
    except BaseException as error:  # noqa: BLE001 - the failure is the point of mode="2026-07-28"
        leaf = first_error(error)
        outcome["error"] = f"{type(leaf).__name__}: {str(leaf)[:120]}"
    outcome["http"] = statuses
    return outcome


if __name__ == "__main__":
    config = load_config(HERE)
    region = config["region"]
    stateless_arn = config["stateless"]["runtime_arn"]
    v1_arn = config["v1"]["runtime_arn"]

    print("═══ Part 1: one SDK v2 server, both protocol versions ═══")
    latest = McpClient(stateless_arn, region, session_id=new_session_id())
    show(f"{LATEST} tools/call whoami", latest.call_tool("whoami"), ("protocol_version", "legacy_mode"))
    show(f"{LATEST} tools/call get_total", latest.call_tool("get_total"))
    add_expense_rounds(stateless_arn, region)

    earlier = McpClient(stateless_arn, region, session_id=new_session_id())
    init = earlier.initialize()
    print(f"── {EARLIER} initialize  protocolVersion={init.result.get('protocolVersion')}")
    show(
        f"{EARLIER} tools/call whoami",
        earlier.call_tool("whoami", version=EARLIER),
        ("protocol_version", "legacy_mode"),
    )

    discover = latest.request("server/discover")
    print(f"── server/discover  supportedVersions={discover.result.get('supportedVersions')}")
    print(f"   The server answers {EARLIER} too, as the initialize above shows, but doesn't advertise it.")

    print("\n═══ Part 2: an unchanged MCP Python SDK v1 server ═══")
    for mode in ("auto", LATEST):
        outcome = asyncio.run(against_v1_server(v1_arn, region, mode))
        print(f"── SDK v2 client, mode={mode!r}")
        print(f"   {json.dumps({k: v for k, v in outcome.items() if k != 'mode'})[:300]}")
    print(f"   mode='auto' tries {LATEST}, gets HTTP 400, and falls back to initialize.")
    print(f"   A client pinned to {LATEST} fails: upgrade servers before pinning clients.")
