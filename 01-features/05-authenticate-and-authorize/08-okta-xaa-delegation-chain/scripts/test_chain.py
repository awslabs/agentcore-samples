"""Milestone test: prove hop D end to end, without the agent or the BFF.

    .venv/bin/python scripts/test_chain.py

Signs you in through the AI Agent's linked app, performs the same OBO exchange the agent
performs, then calls the gateway with exactly what the agent sends:

    Authorization: Bearer <T_gateway>    the gateway's CUSTOM_JWT + Cedar read this,
                                         and the interceptor exchanges it at leg 1

The OBO step is not optional. Leg 1 accepts an access token only if that token's `cid` is
registered as a **Machine access** caller on the AI Agent, and the caller registered there
is the Agent app -- the `cid` of T_gateway. Sending T_user instead fails leg 1 with
"no delegation policy authorizes this token", because an agent cannot be its own caller.

Doing the OBO exchange here has a second benefit: T_gateway already carries
`tools.access`, so the gateway no longer has to be widened with --allow-user-scope just to
run this test.

If this passes, everything from the gateway onwards works: the interceptor's two-leg
exchange, the credential injection, Cedar enforcement, and the resource API's
validation. Only the Runtime and the BFF remain.

Nothing is created and nothing is written to .env. Tokens are shown as claims only.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import boto3

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "deploy"))
sys.path.insert(0, str(HERE))
from _common import env, load_env, must_env, okta_org_url
from okta_signin import sign_in

MCP_VERSION = "2025-03-26"
ID_TOKEN_HEADER = "X-Okta-Id-Token"


def mcp(url: str, payload: dict, token: str, id_token: str, sid: str | None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_VERSION,
        "Authorization": f"Bearer {token}",
    }
    # Only when there is one. On the default access_token path the interceptor exchanges
    # the Authorization bearer, so sending this header would prove nothing -- and sending
    # it unconditionally would hide a regression where the chain quietly depends on it.
    if id_token:
        headers[ID_TOKEN_HEADER] = id_token
    if sid:
        headers["Mcp-Session-Id"] = sid
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=90)
        return resp.headers, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.headers, f"HTTP {exc.code}: {exc.read().decode()[:600]}"


def call_tool(url: str, token: str, id_token: str, sid: str, name: str, args: dict | None = None):
    _, raw = mcp(
        url,
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": name, "arguments": args or {}}},
        token,
        id_token,
        sid,
    )
    return raw


def show(label: str, raw: str) -> None:
    print(f"\n  --- {label} ---")
    try:
        body = json.loads(raw)
    except ValueError:
        print(f"    {raw[:400]}")
        return
    if "error" in body:
        print(f"    ✗ {json.dumps(body['error'])[:400]}")
        return
    content = (body.get("result") or {}).get("content") or []
    for item in content:
        text = item.get("text", "")
        try:
            print("    " + json.dumps(json.loads(text), indent=2).replace("\n", "\n    "))
        except ValueError:
            print(f"    {text[:400]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--no-id-token",
        action="store_true",
        help="Send only Authorization, proving leg 1 needs no ID token (access_token mode).",
    )
    ap.add_argument("--add", metavar="TITLE", help="Also add a todo with this title.")
    args = ap.parse_args()
    load_env()

    gw_url = must_env("GATEWAY_MCP_URL", "Run deploy/02_create_gateway.py first.")
    print(f"gateway: {gw_url}\n")

    tokens = sign_in(
        okta_org_url(),
        must_env("LOGIN_CLIENT_ID"),
        env("FRONTEND_REDIRECT_URI", "http://localhost:8000/auth/callback"),
        f"openid profile email {env('SCOPE_AGENT_ACCESS', 'agent.access')}",
        must_env("AGENTCORE_AS_ISSUER"),
    )
    t_user, t_id = tokens["access_token"], tokens["id_token"]
    if args.no_id_token:
        t_id = ""
        print("  • not sending X-Okta-Id-Token at all")

    # Exactly what agent.py does at hop C. AgentCore Identity holds the Agent app's
    # secret and performs the RFC 8693 exchange; we only ever see the result.
    idp = boto3.client("bedrock-agentcore", region_name=env("AWS_REGION", "us-west-2"))
    wat = idp.get_workload_access_token_for_jwt(
        workloadName=env("AGENT_WORKLOAD_NAME", "xaa-todo-agent"), userToken=t_user
    )["workloadAccessToken"]
    t_gateway = idp.get_resource_oauth2_token(
        workloadIdentityToken=wat,
        resourceCredentialProviderName=env("AGENT_OBO_PROVIDER_NAME", "xaa-agent-obo-provider"),
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        scopes=[env("SCOPE_TOOLS_ACCESS", "tools.access")],
        audiences=[must_env("AGENTCORE_AUDIENCE")],
        customParameters={"subject_token_type": "urn:ietf:params:oauth:token-type:access_token"},
    )["accessToken"]
    print("  ✓ OBO exchange -> T_gateway (scp=tools.access)")

    hdrs, raw = mcp(
        gw_url,
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test_chain", "version": "1"},
            },
        },
        t_gateway,
        t_id,
        None,
    )
    if raw.startswith("HTTP "):
        sys.exit(
            f"  ✗ initialize failed: {raw[:400]}\n"
            "    A 403 with insufficient_scope means the gateway's allowedScopes does\n"
            "    not include this token's scope. The gateway pins tools.access, which is\n"
            "    what the OBO exchange above produces -- check SCOPE_TOOLS_ACCESS and\n"
            "    re-run deploy/02_create_gateway.py. Note allowedClients is NOT used:\n"
            "    Okta's cid is not matched by it, which reports as insufficient_scope too."
        )
    sid = hdrs.get("Mcp-Session-Id") or hdrs.get("mcp-session-id")
    mcp(gw_url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, t_gateway, t_id, sid)
    print("  ✓ MCP session established")

    _, listing = mcp(gw_url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, t_gateway, t_id, sid)
    try:
        names = [t["name"] for t in json.loads(listing)["result"]["tools"]]
        print(f"  tools: {names}")
    except (ValueError, KeyError, TypeError):
        print(f"  tools/list: {listing[:300]}")

    show("todo___whoami", call_tool(gw_url, t_gateway, t_id, sid, "todo___whoami"))
    show("todo___list_todos", call_tool(gw_url, t_gateway, t_id, sid, "todo___list_todos"))
    if args.add:
        show("todo___add_todo", call_tool(gw_url, t_gateway, t_id, sid, "todo___add_todo", {"title": args.add}))

    print("\n================ WHAT THIS PROVED ================")
    print("  If whoami returned your email as `user` and the AI Agent as")
    print("  `acting_agent`, then the full hop-D chain works:")
    print("    interceptor exchanged the inbound Authorization bearer")
    print("    -> ID-JAG leg 1 at the org server")
    print("    -> leg 2 at the resource AS")
    print("    -> injected T_tool")
    print("    -> Cedar permitted the call")
    print("    -> the API validated iss/aud/scp and resolved your identity")
    print("\n  Interceptor detail:")
    print(
        f"    aws logs tail /aws/lambda/{env('RESOURCE_LAMBDA_NAME', 'xaa-todo-resource')}"
        " --region " + env("AWS_REGION", "us-west-2") + " --since 5m"
    )
    print(
        "    aws logs tail /aws/lambda/xaa-todo-idjag-interceptor --region "
        + env("AWS_REGION", "us-west-2")
        + " --since 5m"
    )


if __name__ == "__main__":
    main()
