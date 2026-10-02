# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "discovery_index",
    Path(__file__).resolve().parents[1] / "lambda" / "discovery" / "index.py",
)
discovery = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(discovery)


def test_apply_override_prm_only_touches_resource():
    doc = {
        "resource": "https://gw.example.com/mcp",
        "authorization_servers": ["https://auth.example.com"],
        "scopes_supported": ["read"],
    }
    out = discovery.apply_override(
        "prm", dict(doc), {"resource": "https://mcp.example.com/sales/mcp"}
    )
    assert out["resource"] == "https://mcp.example.com/sales/mcp"
    # Everything else preserved.
    assert out["authorization_servers"] == doc["authorization_servers"]
    assert out["scopes_supported"] == doc["scopes_supported"]


def test_apply_override_agent_card_url_and_interfaces():
    doc = {
        "name": "Planner",
        "url": "https://gw.example.com/planner",
        "additionalInterfaces": [
            {"transport": "JSONRPC", "url": "https://gw.example.com/planner"},
            {"transport": "GRPC", "url": "https://gw.example.com/planner/grpc"},
            {"transport": "OTHER", "url": "https://elsewhere.example.com/x"},
        ],
        "skills": [{"id": "plan"}],
    }
    out = discovery.apply_override(
        "agent_card",
        dict(doc),
        {
            "url": "https://mcp.example.com/sales/planner",
            "gateway_base": "https://gw.example.com/planner",
            "agent_base": "https://mcp.example.com/sales/planner",
        },
    )
    assert out["url"] == "https://mcp.example.com/sales/planner"
    ifaces = out["additionalInterfaces"]
    assert ifaces[0]["url"] == "https://mcp.example.com/sales/planner"
    assert ifaces[1]["url"] == "https://mcp.example.com/sales/planner/grpc"
    # Unrelated interface untouched.
    assert ifaces[2]["url"] == "https://elsewhere.example.com/x"
    # Non-url fields preserved.
    assert out["name"] == "Planner"
    assert out["skills"] == doc["skills"]


def test_apply_override_unknown_kind():
    with pytest.raises(ValueError):
        discovery.apply_override("bogus", {}, {})


def test_origin_verify_value_lookup(monkeypatch):
    # Map now holds ARNs; the value is resolved via _secret_value. Stub the
    # fetch (identity) so the test stays offline.
    monkeypatch.setattr(discovery, "_secret_value", lambda arn: arn.split("/")[-1])
    monkeypatch.setattr(
        discovery,
        "ORIGIN_VERIFY_MAP",
        {
            "https://gwa.example.com": "arn:aws:secretsmanager:us-east-1:1:secret/secretA",
            "https://gwb.example.com": "arn:aws:secretsmanager:us-east-1:1:secret/secretB",
        },
    )
    assert (
        discovery._origin_verify_value_for(
            "https://gwa.example.com/.well-known/oauth-protected-resource"
        )
        == "secretA"
    )
    assert discovery._origin_verify_value_for("https://gwc.example.com/x") is None


def test_origin_verify_value_lookup_requires_path_boundary(monkeypatch):
    # A bare-prefix collision (gwa.example.com vs gwa.example.com.evil.com) must
    # NOT match — only exact or a real "/" path boundary counts.
    monkeypatch.setattr(discovery, "_secret_value", lambda arn: "secretA")
    monkeypatch.setattr(
        discovery, "ORIGIN_VERIFY_MAP", {"https://gwa.example.com": "arnA"}
    )
    assert discovery._origin_verify_value_for("https://gwa.example.com") == "secretA"
    assert discovery._origin_verify_value_for("https://gwa.example.com/x") == "secretA"
    assert (
        discovery._origin_verify_value_for("https://gwa.example.com.evil.com/x") is None
    )


def test_fetch_downstream_rejects_oversized_body(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return b"x" * (discovery.MAX_DOWNSTREAM_BYTES + 1)

    monkeypatch.setattr(
        discovery.urllib.request, "urlopen", lambda req, timeout=None: _Resp()
    )
    monkeypatch.setattr(discovery, "ORIGIN_VERIFY_MAP", {})
    with pytest.raises(ValueError):
        discovery._fetch_downstream("https://gw/x")


def test_match_route_strips_query(monkeypatch):
    monkeypatch.setattr(
        discovery, "_ROUTES_BY_PATH", {"/a/mcp": {"path": "/a/mcp", "kind": "prm"}}
    )
    assert discovery.match_route("/a/mcp?x=1")["kind"] == "prm"
    assert discovery.match_route("/missing") is None


def test_handler_404_for_unknown(monkeypatch):
    monkeypatch.setattr(discovery, "_ROUTES_BY_PATH", {})
    resp = discovery.handler({"rawPath": "/nope"}, None)
    assert resp["statusCode"] == 404


def test_handler_502_on_fetch_failure(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "_ROUTES_BY_PATH",
        {
            "/a/mcp": {
                "path": "/a/mcp",
                "kind": "prm",
                "downstream_url": "https://gw/x",
                "overrides": {"resource": "r"},
            }
        },
    )

    def boom(url, auth=None):
        raise RuntimeError("down")

    monkeypatch.setattr(discovery, "_fetch_downstream", boom)
    resp = discovery.handler({"rawPath": "/a/mcp"}, None)
    assert resp["statusCode"] == 502


def test_handler_success(monkeypatch):
    import json

    monkeypatch.setattr(
        discovery,
        "_ROUTES_BY_PATH",
        {
            "/a/mcp": {
                "path": "/a/mcp",
                "kind": "prm",
                "downstream_url": "https://gw/x",
                "overrides": {"resource": "https://mcp.example.com/a/mcp"},
            }
        },
    )
    monkeypatch.setattr(
        discovery,
        "_fetch_downstream",
        lambda url, auth=None: {
            "resource": "https://gw/mcp",
            "authorization_servers": ["a"],
        },
    )
    resp = discovery.handler({"rawPath": "/a/mcp"}, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["resource"] == "https://mcp.example.com/a/mcp"
    assert body["authorization_servers"] == ["a"]
    assert resp["headers"]["cache-control"] == "no-store"


def test_fetch_downstream_forwards_authorization(monkeypatch):
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return b"{}"

    def fake_urlopen(req, timeout=None):
        captured["auth"] = req.get_header("Authorization")
        return _Resp()

    monkeypatch.setattr(discovery.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(discovery, "ORIGIN_VERIFY_MAP", {})

    discovery._fetch_downstream("https://gw/x")
    assert captured["auth"] is None
    discovery._fetch_downstream("https://gw/x", "Bearer tok123")
    assert captured["auth"] == "Bearer tok123"


def test_handler_forwards_side_header_token(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "_ROUTES_BY_PATH",
        {
            "/a/card": {
                "path": "/a/card",
                "kind": "agent_card",
                "downstream_url": "https://gw/planner/.well-known/agent-card.json",
                "overrides": {"url": "https://mcp.example.com/a/planner"},
            }
        },
    )
    seen = {}

    def fake_fetch(url, auth=None):
        seen["auth"] = auth
        return {"url": "https://gw/planner"}

    monkeypatch.setattr(discovery, "_fetch_downstream", fake_fetch)
    resp = discovery.handler(
        {"rawPath": "/a/card", "headers": {"x-agwcd-authorization": "Bearer tok"}}, None
    )
    assert resp["statusCode"] == 200
    assert seen["auth"] == "Bearer tok"


def _http_error(code):
    import io

    return discovery.urllib.error.HTTPError(
        "https://gw/x",
        code,
        "err",
        {"WWW-Authenticate": 'Bearer realm="gw"'},
        io.BytesIO(b""),
    )


def test_handler_401_emits_custom_domain_challenge(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "_ROUTES_BY_PATH",
        {
            "/a/card": {
                "path": "/a/card",
                "kind": "agent_card",
                "downstream_url": "https://gw/x",
                "overrides": {"url": "u"},
                "resource_metadata": (
                    "https://mcp.example.com/.well-known/oauth-protected-resource/a/planner"
                ),
            }
        },
    )

    def raise401(url, auth=None):
        raise _http_error(401)

    monkeypatch.setattr(discovery, "_fetch_downstream", raise401)
    resp = discovery.handler({"rawPath": "/a/card"}, None)
    assert resp["statusCode"] == 401
    www = resp["headers"]["www-authenticate"]
    assert 'resource_metadata="https://mcp.example.com/' in www
    # Never leaks the raw gateway challenge when a custom PRM is known.
    assert "gw" not in www


def test_handler_401_falls_back_to_downstream_challenge(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "_ROUTES_BY_PATH",
        {
            "/a/mcp": {
                "path": "/a/mcp",
                "kind": "prm",
                "downstream_url": "https://gw/x",
                "overrides": {"resource": "r"},
            }
        },
    )

    def raise403(url, auth=None):
        raise _http_error(403)

    monkeypatch.setattr(discovery, "_fetch_downstream", raise403)
    resp = discovery.handler({"rawPath": "/a/mcp"}, None)
    assert resp["statusCode"] == 403
    assert resp["headers"]["www-authenticate"] == 'Bearer realm="gw"'
