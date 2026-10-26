# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
import pytest
from custom_domains.targets import get_target, target_types

DOMAIN = "mcp.example.com"
GW = "https://gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com"


def test_registry_has_expected_types():
    assert set(target_types()) == {
        "mcp",
        "http_mcp",
        "http_a2a",
        "inference",
        "http_agent",
    }


def test_get_target_unknown():
    with pytest.raises(ValueError):
        get_target("grpc")


def test_mcp_root():
    plan = get_target("mcp").plan(domain_name=DOMAIN, path="/", gateway_url=GW)
    assert plan.live_patterns == ["/mcp", "/mcp/*"]
    assert plan.function_entry.prefix == "/mcp"
    assert plan.function_entry.origin_prefix == "/mcp"
    assert plan.discovery_patterns == ["/.well-known/oauth-protected-resource/mcp"]
    assert plan.needs_www_auth is True
    dr = plan.discovery_routes[0]
    assert dr.kind == "prm"
    assert dr.overrides["resource"] == "https://mcp.example.com/mcp"
    assert dr.downstream_url == f"{GW}/.well-known/oauth-protected-resource"
    # resource_metadata header points at the path-inserted PRM URL.
    assert plan.function_entry.resource_metadata == (
        "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
    )


def test_mcp_path():
    plan = get_target("mcp").plan(domain_name=DOMAIN, path="/sales", gateway_url=GW)
    assert plan.live_patterns == ["/sales/mcp", "/sales/mcp/*"]
    assert plan.function_entry.origin_prefix == "/mcp"  # prefix stripped for origin
    assert plan.discovery_patterns == [
        "/.well-known/oauth-protected-resource/sales/mcp"
    ]
    assert plan.discovery_routes[0].overrides["resource"] == (
        "https://mcp.example.com/sales/mcp"
    )


def test_http_mcp():
    plan = get_target("http_mcp").plan(
        domain_name=DOMAIN, path="/sales", gateway_url=GW, target_name="catalog"
    )
    assert plan.live_patterns == ["/sales/catalog/mcp", "/sales/catalog/mcp/*"]
    assert plan.function_entry.origin_prefix == "/catalog/mcp"
    assert plan.discovery_patterns == [
        "/.well-known/oauth-protected-resource/sales/catalog/mcp"
    ]
    assert plan.discovery_routes[0].overrides["resource"] == (
        "https://mcp.example.com/sales/catalog/mcp"
    )


def test_http_mcp_requires_target_name():
    with pytest.raises(ValueError):
        get_target("http_mcp").plan(domain_name=DOMAIN, path="/x", gateway_url=GW)


def test_a2a():
    plan = get_target("http_a2a").plan(
        domain_name=DOMAIN, path="/sales", gateway_url=GW, target_name="planner"
    )
    assert plan.live_patterns == ["/sales/planner", "/sales/planner/*"]
    assert plan.function_entry.origin_prefix == "/planner"
    # A2A now advertises OAuth discovery like MCP: WWW-Authenticate rewrite on
    # the live endpoint pointing at the agent's custom-domain PRM.
    assert plan.needs_www_auth is True
    assert plan.function_entry.resource_metadata == (
        "https://mcp.example.com/.well-known/oauth-protected-resource/sales/planner"
    )
    assert plan.discovery_patterns == [
        "/.well-known/oauth-protected-resource/sales/planner",
        "/sales/planner/.well-known/agent-card.json",
    ]

    prm = next(d for d in plan.discovery_routes if d.kind == "prm")
    # PRM proxied from the gateway's single root PRM; resource = bare agent URL.
    assert prm.path == "/.well-known/oauth-protected-resource/sales/planner"
    assert prm.downstream_url == f"{GW}/.well-known/oauth-protected-resource"
    assert prm.overrides == {"resource": "https://mcp.example.com/sales/planner"}

    card = next(d for d in plan.discovery_routes if d.kind == "agent_card")
    assert card.path == "/sales/planner/.well-known/agent-card.json"
    assert card.overrides["url"] == "https://mcp.example.com/sales/planner"
    assert card.overrides["gateway_base"] == f"{GW}/planner"
    assert card.overrides["agent_base"] == "https://mcp.example.com/sales/planner"
    assert card.downstream_url == f"{GW}/planner/.well-known/agent-card.json"
    # On a downstream 401 the Lambda challenges toward the agent's custom PRM.
    assert card.resource_metadata == (
        "https://mcp.example.com/.well-known/oauth-protected-resource/sales/planner"
    )


def test_a2a_root_path():
    plan = get_target("http_a2a").plan(
        domain_name=DOMAIN, path="/", gateway_url=GW, target_name="planner"
    )
    assert plan.live_patterns == ["/planner", "/planner/*"]
    prm = next(d for d in plan.discovery_routes if d.kind == "prm")
    assert prm.path == "/.well-known/oauth-protected-resource/planner"
    assert prm.overrides == {"resource": "https://mcp.example.com/planner"}


def test_inference():
    """Gateway aggregated /inference endpoint — like mcp, no target_name, and
    the base behavior: live passthrough + a single PRM, no agent card."""
    plan = get_target("inference").plan(
        domain_name=DOMAIN, path="/sales", gateway_url=GW
    )
    assert plan.live_patterns == ["/sales/inference", "/sales/inference/*"]
    assert plan.function_entry.prefix == "/sales/inference"
    assert plan.function_entry.origin_prefix == "/inference"
    assert plan.needs_www_auth is True
    assert len(plan.discovery_routes) == 1
    prm = plan.discovery_routes[0]
    assert prm.kind == "prm"
    assert prm.overrides == {"resource": "https://mcp.example.com/sales/inference"}
    assert plan.discovery_patterns == [
        "/.well-known/oauth-protected-resource/sales/inference"
    ]


def test_inference_root():
    plan = get_target("inference").plan(domain_name=DOMAIN, path="/", gateway_url=GW)
    assert plan.live_patterns == ["/inference", "/inference/*"]
    assert plan.discovery_routes[0].overrides["resource"] == (
        "https://mcp.example.com/inference"
    )


def test_http_agent():
    """Agent-as-tool with no card — same path shape as a2a, but only a PRM
    (no agent_card discovery route)."""
    plan = get_target("http_agent").plan(
        domain_name=DOMAIN, path="/sales", gateway_url=GW, target_name="infer"
    )
    assert plan.live_patterns == ["/sales/infer", "/sales/infer/*"]
    assert plan.function_entry.origin_prefix == "/infer"
    assert plan.needs_www_auth is True
    # Exactly one discovery route — the PRM — and no agent card.
    assert len(plan.discovery_routes) == 1
    prm = plan.discovery_routes[0]
    assert prm.kind == "prm"
    assert prm.overrides == {"resource": "https://mcp.example.com/sales/infer"}
    assert plan.discovery_patterns == [
        "/.well-known/oauth-protected-resource/sales/infer"
    ]


def test_http_agent_requires_target_name():
    with pytest.raises(ValueError):
        get_target("http_agent").plan(domain_name=DOMAIN, path="/x", gateway_url=GW)
