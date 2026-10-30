#!/usr/bin/env python3
"""
selftest.py — offline checks for confirm-web-search.py.

Runs entirely locally: no AWS calls, no network, no cost. Use it to confirm the
demo script is sound before you walk into a customer meeting, and after editing
it.

    python3 scripts/selftest.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cws", HERE / "confirm-web-search.py")
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

import boto3  # imported after the module under test loads

FAILURES: list[str] = []


def check(label: str, got, want=True) -> None:
    if got == want:
        print(f"  ok   {label}")
    else:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL {label}: got {got!r}, want {want!r}")


def section(name: str) -> None:
    print(f"\n{name}")


# ---------------------------------------------------------------------------
section("URL normalization")
# ---------------------------------------------------------------------------
check("already /mcp", m.normalize_mcp_url("https://gw.example.com/mcp"), "https://gw.example.com/mcp")
check("appends /mcp", m.normalize_mcp_url("https://gw.example.com"), "https://gw.example.com/mcp")
check("strips trailing slash", m.normalize_mcp_url("https://gw.example.com/mcp/"), "https://gw.example.com/mcp")
check("bare host + slash", m.normalize_mcp_url("https://gw.example.com/"), "https://gw.example.com/mcp")

# ---------------------------------------------------------------------------
section("Region inference from Gateway hostname")
# ---------------------------------------------------------------------------
check(
    "eu-west-1",
    m.region_from_url("https://abc.gateway.bedrock-agentcore.eu-west-1.amazonaws.com/mcp"),
    "eu-west-1",
)
check(
    "us-east-1",
    m.region_from_url("https://abc.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"),
    "us-east-1",
)
check("unknown host -> None", m.region_from_url("https://example.com/mcp"), None)

# ---------------------------------------------------------------------------
section("Response body parsing")
# ---------------------------------------------------------------------------
check(
    "plain JSON",
    m._parse_body('{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}')["result"],
    {"tools": []},
)
sse = (
    'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"a":1}}\n\n'
    'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"a":2}}\n\n'
)
check("SSE uses last frame", m._parse_body(sse)["result"], {"a": 2})

for label, payload in [("empty body", "   "), ("non-JSON non-SSE", "<html>nope</html>")]:
    try:
        m._parse_body(payload)
        check(f"{label} raises", False)
    except m.McpError:
        check(f"{label} raises McpError", True)

# ---------------------------------------------------------------------------
section("Result extraction (content[0].text is a JSON-encoded string)")
# ---------------------------------------------------------------------------
inner = {
    "id": "824f89d0",
    "results": [
        {"text": "Py 3.13 released", "publishedDate": "2024-10-07", "url": "https://e.com/p", "title": "Py 3.13"},
        {"text": "second", "url": "https://e.com/2", "title": "Second"},
    ],
}
res = {"isError": False, "content": [{"type": "text", "text": json.dumps(inner)}]}
got = m.extract_results(res)
check("count", len(got), 2)
check("title", got[0]["title"], "Py 3.13")
check("publishedDate", got[0]["publishedDate"], "2024-10-07")
check("no results key -> []", m.extract_results({"content": [{"type": "text", "text": '{"foo":1}'}]}), [])
check("non-JSON text -> []", m.extract_results({"content": [{"type": "text", "text": "not json"}]}), [])
check("no content -> []", m.extract_results({}), [])
check("image block -> []", m.extract_results({"content": [{"type": "image", "data": "x"}]}), [])

# ---------------------------------------------------------------------------
section("Tool error text extraction")
# ---------------------------------------------------------------------------
check(
    "governance refusal surfaced",
    "no overlap"
    in m.tool_error_text(
        {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": "DependencyFailedException - Runtime domainFilter.include has no overlap with admin domainFilter.include; request cannot be satisfied.",
                }
            ],
        }
    ),
)
check("falls back to JSON dump", m.tool_error_text({"isError": True}) != "", True)

# ---------------------------------------------------------------------------
section("Sparse-result rendering must not crash")
# ---------------------------------------------------------------------------
with contextlib.redirect_stdout(io.StringIO()):
    m.render([{"title": "T"}, {}, {"url": "https://u", "text": "x" * 400}])
check("render tolerates missing fields", True)

# ---------------------------------------------------------------------------
section("SigV4 signing (intercepted, no network)")
# ---------------------------------------------------------------------------
captured: dict = {}


class _FakeResp:
    def __init__(self, body: str):
        self._b = body.encode()

    def read(self) -> bytes:
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(req, timeout=None, context=None):
    captured["url"] = req.full_url
    captured["method"] = req.get_method()
    captured["headers"] = {k.lower(): v for k, v in req.header_items()}
    captured["body"] = req.data.decode()
    captured["verifying_tls"] = context is not None and context.verify_mode != 0
    return _FakeResp(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {
                            "name": "web-search___WebSearch",
                            "inputSchema": {"properties": {"query": {}, "maxResults": {}, "filters": {}}},
                        }
                    ]
                },
            }
        )
    )


m.urllib.request.urlopen = _fake_urlopen

# Obviously-dummy credentials. We deliberately avoid AWS's canonical example
# access-key string: it matches the access-key regex that secret scanners
# (detect-secrets, gitleaks) look for and causes needless CI noise, even though
# it is published and fake. SigV4 signing does not care about the key format.
sess = boto3.Session(
    aws_access_key_id="dummy-access-key-id-for-offline-test",
    aws_secret_access_key="dummy-secret-access-key-for-offline-test",
    aws_session_token="dummy-session-token",
    region_name="eu-west-1",
)
url = "https://abc.gateway.bedrock-agentcore.eu-west-1.amazonaws.com/mcp"
client = m.McpClient(url, "eu-west-1", sess, "2025-11-25")
result = client.call("tools/list")

h = captured["headers"]
auth = h.get("authorization", "")
check("POST", captured["method"], "POST")
check("URL preserved", captured["url"], url)
check("TLS verification on", captured["verifying_tls"])
check("SigV4 Authorization header", auth.startswith("AWS4-HMAC-SHA256"))
check("credential scope region+service", "/eu-west-1/bedrock-agentcore/aws4_request" in auth)
check("session token forwarded", h.get("x-amz-security-token"), "dummy-session-token")
check("x-amz-date set", "x-amz-date" in h)
check("content-type", h.get("content-type"), "application/json")
check("accept includes SSE", "text/event-stream" in h.get("accept", ""))
check("MCP-Protocol-Version", h.get("mcp-protocol-version"), "2025-11-25")
for signed in ("host", "content-type", "mcp-protocol-version"):
    check(f"{signed} in SignedHeaders", signed in auth)

body = json.loads(captured["body"])
check("jsonrpc 2.0", body["jsonrpc"], "2.0")
check("method", body["method"], "tools/list")
check("tools parsed", result["tools"][0]["name"], "web-search___WebSearch")

client.call("tools/call", {"name": "x", "arguments": {}})
check("request id increments", json.loads(captured["body"])["id"], 2)
check("params forwarded", json.loads(captured["body"])["params"]["name"], "x")

# ---------------------------------------------------------------------------
print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
print("ALL SELFTESTS PASSED")
