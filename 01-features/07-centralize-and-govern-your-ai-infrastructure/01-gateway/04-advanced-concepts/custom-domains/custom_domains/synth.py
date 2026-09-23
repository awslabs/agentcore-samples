# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""Turn a :class:`~agwcd.config.Config` into the concrete artifacts the CDK
stack needs: the CloudFront viewer-request function code, the discovery
Lambda's ``routes.json``, and the CloudFront behavior specs. Pure Python so it
can be unit-tested without synthesizing CloudFormation."""

from __future__ import annotations

import json
from dataclasses import dataclass

from agwcd.config import Config

# Request header the viewer-request function injects for the WWW-Authenticate
# rewrite (Lambda@Edge). Must also be forwarded to the origin/edge functions.
RESOURCE_METADATA_HEADER = "x-agwcd-resource-metadata"

# The discovery-origin Function URL uses OAC, so CloudFront occupies the
# `Authorization` header with its SigV4 signature. To let the discovery Lambda
# fetch an auth-protected downstream doc (e.g. an A2A agent card) on the
# caller's behalf, a viewer-request function copies the caller's bearer token
# into this side header (and deletes the original) before OAC signs.
FORWARDED_AUTH_HEADER = "x-agwcd-authorization"

# Viewer-request CloudFront Function attached to discovery behaviors. Takes no
# config, so it is a constant. ES5.1-safe (cloudfront-js).
DISCOVERY_AUTH_FUNCTION_CODE = (
    "function handler(event) {\n"
    "  var req = event.request;\n"
    "  var h = req.headers;\n"
    "  if (h.authorization) {\n"
    f'    h["{FORWARDED_AUTH_HEADER}"] = {{ value: h.authorization.value }};\n'
    "    delete h.authorization;\n"
    "  }\n"
    "  return req;\n"
    "}\n"
)


@dataclass(frozen=True)
class LiveBehaviorSpec:
    path_pattern: str
    gateway_url: str
    needs_www_auth: bool


@dataclass(frozen=True)
class DiscoveryBehaviorSpec:
    path_pattern: str


@dataclass(frozen=True)
class SynthResult:
    function_code: str
    discovery_auth_function_code: str
    routes_json: dict
    live_behaviors: list[LiveBehaviorSpec]
    discovery_behaviors: list[DiscoveryBehaviorSpec]
    gateway_urls: list[str]  # distinct, sorted — one HttpOrigin each
    origin_verify_gateways: list[str]  # subset of gateway_urls with verification on


def _function_code(routes: list[dict]) -> str:
    """Generate the CloudFront Function (cloudfront-js-1.0 / ES5.1 safe).

    ``routes`` are ordered longest-prefix-first. On a match the viewer URI is
    rewritten to the gateway origin path and (for MCP) the resource-metadata
    header is injected; unmatched requests are denied with 403.
    """
    table = json.dumps(routes, separators=(",", ":"))
    return (
        "function handler(event) {\n"
        "  var req = event.request;\n"
        "  var uri = req.uri;\n"
        # Strip any client-supplied resource-metadata header up front so a
        # viewer can never forge the value the WWW-Authenticate rewrite trusts;
        # it is re-injected below only on a matched route. Applies to the
        # 403 path too.
        f'  delete req.headers["{RESOURCE_METADATA_HEADER}"];\n'
        f"  var routes = {table};\n"
        "  for (var i = 0; i < routes.length; i++) {\n"
        "    var r = routes[i];\n"
        '    if (uri === r.p || uri.indexOf(r.p + "/") === 0) {\n'
        "      req.uri = r.o + uri.substring(r.p.length);\n"
        f'      if (r.rm) {{ req.headers["{RESOURCE_METADATA_HEADER}"] = {{ value: r.rm }}; }}\n'
        "      return req;\n"
        "    }\n"
        "  }\n"
        '  return { statusCode: 403, statusDescription: "Forbidden" };\n'
        "}\n"
    )


def build(config: Config) -> SynthResult:
    config.validate()

    fn_routes: list[dict] = []
    routes_json: list[dict] = []
    live: list[LiveBehaviorSpec] = []
    discovery: list[DiscoveryBehaviorSpec] = []
    gateways: set[str] = set()

    for route, _endpoint, plan in config.iter_plans():
        gateways.add(route.gateway_url)

        fe = plan.function_entry
        fn_routes.append(
            {"p": fe.prefix, "o": fe.origin_prefix, "rm": fe.resource_metadata}
        )
        for pat in plan.live_patterns:
            live.append(
                LiveBehaviorSpec(
                    path_pattern=pat,
                    gateway_url=route.gateway_url,
                    needs_www_auth=plan.needs_www_auth,
                )
            )
        for pat in plan.discovery_patterns:
            discovery.append(DiscoveryBehaviorSpec(path_pattern=pat))
        for dr in plan.discovery_routes:
            routes_json.append(
                {
                    "path": dr.path,
                    "kind": dr.kind,
                    "downstream_url": dr.downstream_url,
                    "overrides": dr.overrides,
                    "resource_metadata": dr.resource_metadata,
                }
            )

    # Longest prefix first so nested routes match before their parents.
    fn_routes.sort(key=lambda r: len(r["p"]), reverse=True)

    return SynthResult(
        function_code=_function_code(fn_routes),
        discovery_auth_function_code=DISCOVERY_AUTH_FUNCTION_CODE,
        routes_json={"routes": routes_json},
        live_behaviors=live,
        discovery_behaviors=discovery,
        gateway_urls=sorted(gateways),
        origin_verify_gateways=config.origin_verify_gateways(),
    )
