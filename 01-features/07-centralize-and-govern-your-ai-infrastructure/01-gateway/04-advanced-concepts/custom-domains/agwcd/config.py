# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""``agwcd.json`` config model: schema, load/save, validation, and expansion
into per-endpoint plans. Pure Python (no CDK) — shared by the CLI and the
CDK stack."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from custom_domains.targets import EndpointPlan, get_target

DEFAULT_CONFIG_PATH = "agwcd.json"

# Default CloudFront geo allowlist (US + Canada + EU member states) — carried
# over from the original stack; overridable per-domain in config.
DEFAULT_GEO_ALLOWLIST: list[str] = [
    "US",
    "CA",
    "AT",
    "BE",
    "BG",
    "HR",
    "CY",
    "CZ",
    "DK",
    "EE",
    "FI",
    "FR",
    "DE",
    "GR",
    "HU",
    "IE",
    "IT",
    "LV",
    "LT",
    "LU",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SK",
    "SI",
    "ES",
    "SE",
]

_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-zA-Z0-9-]{1,63}\.)+[a-zA-Z]{2,63}$")
_TARGET_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


class ConfigError(ValueError):
    """Raised for any invalid configuration."""


@dataclass
class Endpoint:
    type: str
    target_name: str | None = None

    @staticmethod
    def from_dict(d: dict) -> Endpoint:
        return Endpoint(type=d["type"], target_name=d.get("target_name"))

    def to_dict(self) -> dict:
        out = {"type": self.type}
        if self.target_name:
            out["target_name"] = self.target_name
        return out


@dataclass
class Route:
    path: str
    gateway_url: str
    endpoints: list[Endpoint] = field(default_factory=list)
    # Opt-in origin verification: inject a per-gateway secret header so the
    # gateway can reject traffic that did not come through CloudFront. Off
    # unless explicitly enabled. Keyed per gateway (see validate()).
    origin_verify: bool = False

    @staticmethod
    def from_dict(d: dict) -> Route:
        return Route(
            path=d["path"],
            gateway_url=d["gateway_url"],
            endpoints=[Endpoint.from_dict(e) for e in d.get("endpoints", [])],
            origin_verify=bool(d.get("origin_verify", False)),
        )

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "gateway_url": self.gateway_url,
            "origin_verify": self.origin_verify,
            "endpoints": [e.to_dict() for e in self.endpoints],
        }


@dataclass
class Config:
    domain_name: str
    geo_allowlist: list[str] = field(
        default_factory=lambda: list(DEFAULT_GEO_ALLOWLIST)
    )
    routes: list[Route] = field(default_factory=list)

    # ---- (de)serialization -------------------------------------------------
    @staticmethod
    def from_dict(d: dict) -> Config:
        return Config(
            domain_name=d["domain_name"],
            geo_allowlist=list(d.get("geo_allowlist") or DEFAULT_GEO_ALLOWLIST),
            routes=[Route.from_dict(r) for r in d.get("routes", [])],
        )

    def to_dict(self) -> dict:
        return {
            "domain_name": self.domain_name,
            "geo_allowlist": self.geo_allowlist,
            "routes": [r.to_dict() for r in self.routes],
        }

    @staticmethod
    def load(path: str = DEFAULT_CONFIG_PATH) -> Config:
        p = Path(path)
        if not p.exists():
            raise ConfigError(
                f"config file {path!r} not found — run `agwcd setup` first"
            )
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            raise ConfigError(f"{path} is not valid JSON: {e}") from e
        cfg = Config.from_dict(data)
        cfg.validate()
        return cfg

    def save(self, path: str = DEFAULT_CONFIG_PATH) -> None:
        self.validate()
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    # ---- lookup helpers ----------------------------------------------------
    def route(self, path: str) -> Route | None:
        path = _norm_path(path)
        for r in self.routes:
            if _norm_path(r.path) == path:
                return r
        return None

    # ---- validation --------------------------------------------------------
    def validate(self) -> None:
        if not self.domain_name or not _HOSTNAME_RE.match(self.domain_name):
            raise ConfigError(f"invalid domain_name {self.domain_name!r}")

        for code in self.geo_allowlist:
            if not _COUNTRY_RE.match(code):
                raise ConfigError(
                    f"invalid geo_allowlist entry {code!r} (want 2-letter code)"
                )

        # A root gateway (path "/") and path gateways are mutually exclusive:
        # a domain either fronts a single gateway at the root, or one-or-more
        # gateways under distinct paths — never both (avoids /<path> collisions
        # between a root gateway's targets and a path gateway).
        has_root = any(_norm_path(r.path) == "/" for r in self.routes)
        has_path = any(_norm_path(r.path) != "/" for r in self.routes)
        if has_root and has_path:
            raise ConfigError(
                "a root gateway (path '/') and path gateways are mutually "
                "exclusive — use either one gateway at the root or one-or-more "
                "gateways under paths, not both"
            )

        seen_paths: set[str] = set()
        # Origin verification is a property of the gateway, so all routes that
        # share a gateway_url must agree on it.
        verify_by_gateway: dict[str, bool] = {}
        for r in self.routes:
            np = _norm_path(r.path)
            if np in seen_paths:
                raise ConfigError(f"duplicate route path {r.path!r}")
            seen_paths.add(np)

            if not (r.gateway_url.startswith("https://") and len(r.gateway_url) > 8):
                raise ConfigError(
                    f"route {r.path!r}: gateway_url must be an https:// URL"
                )

            if (
                r.gateway_url in verify_by_gateway
                and verify_by_gateway[r.gateway_url] != r.origin_verify
            ):
                raise ConfigError(
                    f"gateway {r.gateway_url!r} has conflicting origin_verify "
                    f"settings across routes — a gateway must be all-on or all-off"
                )
            verify_by_gateway[r.gateway_url] = r.origin_verify

            seen_targets: set[tuple[str, str | None]] = set()
            for e in r.endpoints:
                try:
                    tt = get_target(e.type)
                except ValueError as exc:
                    raise ConfigError(f"route {r.path!r}: {exc}") from exc
                if tt.requires_target_name:
                    if not e.target_name or not _TARGET_NAME_RE.match(e.target_name):
                        raise ConfigError(
                            f"route {r.path!r}: endpoint {e.type!r} requires a "
                            f"valid target_name"
                        )
                elif e.target_name:
                    raise ConfigError(
                        f"route {r.path!r}: endpoint {e.type!r} does not take a "
                        f"target_name"
                    )
                key = (e.type, e.target_name)
                if key in seen_targets:
                    raise ConfigError(
                        f"route {r.path!r}: duplicate endpoint {e.type!r}"
                        + (f"/{e.target_name}" if e.target_name else "")
                    )
                seen_targets.add(key)

        # Cross-route: CloudFront path patterns must be unique.
        seen_patterns: dict[str, str] = {}
        for _route, _ep, plan in self.iter_plans():
            for pat in plan.live_patterns + plan.discovery_patterns:
                if pat in seen_patterns:
                    raise ConfigError(
                        f"path {pat!r} produced by two endpoints — routes/targets "
                        f"collide"
                    )
                seen_patterns[pat] = _ep.type

    def origin_verify_gateways(self) -> list[str]:
        """Distinct gateway URLs (sorted) that have origin verification on."""
        return sorted({r.gateway_url for r in self.routes if r.origin_verify})

    # ---- expansion ---------------------------------------------------------
    def iter_plans(self) -> Iterator[tuple[Route, Endpoint, EndpointPlan]]:
        """Yield (route, endpoint, plan) for every endpoint in the config."""
        for r in self.routes:
            for e in r.endpoints:
                plan = get_target(e.type).plan(
                    domain_name=self.domain_name,
                    path=r.path,
                    gateway_url=r.gateway_url,
                    target_name=e.target_name,
                )
                yield r, e, plan


def _norm_path(path: str) -> str:
    if path in ("", "/"):
        return "/"
    return "/" + path.strip("/")
