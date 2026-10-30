# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""``agwcd`` — AgentCore Gateway Custom Domain CLI.

Manages an ``agwcd.json`` config (custom domain → gateway routes → endpoints)
and shells out to the AWS CDK to deploy a CloudFront distribution that fronts
the gateways on the custom domain with correct OAuth / A2A discovery.
"""

from __future__ import annotations

import subprocess
import sys

import typer
from custom_domains.targets import get_target, target_types

from agwcd.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_GEO_ALLOWLIST,
    Config,
    ConfigError,
    Endpoint,
    Route,
    _norm_path,
)

app = typer.Typer(
    add_completion=False,
    help=(
        "Front Amazon Bedrock AgentCore Gateways behind a custom domain.\n\n"
        "New here? Run `agwcd init` for a guided walkthrough."
    ),
    no_args_is_help=True,
)
add_app = typer.Typer(help="Add routes and endpoints.", no_args_is_help=True)
remove_app = typer.Typer(help="Remove routes and endpoints.", no_args_is_help=True)
app.add_typer(add_app, name="add")
app.add_typer(remove_app, name="remove")

CONFIG_OPT = typer.Option(
    DEFAULT_CONFIG_PATH, "--config", "-c", help="Config file path."
)


def _err(msg: str) -> typer.Exit:
    typer.secho(f"error: {msg}", fg=typer.colors.RED, err=True)
    return typer.Exit(1)


def _load(path: str) -> Config:
    try:
        return Config.load(path)
    except ConfigError as e:
        raise _err(str(e))


def _save(cfg: Config, path: str) -> None:
    try:
        cfg.save(path)
    except ConfigError as e:
        raise _err(str(e))


# --------------------------------------------------------------------------- #
# interactive prompt helpers (re-prompt on invalid input)                     #
# --------------------------------------------------------------------------- #
def _prompt_domain(default: str | None = None) -> str:
    """Prompt for a custom domain, re-asking until it looks like a hostname."""
    while True:
        val = typer.prompt(
            "Custom domain (e.g. mcp.example.com)", default=default
        ).strip()
        if "://" not in val and " " not in val and "." in val and len(val) >= 3:
            return val
        typer.secho(
            "  enter a bare hostname like mcp.example.com (no scheme)",
            fg=typer.colors.RED,
            err=True,
        )


def _prompt_gateway_url(default: str | None = None) -> str:
    """Prompt for a gateway URL, re-asking until it is an https:// URL."""
    while True:
        val = typer.prompt("Gateway URL (https://...)", default=default).strip()
        if val.startswith("https://") and len(val) > len("https://"):
            return val
        typer.secho("  must start with https://", fg=typer.colors.RED, err=True)


def _prompt_nonempty(label: str) -> str:
    while True:
        val = typer.prompt(label).strip()
        if val:
            return val
        typer.secho("  cannot be empty", fg=typer.colors.RED, err=True)


def _prompt_choice(label: str, choices: list[tuple], default: str) -> str:
    """Numbered menu over ``choices`` (list of ``(key, description)``).

    Accepts either the 1-based number or the literal key; re-prompts otherwise.
    """
    for i, (key, desc) in enumerate(choices, 1):
        typer.echo(f"  {i}) {key} — {desc}")
    keys = [k for k, _ in choices]
    while True:
        raw = typer.prompt(label, default=default).strip()
        if raw in keys:
            return raw
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return keys[int(raw) - 1]
        typer.secho(
            f"  pick 1-{len(choices)} or a type name", fg=typer.colors.RED, err=True
        )


# --------------------------------------------------------------------------- #
# setup                                                                       #
# --------------------------------------------------------------------------- #
@app.command()
def setup(
    domain: str | None = typer.Option(
        None, help="Custom domain, e.g. mcp.example.com."
    ),
    config: str = CONFIG_OPT,
):
    """Initialize a new config for a custom domain."""
    from pathlib import Path

    if Path(config).exists() and not typer.confirm(
        f"{config} already exists — overwrite?", default=False
    ):
        raise typer.Exit(0)

    domain = domain or _prompt_domain()
    cfg = Config(
        domain_name=domain, geo_allowlist=list(DEFAULT_GEO_ALLOWLIST), routes=[]
    )
    _save(cfg, config)
    typer.secho(f"Wrote {config} for {domain}.", fg=typer.colors.GREEN)
    typer.echo("Next: `agwcd add path` to attach a gateway.")


# --------------------------------------------------------------------------- #
# init — guided end-to-end walkthrough                                        #
# --------------------------------------------------------------------------- #
@app.command()
def init(config: str = CONFIG_OPT):
    """Guided setup: walk through domain → gateway(s) → endpoints → deploy."""
    from pathlib import Path

    if Path(config).exists() and not typer.confirm(
        f"{config} already exists — start over and overwrite?", default=False
    ):
        typer.echo(
            "Leaving it as-is. Use `agwcd list` to review or `agwcd add path` "
            "to extend it."
        )
        raise typer.Exit(0)

    typer.secho(
        "Let's set up a custom domain in front of your AgentCore Gateway(s).",
        bold=True,
    )
    domain = _prompt_domain()

    geo = list(DEFAULT_GEO_ALLOWLIST)
    if typer.confirm(
        "Customize the geo allowlist? (default: US + Canada + EU)", default=False
    ):
        raw = typer.prompt("Allowed country codes (comma-separated, e.g. US,CA,GB)")
        geo = [c.strip().upper() for c in raw.split(",") if c.strip()]

    cfg = Config(domain_name=domain, geo_allowlist=geo, routes=[])
    _save(cfg, config)  # persist early so a mid-way exit keeps progress
    typer.secho(f"\nWrote {config} for {domain}.", fg=typer.colors.GREEN)
    typer.echo(
        "A domain fronts EITHER one gateway at the root (/) OR one-or-more "
        "gateways under paths — not both.\n"
    )

    if typer.confirm("Attach a gateway now?", default=True):
        route = _add_path_interactive(cfg, config)
        # More paths only make sense when the first wasn't the (exclusive) root.
        while _norm_path(route.path) != "/" and typer.confirm(
            "Attach another gateway under a different path?", default=False
        ):
            route = _add_path_interactive(cfg, config)

    typer.secho("\nConfiguration:", bold=True)
    _render(cfg)
    typer.echo("")

    if cfg.routes and typer.confirm("Deploy now with `cdk deploy`?", default=False):
        _cdk("deploy", config, [])
        return
    typer.echo("Next steps:")
    if not cfg.routes:
        typer.echo("  agwcd add path        # attach a gateway")
    typer.echo("  agwcd list            # review the resulting URLs")
    typer.echo("  agwcd deploy          # deploy to us-east-1")


# --------------------------------------------------------------------------- #
# add path / add endpoint                                                     #
# --------------------------------------------------------------------------- #
@add_app.command("path")
def add_path(
    path: str | None = typer.Argument(
        None, help="URL path, e.g. /sales (or / for root)."
    ),
    gateway_url: str | None = typer.Option(None, "--gateway-url", "-g"),
    origin_verify: bool | None = typer.Option(
        None,
        "--origin-verify/--no-origin-verify",
        help="Require CloudFront-only access to this gateway. Prompted if omitted.",
    ),
    config: str = CONFIG_OPT,
):
    """Attach a gateway to a domain path, then optionally add endpoints."""
    cfg = _load(config)
    _add_path_interactive(cfg, config, path, gateway_url, origin_verify)


def _add_path_interactive(
    cfg: Config,
    config: str,
    path: str | None = None,
    gateway_url: str | None = None,
    origin_verify: bool | None = None,
) -> Route:
    """Prompt for (or accept) a path + gateway + origin-verify, append the route,
    then loop offering to add endpoints. Shared by `add path` and `init`."""
    path = path or typer.prompt("Path (use / for the root gateway)", default="/")
    if cfg.route(path) is not None:
        raise _err(f"path {path!r} already exists")

    # Root and path gateways are mutually exclusive.
    np = _norm_path(path)
    has_root = any(_norm_path(r.path) == "/" for r in cfg.routes)
    has_path = any(_norm_path(r.path) != "/" for r in cfg.routes)
    if np == "/" and has_path:
        raise _err(
            "cannot add a root gateway — this domain already fronts gateways "
            "under paths (root and path gateways are mutually exclusive)"
        )
    if np != "/" and has_root:
        raise _err(
            "cannot add a path gateway — this domain already fronts a gateway "
            "at the root (root and path gateways are mutually exclusive)"
        )

    gateway_url = gateway_url or _prompt_gateway_url()

    # Origin verification is a per-gateway property: inherit it when the gateway
    # is already used by another route, otherwise take the flag or ask.
    existing = next((r for r in cfg.routes if r.gateway_url == gateway_url), None)
    if origin_verify is None:
        if existing is not None:
            origin_verify = existing.origin_verify
            typer.echo(f"Reusing gateway (origin verification = {origin_verify}).")
        else:
            origin_verify = typer.confirm(
                "Enable origin verification (require CloudFront-only access) for "
                "this gateway?",
                default=True,
            )

    route = Route(
        path=_norm_path(path),
        gateway_url=gateway_url,
        endpoints=[],
        origin_verify=origin_verify,
    )
    cfg.routes.append(route)
    _save(cfg, config)
    typer.secho(
        f"Added path {route.path} → {gateway_url} "
        f"(origin verification {'on' if origin_verify else 'off'})",
        fg=typer.colors.GREEN,
    )
    if origin_verify:
        typer.echo(
            "  Post-deploy: attach the OriginVerifyInterceptor Lambda to this "
            "gateway as a REQUEST interceptor (see README)."
        )

    while typer.confirm(f"Add an endpoint to {route.path}?", default=True):
        _prompt_endpoint(cfg, route, config)
    return route


@add_app.command("endpoint")
def add_endpoint(
    path: str | None = typer.Argument(
        None, help="Existing path to add an endpoint to."
    ),
    type: str | None = typer.Option(
        None, "--type", "-t", help=f"One of: {', '.join(target_types())}."
    ),
    target_name: str | None = typer.Option(None, "--target-name", "-n"),
    config: str = CONFIG_OPT,
):
    """Add an endpoint (mcp / http_mcp / http_a2a / …) to an existing path."""
    cfg = _load(config)
    path = path or typer.prompt("Path")
    route = cfg.route(path)
    if route is None:
        raise _err(f"no such path {path!r} — add it with `agwcd add path`")

    if type is None:
        _prompt_endpoint(cfg, route, config)
        return
    _add_endpoint(cfg, route, type, target_name, config)


def _prompt_endpoint(cfg: Config, route: Route, config: str) -> None:
    typer.echo("Endpoint types:")
    choices = []
    for key in target_types():
        tt = get_target(key)
        suffix = " (needs target name)" if tt.requires_target_name else ""
        choices.append((key, tt.description + suffix))
    etype = _prompt_choice("Endpoint type (number or name)", choices, default="mcp")
    tt = get_target(etype)
    tname = _prompt_nonempty("Target name") if tt.requires_target_name else None
    _add_endpoint(cfg, route, etype, tname, config)


def _add_endpoint(
    cfg: Config,
    route: Route,
    etype: str,
    tname: str | None,
    config: str,
) -> None:
    route.endpoints.append(Endpoint(type=etype, target_name=tname))
    try:
        cfg.validate()
    except ConfigError as e:
        route.endpoints.pop()
        raise _err(str(e))
    _save(cfg, config)
    label = etype + (f"/{tname}" if tname else "")
    plan = get_target(etype).plan(
        domain_name=cfg.domain_name,
        path=route.path,
        gateway_url=route.gateway_url,
        target_name=tname,
    )
    typer.secho(f"Added {label} on {route.path}", fg=typer.colors.GREEN)
    typer.echo(f"  live: https://{cfg.domain_name}{plan.live_patterns[0]}")


# --------------------------------------------------------------------------- #
# list / remove                                                               #
# --------------------------------------------------------------------------- #
@app.command("list")
def list_config(config: str = CONFIG_OPT):
    """Show the configured domain, routes, and endpoints."""
    _render(_load(config))


def _render(cfg: Config) -> None:
    typer.secho(f"Domain: {cfg.domain_name}", bold=True)
    typer.echo(f"Geo allowlist: {', '.join(cfg.geo_allowlist)}")
    if not cfg.routes:
        typer.echo("(no routes — `agwcd add path`)")
        return
    for r in cfg.routes:
        verify = "verified" if r.origin_verify else "unverified"
        typer.secho(f"\n{r.path}  →  {r.gateway_url}  [{verify}]", fg=typer.colors.CYAN)
        if not r.endpoints:
            typer.echo("  (no endpoints)")
        for e in r.endpoints:
            plan = get_target(e.type).plan(
                domain_name=cfg.domain_name,
                path=r.path,
                gateway_url=r.gateway_url,
                target_name=e.target_name,
            )
            label = e.type + (f"/{e.target_name}" if e.target_name else "")
            typer.echo(f"  - {label}: https://{cfg.domain_name}{plan.live_patterns[0]}")


@remove_app.command("path")
def remove_path(
    path: str = typer.Argument(..., help="Path to remove."),
    config: str = CONFIG_OPT,
):
    """Remove a path and all its endpoints."""
    cfg = _load(config)
    route = cfg.route(path)
    if route is None:
        raise _err(f"no such path {path!r}")
    cfg.routes.remove(route)
    _save(cfg, config)
    typer.secho(f"Removed path {route.path}", fg=typer.colors.GREEN)


@remove_app.command("endpoint")
def remove_endpoint(
    path: str = typer.Argument(..., help="Path the endpoint is on."),
    type: str = typer.Argument(..., help="Endpoint type."),
    target_name: str | None = typer.Option(None, "--target-name", "-n"),
    config: str = CONFIG_OPT,
):
    """Remove a single endpoint from a path."""
    cfg = _load(config)
    route = cfg.route(path)
    if route is None:
        raise _err(f"no such path {path!r}")
    match = [
        e for e in route.endpoints if e.type == type and e.target_name == target_name
    ]
    if not match:
        raise _err(f"no endpoint {type!r} on {path!r}")
    route.endpoints.remove(match[0])
    _save(cfg, config)
    typer.secho(f"Removed {type} from {route.path}", fg=typer.colors.GREEN)


# --------------------------------------------------------------------------- #
# cdk passthrough                                                             #
# --------------------------------------------------------------------------- #
def _cdk(cdk_cmd: str, config: str, extra: list[str]) -> None:
    cfg = _load(config)  # validate before invoking CDK
    _ = cfg
    cmd = ["cdk", cdk_cmd, "-c", f"agwcd_config={config}", *extra]
    typer.secho(f"$ {' '.join(cmd)}", fg=typer.colors.BRIGHT_BLACK)
    try:
        result = subprocess.run(cmd, check=False)
    except FileNotFoundError:
        raise _err("`cdk` not found — install the AWS CDK CLI (npm i -g aws-cdk)")
    if result.returncode != 0:
        raise typer.Exit(result.returncode)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def synth(ctx: typer.Context, config: str = CONFIG_OPT):
    """Synthesize the CloudFormation template (`cdk synth`)."""
    _cdk("synth", config, ctx.args)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def deploy(ctx: typer.Context, config: str = CONFIG_OPT):
    """Deploy the stack (`cdk deploy`). Must target us-east-1."""
    _cdk("deploy", config, ctx.args)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def diff(ctx: typer.Context, config: str = CONFIG_OPT):
    """Diff the deployed stack against the current config (`cdk diff`)."""
    _cdk("diff", config, ctx.args)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def destroy(ctx: typer.Context, config: str = CONFIG_OPT):
    """Tear down the stack (`cdk destroy`)."""
    _cdk("destroy", config, ctx.args)


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(app())
