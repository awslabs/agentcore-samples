# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""CLI tests — drive the interactive prompts via piped stdin."""

import json

from agwcd.cli import app
from agwcd.config import Config
from typer.testing import CliRunner

runner = CliRunner()
GW = "https://gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com"
GW2 = "https://gw2.gateway.bedrock-agentcore.us-east-1.amazonaws.com"


def _cfg(tmp_path):
    return str(tmp_path / "agwcd.json")


def _write(path, cfg: Config):
    cfg.save(path)


# --------------------------------------------------------------------------- #
# init — guided walkthrough                                                   #
# --------------------------------------------------------------------------- #
def test_init_root_with_one_endpoint(tmp_path):
    path = _cfg(tmp_path)
    # domain / no-geo / attach? yes / path / gateway / verify? no /
    # add endpoint? yes / type mcp / add endpoint? no / deploy? no
    stdin = f"mcp.example.com\nn\ny\n/\n{GW}\nn\ny\nmcp\nn\nn\n"
    result = runner.invoke(app, ["init", "-c", path], input=stdin)
    assert result.exit_code == 0, result.output

    cfg = Config.load(path)
    assert cfg.domain_name == "mcp.example.com"
    assert len(cfg.routes) == 1
    assert cfg.routes[0].path == "/"
    assert cfg.routes[0].gateway_url == GW
    assert cfg.routes[0].origin_verify is False
    assert [e.type for e in cfg.routes[0].endpoints] == ["mcp"]


def test_init_path_based_two_gateways(tmp_path):
    path = _cfg(tmp_path)
    stdin = f"mcp.example.com\nn\ny\n/it\n{GW}\nn\nn\ny\n/ht\n{GW2}\nn\nn\nn\nn\n"
    result = runner.invoke(app, ["init", "-c", path], input=stdin)
    assert result.exit_code == 0, result.output

    cfg = Config.load(path)
    assert [r.path for r in cfg.routes] == ["/it", "/ht"]
    assert cfg.routes[0].gateway_url == GW
    assert cfg.routes[1].gateway_url == GW2


# --------------------------------------------------------------------------- #
# prompt hardening                                                            #
# --------------------------------------------------------------------------- #
def test_endpoint_type_menu_reprompts_then_accepts_number(tmp_path):
    path = _cfg(tmp_path)
    _write(path, Config(domain_name="mcp.example.com", routes=[]))
    # attach a root gateway first (non-interactively minus the endpoint loop)
    runner.invoke(
        app,
        ["add", "path", "/", "-g", GW, "--no-origin-verify", "-c", path],
        input="n\n",
    )
    # add endpoint interactively: bad type -> re-prompt -> "1" (mcp).
    result = runner.invoke(
        app, ["add", "endpoint", "/", "-c", path], input="bogus\n1\n"
    )
    assert result.exit_code == 0, result.output
    cfg = Config.load(path)
    assert [e.type for e in cfg.route("/").endpoints] == ["mcp"]


def test_gateway_url_reprompts_until_https(tmp_path):
    path = _cfg(tmp_path)
    _write(path, Config(domain_name="mcp.example.com", routes=[]))
    # path / bad url -> reprompt -> good url / verify no / add endpoint no
    stdin = f"/\nhttp://insecure\n{GW}\nn\nn\n"
    result = runner.invoke(app, ["add", "path", "-c", path], input=stdin)
    assert result.exit_code == 0, result.output
    cfg = Config.load(path)
    assert cfg.route("/").gateway_url == GW


# --------------------------------------------------------------------------- #
# exclusivity + non-interactive flags still work                             #
# --------------------------------------------------------------------------- #
def test_add_path_rejects_path_when_root_exists(tmp_path):
    path = _cfg(tmp_path)
    _write(
        path,
        Config(domain_name="mcp.example.com", routes=[]),
    )
    runner.invoke(
        app,
        ["add", "path", "/", "-g", GW, "--no-origin-verify", "-c", path],
        input="n\n",
    )
    result = runner.invoke(
        app,
        ["add", "path", "/sales", "-g", GW2, "--no-origin-verify", "-c", path],
        input="n\n",
    )
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


def test_non_interactive_flags_unchanged(tmp_path):
    path = _cfg(tmp_path)
    r1 = runner.invoke(app, ["setup", "--domain", "mcp.example.com", "-c", path])
    assert r1.exit_code == 0, r1.output
    r2 = runner.invoke(
        app,
        ["add", "path", "/sales", "-g", GW, "--no-origin-verify", "-c", path],
        input="n\n",  # decline the endpoint loop
    )
    assert r2.exit_code == 0, r2.output
    r3 = runner.invoke(
        app,
        ["add", "endpoint", "/sales", "-t", "http_mcp", "-n", "catalog", "-c", path],
    )
    assert r3.exit_code == 0, r3.output

    with open(path) as f:
        data = json.loads(f.read())
    assert data["domain_name"] == "mcp.example.com"
    ep = data["routes"][0]["endpoints"][0]
    assert ep == {"type": "http_mcp", "target_name": "catalog"}
