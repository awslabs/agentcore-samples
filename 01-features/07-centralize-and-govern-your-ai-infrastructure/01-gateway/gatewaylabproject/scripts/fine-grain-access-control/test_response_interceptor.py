"""Offline unit tests for the FGAC RESPONSE interceptor filtering logic.

Extracts the inline RESPONSE-interceptor Lambda from the CloudFormation
template and exercises its ``lambda_handler`` against synthetic gateway
events — no AWS, no live gateway, no network. Verifies that scope-based
filtering works across every response shape the gateway can emit:

  1. ``result.tools``                        (plain tools/list)
  2. ``result.structuredContent.tools``      (tools/list + search)
  3. ``result.content[*].text`` JSON payload (semantic search)

Run:
    uv run python scripts/fine-grain-access-control/test_response_interceptor.py
    # or: python3 scripts/fine-grain-access-control/test_response_interceptor.py
"""

import base64
import importlib.util
import json
import os
import sys
import tempfile

REPO_YAML = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "cloudformation",
    "fine-grain-access-control",
    "fgac-interceptors-stack.yaml",
)


def _extract_zipfile_blocks(path):
    """Pull each ``ZipFile: |`` literal block out of the CFN template by
    indentation (avoids a hard PyYAML dependency)."""
    with open(path) as f:
        lines = f.read().splitlines()
    blocks, i = [], 0
    while i < len(lines):
        if lines[i].strip() == "ZipFile: |":
            code_indent = (len(lines[i]) - len(lines[i].lstrip())) + 2
            i += 1
            body = []
            while i < len(lines):
                ln = lines[i]
                if ln.strip() == "":
                    body.append("")
                    i += 1
                    continue
                if (len(ln) - len(ln.lstrip())) < code_indent:
                    break
                body.append(ln[code_indent:])
                i += 1
            blocks.append("\n".join(body))
        else:
            i += 1
    return blocks


def _load_response_interceptor():
    blocks = _extract_zipfile_blocks(REPO_YAML)
    assert len(blocks) == 2, f"expected 2 inline Lambdas, found {len(blocks)}"
    os.environ["GATEWAY_TARGET_NAME"] = "fgac-mcp-target"
    # Write the interceptor Lambda source (extracted from our own CloudFormation
    # template) to a temp module and import it, so we can unit-test its handler
    # offline (no AWS). importlib avoids exec/eval entirely.
    with tempfile.NamedTemporaryFile(
        "w", suffix="_response_interceptor.py", delete=False
    ) as f:
        f.write(blocks[1])
        module_path = f.name
    spec = importlib.util.spec_from_file_location(
        "fgac_response_interceptor", module_path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_jwt(scope):
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(json.dumps({"scope": scope}).encode())
        .decode()
        .rstrip("=")
    )
    return f"{header}.{payload}.sig"


TOOLS = [
    {"name": "fgac-mcp-target___getOrder", "description": "get"},
    {"name": "fgac-mcp-target___updateOrder", "description": "upd"},
    {"name": "fgac-mcp-target___cancelOrder", "description": "cxl"},
    {"name": "fgac-mcp-target___deleteOrder", "description": "del"},
]

LIMITED = _make_jwt("fgac/fgac-mcp-target:getOrder")
FULL = _make_jwt("fgac/fgac-mcp-target")


def _event(result_body, jwt):
    return {
        "mcp": {
            "gatewayRequest": {"headers": {"Authorization": f"Bearer {jwt}"}},
            "gatewayResponse": {
                "headers": {},
                "body": {"jsonrpc": "2.0", "id": 1, "result": result_body},
            },
        }
    }


def _out(resp):
    return resp["mcp"]["transformedGatewayResponse"]["body"]["result"]


def _short(names):
    return sorted(n.split("___")[-1] for n in names)


def main():
    mod = _load_response_interceptor()
    handler = mod.lambda_handler
    passed = failed = 0

    def check(label, got, expect):
        nonlocal passed, failed
        ok = sorted(got) == sorted(expect)
        print(
            ("  PASS " if ok else "  FAIL ")
            + label
            + f" -> {got}"
            + ("" if ok else f"  (expected {expect})")
        )
        passed += int(ok)
        failed += int(not ok)

    # Shape 1: result.tools, limited scope -> only getOrder
    r = handler(_event({"tools": [dict(t) for t in TOOLS]}, LIMITED), None)
    check(
        "result.tools (limited)",
        _short(t["name"] for t in _out(r)["tools"]),
        ["getOrder"],
    )

    # Shape 2: structuredContent.tools, limited scope -> only getOrder
    r = handler(
        _event({"structuredContent": {"tools": [dict(t) for t in TOOLS]}}, LIMITED),
        None,
    )
    check(
        "structuredContent.tools (limited)",
        _short(t["name"] for t in _out(r)["structuredContent"]["tools"]),
        ["getOrder"],
    )

    # Shape 3: semantic-search content[0].text JSON, limited scope -> only getOrder
    search_payload = json.dumps({"tools": [dict(t) for t in TOOLS]})
    r = handler(
        _event({"content": [{"type": "text", "text": search_payload}]}, LIMITED), None
    )
    parsed = json.loads(_out(r)["content"][0]["text"])
    check(
        "search content.text (limited)",
        _short(t["name"] for t in parsed["tools"]),
        ["getOrder"],
    )

    # Shape 3 with full scope -> all four survive (no over-filtering)
    r = handler(
        _event({"content": [{"type": "text", "text": search_payload}]}, FULL), None
    )
    parsed = json.loads(_out(r)["content"][0]["text"])
    check(
        "search content.text (full)",
        _short(t["name"] for t in parsed["tools"]),
        ["getOrder", "updateOrder", "cancelOrder", "deleteOrder"],
    )

    # Non-JSON text content is left untouched
    r = handler(
        _event(
            {"content": [{"type": "text", "text": "human readable, not json"}]}, LIMITED
        ),
        None,
    )
    check(
        "non-JSON text untouched",
        [_out(r)["content"][0]["text"]],
        ["human readable, not json"],
    )

    # Missing token fails safe (no crash, no filtering exception surfaced)
    handler(
        {
            "mcp": {
                "gatewayRequest": {"headers": {}},
                "gatewayResponse": {"headers": {}, "body": {"result": {"tools": []}}},
            }
        },
        None,
    )

    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
