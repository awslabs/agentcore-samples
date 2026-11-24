"""Protocol basics on AgentCore Runtime with MCP 2026-07-28, over HTTPS with a Cognito bearer token.

Part 1 calls the server with no initialize handshake, then as an MCP 2025-11-25 client.
Part 2 shows that the runtime session header decides which microVM answers.
Part 3 sends the same failures as a 2026-07-28 request and as an earlier client, and prints the
HTTP status and JSON-RPC code of each.

Usage: python invoke.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "helpers"))
from deploy_utils import load_config
from mcp_client import EARLIER, LATEST, SESSION_HEADER, HttpMcpClient, Reply, SigV4, cognito_token, new_session_id


def header(reply: Reply, name: str) -> str | None:
    return next((v for k, v in reply.headers.items() if k.lower() == name.lower()), None)


def outcome(reply: Reply) -> str:
    """HTTP status plus the JSON-RPC error code, isError, or a note that the body isn't JSON-RPC."""
    if reply.error:
        text = f"{reply.status}, {reply.error['code']}"
    elif reply.result.get("isError"):
        text = f"{reply.status}, isError"
    elif "jsonrpc" not in reply.message:  # for example AgentCore Runtime's own {"message": ...} body
        text = f"{reply.status}, not JSON-RPC"
    else:
        text = f"{reply.status}, ok"
    if header(reply, "WWW-Authenticate"):
        text += " + WWW-Authenticate"
    return text


if __name__ == "__main__":
    config = load_config(HERE)
    arn, region, cognito = config["runtime_arn"], config["region"], config["cognito"]
    token = cognito_token(region, cognito["client_id"], cognito["username"], cognito["password"])

    def client(session_id: str | None = None, **kwargs) -> HttpMcpClient:
        return HttpMcpClient(arn, region, kwargs.pop("token", token), session_id=session_id, **kwargs)

    print("═══ Part 1: no initialize handshake ═══")
    tools = client(new_session_id()).request("tools/list")
    print(f"── {LATEST} tools/list, first request  {outcome(tools)}  {[t['name'] for t in tools.result['tools']]}")
    earlier = client(new_session_id())
    init = earlier.initialize()
    print(f"── {EARLIER} initialize  {outcome(init)}  protocolVersion={init.result.get('protocolVersion')}")
    who = earlier.call_tool("whoami", version=EARLIER)
    print(f"── {EARLIER} tools/call whoami  {outcome(who)}  protocol_version={who.structured['protocol_version']}")

    print("\n═══ Part 2: the runtime session header picks the microVM ═══")
    s1, s2 = new_session_id(), new_session_id()
    for label, session_id in [("S1", s1), ("S1 again", s1), ("S2", s2), ("no header", None), ("no header", None)]:
        reply = client(session_id).call_tool("whoami")
        print(
            f"── {label:<10} instance {reply.structured['instance_id']}  "
            f"server saw {SESSION_HEADER}={(reply.structured['runtime_session_id'] or '')[:20]}…  "
            f"response Mcp-Session-Id={(header(reply, 'Mcp-Session-Id') or '')[:20]}…"
        )

    print("\n═══ Part 3: errors keep their HTTP status in 2026-07-28 ═══")
    missing_arn = arn[:-10] + "0000000000"  # same account and Region, no such runtime
    cases = [
        ("Unknown method", lambda c, v: c.request("finance/archive", version=v)),
        # An earlier client doesn't send Mcp-Name; HttpMcpClient adds it so the middleware sees the call.
        ("Server answers HTTP 500", lambda c, v: c.call_tool("simulate_server_fault", version=v)),
        ("Tool raises ToolError", lambda c, v: c.call_tool("get_budget", {"category": "entertainment"}, version=v)),
        (
            "Mcp-Method doesn't match the body",
            lambda c, v: c.request("tools/list", version=v, headers={"Mcp-Method": "tools/call"}),
        ),
        ("No bearer token", lambda c, v: client(c.session_id, token=None).request("tools/list", version=v)),
        (
            "SigV4 instead of a token",
            lambda c, v: client(c.session_id, token=None, auth=SigV4(region)).request("tools/list", version=v),
        ),
        (
            "Runtime ARN that doesn't exist",
            lambda c, v: HttpMcpClient(missing_arn, region, token).request("tools/list", version=v),
        ),
    ]
    print(f"   {'Case':<36} {LATEST:<30} {EARLIER} (earlier client)")
    for label, send in cases:
        latest = outcome(send(client(new_session_id()), LATEST))
        if label.startswith("Mcp-Method"):
            earlier_outcome = "—"  # the standard headers are new in 2026-07-28
        else:
            earlier_outcome = outcome(send(client(new_session_id()), EARLIER))
        print(f"   {label:<36} {latest:<30} {earlier_outcome}")
