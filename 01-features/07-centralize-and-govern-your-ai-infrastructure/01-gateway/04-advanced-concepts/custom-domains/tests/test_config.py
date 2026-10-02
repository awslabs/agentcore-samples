# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
import json

import pytest
from agwcd.config import Config, ConfigError, Endpoint, Route, _norm_path


def _cfg():
    return Config(
        domain_name="mcp.example.com",
        geo_allowlist=["US", "CA"],
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


def test_roundtrip(tmp_path):
    cfg = _cfg()
    p = tmp_path / "agwcd.json"
    cfg.save(str(p))
    loaded = Config.load(str(p))
    assert loaded.to_dict() == cfg.to_dict()
    # File is valid JSON with expected top-level keys.
    data = json.loads(p.read_text())
    assert set(data) == {"domain_name", "geo_allowlist", "routes"}


def test_default_geo_allowlist():
    cfg = Config(domain_name="mcp.example.com")
    assert "US" in cfg.geo_allowlist and "DE" in cfg.geo_allowlist


@pytest.mark.parametrize("domain", ["", "not a domain", "http://x.com", "x"])
def test_invalid_domain(domain):
    with pytest.raises(ConfigError):
        Config(domain_name=domain, routes=[]).validate()


def test_invalid_geo():
    with pytest.raises(ConfigError):
        Config(domain_name="mcp.example.com", geo_allowlist=["USA"]).validate()


def test_duplicate_path():
    cfg = Config(
        domain_name="mcp.example.com",
        routes=[
            Route("/a", "https://g.example.com", []),
            Route("a", "https://g.example.com", []),  # normalizes to /a
        ],
    )
    with pytest.raises(ConfigError, match="duplicate route path"):
        cfg.validate()


def test_gateway_url_must_be_https():
    cfg = Config(
        domain_name="mcp.example.com", routes=[Route("/a", "http://g.example.com", [])]
    )
    with pytest.raises(ConfigError, match="https"):
        cfg.validate()


def test_http_mcp_requires_target_name():
    cfg = Config(
        domain_name="mcp.example.com",
        routes=[Route("/a", "https://g.example.com", [Endpoint("http_mcp")])],
    )
    with pytest.raises(ConfigError, match="target_name"):
        cfg.validate()


def test_mcp_rejects_target_name():
    cfg = Config(
        domain_name="mcp.example.com",
        routes=[Route("/a", "https://g.example.com", [Endpoint("mcp", "nope")])],
    )
    with pytest.raises(ConfigError, match="does not take a target_name"):
        cfg.validate()


def test_unknown_type():
    cfg = Config(
        domain_name="mcp.example.com",
        routes=[Route("/a", "https://g.example.com", [Endpoint("grpc")])],
    )
    with pytest.raises(ConfigError):
        cfg.validate()


def test_duplicate_endpoint_in_route():
    cfg = Config(
        domain_name="mcp.example.com",
        routes=[
            Route(
                "/a",
                "https://g.example.com",
                [Endpoint("http_mcp", "x"), Endpoint("http_mcp", "x")],
            )
        ],
    )
    with pytest.raises(ConfigError, match="duplicate endpoint"):
        cfg.validate()


def test_cross_route_pattern_collision():
    # Two mcp endpoints producing the same viewer path.
    cfg = Config(
        domain_name="mcp.example.com",
        routes=[
            Route("/sales", "https://g.example.com", [Endpoint("http_mcp", "catalog")]),
            Route("/sales/catalog", "https://g.example.com", [Endpoint("mcp")]),
        ],
    )
    with pytest.raises(ConfigError, match="collide"):
        cfg.validate()


def test_load_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        Config.load(str(tmp_path / "nope.json"))


@pytest.mark.parametrize(
    "raw,norm",
    [
        ("", "/"),
        ("/", "/"),
        ("sales", "/sales"),
        ("/sales/", "/sales"),
        ("a/b", "/a/b"),
    ],
)
def test_norm_path(raw, norm):
    assert _norm_path(raw) == norm


def test_iter_plans_count():
    plans = list(_cfg().iter_plans())
    assert len(plans) == 4  # 1 on /hr + 3 on /sales


def test_root_and_path_gateways_mutually_exclusive():
    cfg = Config(
        domain_name="mcp.example.com",
        routes=[
            Route("/", "https://gwa.example.com", [Endpoint("mcp")]),
            Route("/hr", "https://gwb.example.com", [Endpoint("mcp")]),
        ],
    )
    with pytest.raises(ConfigError, match="mutually"):
        cfg.validate()


def test_origin_verify_defaults_off_and_roundtrips(tmp_path):
    cfg = Config(
        domain_name="mcp.example.com",
        routes=[Route("/a", "https://g.example.com", [Endpoint("mcp")])],
    )
    assert cfg.routes[0].origin_verify is False
    cfg.routes[0].origin_verify = True
    p = tmp_path / "c.json"
    cfg.save(str(p))
    assert Config.load(str(p)).routes[0].origin_verify is True


def test_origin_verify_gateways():
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
    assert cfg.origin_verify_gateways() == ["https://gwa.example.com"]


def test_origin_verify_conflict_across_shared_gateway():
    cfg = Config(
        domain_name="mcp.example.com",
        routes=[
            Route(
                "/a", "https://gwa.example.com", [Endpoint("mcp")], origin_verify=True
            ),
            Route(
                "/b", "https://gwa.example.com", [Endpoint("mcp")], origin_verify=False
            ),
        ],
    )
    with pytest.raises(ConfigError, match="conflicting origin_verify"):
        cfg.validate()
