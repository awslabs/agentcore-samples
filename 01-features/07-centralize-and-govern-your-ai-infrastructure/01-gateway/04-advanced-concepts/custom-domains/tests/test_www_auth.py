# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "www_auth_index",
    Path(__file__).resolve().parents[1] / "lambda" / "www_auth" / "index.py",
)
www_auth = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(www_auth)

RM = "https://mcp.example.com/.well-known/oauth-protected-resource/sales/mcp"


def test_rewrite_replaces_existing_resource_metadata():
    hv = 'Bearer resource_metadata="https://gw.example.com/.well-known/oauth-protected-resource"'
    assert (
        www_auth.rewrite_www_authenticate(hv, RM) == f'Bearer resource_metadata="{RM}"'
    )


def test_rewrite_preserves_other_params():
    hv = 'Bearer realm="x", resource_metadata="https://old", scope="read"'
    out = www_auth.rewrite_www_authenticate(hv, RM)
    assert 'realm="x"' in out and 'scope="read"' in out
    assert f'resource_metadata="{RM}"' in out


def test_rewrite_appends_when_absent():
    hv = 'Bearer realm="x"'
    out = www_auth.rewrite_www_authenticate(hv, RM)
    assert out == f'Bearer realm="x", resource_metadata="{RM}"'


def test_rewrite_from_empty():
    assert (
        www_auth.rewrite_www_authenticate("", RM) == f'Bearer resource_metadata="{RM}"'
    )


def _event(status, headers=None, req_headers=None):
    return {
        "Records": [
            {
                "cf": {
                    "request": {"headers": req_headers or {}},
                    "response": {"status": status, "headers": headers or {}},
                }
            }
        ]
    }


def test_handler_ignores_non_401():
    resp = www_auth.handler(_event("200"), None)
    assert "www-authenticate" not in resp.get("headers", {})


def test_handler_noop_when_header_missing():
    resp = www_auth.handler(
        _event("401", {"www-authenticate": [{"value": "Bearer"}]}), None
    )
    # No resource-metadata request header → leave response untouched.
    assert resp["headers"]["www-authenticate"][0]["value"] == "Bearer"


def test_handler_rewrites_on_401():
    resp = www_auth.handler(
        _event(
            "401",
            headers={
                "www-authenticate": [
                    {"value": 'Bearer resource_metadata="https://old"'}
                ]
            },
            req_headers={"x-agwcd-resource-metadata": [{"value": RM}]},
        ),
        None,
    )
    assert (
        resp["headers"]["www-authenticate"][0]["value"]
        == f'Bearer resource_metadata="{RM}"'
    )


def test_handler_sets_header_when_absent():
    resp = www_auth.handler(
        _event(
            "401",
            headers={},
            req_headers={"x-agwcd-resource-metadata": [{"value": RM}]},
        ),
        None,
    )
    entry = resp["headers"]["www-authenticate"][0]
    assert entry["key"] == "WWW-Authenticate"
    assert entry["value"] == f'Bearer resource_metadata="{RM}"'
