# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Local MCP client tester for the sample AgentCore Gateway.

Validates the machine-to-machine path end to end:
  1. read the deployment state (deploy/state/outputs.env),
  2. fetch the confidential M2M client's secret from Cognito,
  3. mint a client_credentials access token from the hosted-UI /oauth2/token
     endpoint with the sample-mcp/invoke scope,
  4. open an MCP streamable-HTTP session to the Gateway with that Bearer token,
  5. list the tools the Gateway exposes,
  6. call each tool with a minimal valid input and print the result.

This is exactly the flow a real agent (Kiro, Amazon Quick, a custom client) uses
to reach a registry-published MCP server via its OAuth2 credential provider.

Secrets (client secret, access token) are used by handle only -- never printed.

Setup (one time):
    python3 -m venv deploy/.venv
    deploy/.venv/bin/pip install -r deploy/requirements-test.txt

Run:
    deploy/.venv/bin/python deploy/mcp_client_tester.py
Optional: --tool <name> --arg key=value ...   to drive one tool with custom args.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

STATE = Path(__file__).resolve().parent / "state" / "outputs.env"


def load_state(path: Path) -> dict:
    """Parse the KEY=VALUE state file (ignore comments / blanks)."""
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def aws(*args: str) -> str:
    """Run an AWS CLI call and return stdout (raises on non-zero)."""
    res = subprocess.run(["aws", *args], capture_output=True, text=True, check=True)
    return res.stdout.strip()


def get_client_secret(pool_id: str, client_id: str, region: str) -> str:
    """Fetch the confidential app client's generated secret. Returned by value
    into memory only -- the caller never prints it."""
    out = aws(
        "cognito-idp",
        "describe-user-pool-client",
        "--user-pool-id",
        pool_id,
        "--client-id",
        client_id,
        "--region",
        region,
        "--query",
        "UserPoolClient.ClientSecret",
        "--output",
        "text",
    )
    if not out or out == "None":
        raise SystemExit("no client secret on the M2M app client -- was it created with --generate-secret?")
    return out


def mint_token(domain: str, region: str, client_id: str, client_secret: str, scope: str) -> str:
    """client_credentials grant against the Cognito hosted-UI token endpoint."""
    token_url = f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token"
    resp = httpx.post(
        token_url,
        data={"grant_type": "client_credentials", "scope": scope},
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise SystemExit(f"token endpoint returned {resp.status_code}: {resp.text[:300]}")
    tok = resp.json().get("access_token")
    if not tok:
        raise SystemExit(f"no access_token in response: {resp.json()}")
    return tok


async def run(gateway_url: str, token: str, only_tool: str | None, extra_args: dict) -> int:
    headers = {"Authorization": f"Bearer {token}"}
    print(f"-> connecting to gateway MCP endpoint\n   {gateway_url}")
    async with (
        streamablehttp_client(gateway_url, headers=headers) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        print("OK  initialize succeeded (MCP session established)")

        listed = await session.list_tools()
        tools = listed.tools
        print(f"OK  tools/list returned {len(tools)} tool(s):")
        for t in tools:
            req = (t.inputSchema or {}).get("required", []) if t.inputSchema else []
            print(f"      - {t.name}  (required: {req or 'none'})")

        if not tools:
            print("!!  no tools exposed -- check the Gateway target configuration")
            return 1

        # Choose which tool(s) to call.
        targets = [t for t in tools if (only_tool in t.name)] if only_tool else tools
        if only_tool and not targets:
            print(f"!!  no tool matching '{only_tool}'")
            return 1

        rc = 0
        for t in targets:
            # Build a minimal valid input: caller args, else a sensible default.
            args = dict(extra_args)
            # describe_registry_record_types takes no required args
            if not args and t.name.endswith("echo"):
                args = {"message": "mcp-client-tester ping"}
            print(f"\n-> tools/call {t.name}  args={args}")
            try:
                result = await session.call_tool(t.name, args)
                payload = [c.text for c in result.content if getattr(c, "text", None)]
                is_err = getattr(result, "isError", False)
                tag = "ERROR" if is_err else "OK   "
                print(f"{tag} {t.name} ->")
                for p in payload:
                    try:
                        print("      " + json.dumps(json.loads(p), indent=2).replace("\n", "\n      "))
                    except ValueError:  # not JSON: print the raw text
                        print("      " + p)
                if is_err:
                    rc = 1
            except Exception as e:  # noqa: BLE001
                print(f"!!  {t.name} call raised: {e}")
                rc = 1
        return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate MCP tool access to the sample AgentCore Gateway.")
    ap.add_argument("--tool", help="only call tools whose name contains this substring")
    ap.add_argument("--arg", action="append", default=[], help="key=value tool argument (repeatable)")
    ns = ap.parse_args()
    extra = {}
    for kv in ns.arg:
        if "=" in kv:
            k, v = kv.split("=", 1)
            extra[k] = v

    if not STATE.exists():
        raise SystemExit(f"state file not found: {STATE} -- deploy first (99-all.sh up)")
    st = load_state(STATE)
    region = st.get("REGION", "us-east-1")
    pool_id = st["USER_POOL_ID"]
    m2m_client = st["SAMPLE_M2M_CLIENT_ID"]
    domain = st["SAMPLE_COGNITO_DOMAIN"]
    gateway_url = st["SAMPLE_GW_URL"]
    scope = "sample-mcp/invoke"

    print(f"state: pool={pool_id} m2m_client={m2m_client} domain={domain}")
    print("-> fetching M2M client secret from Cognito (by handle, not printed)")
    secret = get_client_secret(pool_id, m2m_client, region)
    print("-> minting client_credentials token (scope: " + scope + ")")
    token = mint_token(domain, region, m2m_client, secret, scope)
    del secret
    print(f"OK  token minted (len={len(token)}, value withheld)")

    rc = asyncio.run(run(gateway_url, token, ns.tool, extra))
    print("\n" + ("PASS: MCP tool access validated." if rc == 0 else "FAIL: see errors above."))
    return rc


if __name__ == "__main__":
    sys.exit(main())
