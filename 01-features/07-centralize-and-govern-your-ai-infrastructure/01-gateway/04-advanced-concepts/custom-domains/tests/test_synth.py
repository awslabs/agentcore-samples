# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
import json
import re

from agwcd.config import Config, Endpoint, Route
from custom_domains import synth


def _cfg():
    return Config(
        domain_name="mcp.example.com",
        geo_allowlist=["US"],
        routes=[
            Route(
                "/hr",
                "https://gwa.gateway.bedrock-agentcore.us-east-1.amazonaws.com",
                [Endpoint("mcp")],
            ),
            Route(
                "/sales",
                "https://gwb.gateway.bedrock-agentcore.us-east-1.amazonaws.com",
                [
                    Endpoint("mcp"),
                    Endpoint("http_mcp", "catalog"),
                    Endpoint("http_a2a", "planner"),
                ],
            ),
        ],
    )


def test_origin_verify_gateways_in_result():
    cfg = Config(
        domain_name="mcp.example.com",
        routes=[
            Route(
                "/a", "https://gwa.example.com", [Endpoint("mcp")], origin_verify=True
            ),
            Route(
                "/b", "https://gwb.example.com", [Endpoint("mcp")], origin_verify=False
            ),
        ],
    )
    result = synth.build(cfg)
    assert result.origin_verify_gateways == ["https://gwa.example.com"]


def test_gateway_urls_deduped_and_sorted():
    # Reuse gwa on two paths → still one origin.
    cfg = Config(
        domain_name="mcp.example.com",
        routes=[
            Route("/a", "https://gwa.example.com", [Endpoint("mcp")]),
            Route("/b", "https://gwa.example.com", [Endpoint("mcp")]),
        ],
    )
    result = synth.build(cfg)
    assert result.gateway_urls == ["https://gwa.example.com"]


def test_live_and_discovery_behaviors():
    result = synth.build(_cfg())
    live = {b.path_pattern for b in result.live_behaviors}
    disc = {d.path_pattern for d in result.discovery_behaviors}
    assert "/hr/mcp" in live and "/sales/mcp" in live
    assert "/sales/planner" in live
    assert "/.well-known/oauth-protected-resource/hr/mcp" in disc
    assert "/sales/planner/.well-known/agent-card.json" in disc


def test_www_auth_flag_on_mcp_and_a2a():
    result = synth.build(_cfg())
    by_pat = {b.path_pattern: b.needs_www_auth for b in result.live_behaviors}
    assert by_pat["/sales/mcp"] is True
    # A2A now advertises OAuth discovery too, so its live endpoint needs the
    # WWW-Authenticate rewrite.
    assert by_pat["/sales/planner"] is True


def test_routes_json_shape():
    result = synth.build(_cfg())
    routes = result.routes_json["routes"]
    kinds = {r["kind"] for r in routes}
    assert kinds == {"prm", "agent_card"}
    for r in routes:
        assert set(r) == {
            "path",
            "kind",
            "downstream_url",
            "overrides",
            "resource_metadata",
        }
    # The A2A agent card carries the custom-domain PRM for its 401 challenge.
    card = next(r for r in routes if r["kind"] == "agent_card")
    assert card["resource_metadata"] == (
        "https://mcp.example.com/.well-known/oauth-protected-resource/sales/planner"
    )


def test_discovery_auth_function_forwards_authorization():
    code = synth.build(_cfg()).discovery_auth_function_code
    # ES5.1-safe (cloudfront-js) — no ES6 constructs.
    assert "=>" not in code
    assert "let " not in code and "const " not in code
    # Copies the caller's Authorization into the side header and drops the
    # original (so it can't collide with OAC's SigV4 signature or be spoofed).
    assert synth.FORWARDED_AUTH_HEADER in code
    assert "h.authorization.value" in code
    assert "delete h.authorization" in code


def test_function_code_is_es5_safe_and_longest_prefix_first():
    result = synth.build(_cfg())
    code = result.function_code
    # No ES6 constructs.
    assert "=>" not in code
    assert "let " not in code and "const " not in code
    # Denies unmatched.
    assert "statusCode: 403" in code
    # Injects the resource-metadata header for mcp. CloudFront Functions require
    # a header assigned as a single object ``{ value: ... }`` — NOT an array
    # (an array triggers a runtime FunctionValidationError → 503).
    assert synth.RESOURCE_METADATA_HEADER in code
    assert (
        f'req.headers["{synth.RESOURCE_METADATA_HEADER}"] = {{ value: r.rm }};' in code
    )
    assert f'["{synth.RESOURCE_METADATA_HEADER}"] = [' not in code
    # Always strips any client-supplied resource-metadata header before the
    # loop, so a viewer can never forge the value the WWW-Authenticate rewrite
    # trusts (covers matched + 403 paths).
    assert f'delete req.headers["{synth.RESOURCE_METADATA_HEADER}"];' in code
    # The strip precedes the route table / injection.
    assert code.index("delete req.headers") < code.index("var routes =")
    # Table is embedded and ordered longest-prefix-first.
    table = json.loads(re.search(r"var routes = (\[.*?\]);", code).group(1))
    lengths = [len(r["p"]) for r in table]
    assert lengths == sorted(lengths, reverse=True)
    # Every live prefix present exactly once.
    prefixes = {r["p"] for r in table}
    assert {"/hr/mcp", "/sales/mcp", "/sales/catalog/mcp", "/sales/planner"} == prefixes


def test_function_strips_prefix_semantics():
    # A minimal manual check that origin_prefix replaces the viewer prefix.
    result = synth.build(_cfg())
    code = result.function_code
    assert "req.uri = r.o + uri.substring(r.p.length);" in code
