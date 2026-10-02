# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""Regional discovery Lambda (CloudFront origin behind a Function URL).

Serves the per-path discovery documents that the gateway cannot: for each
configured discovery path it fetches the *downstream* document from the raw
gateway and overrides only the field(s) that must reflect the custom domain —
`resource` for OAuth Protected Resource Metadata (RFC 9728), `url` (and
`additionalInterfaces[].url`) for an A2A Agent Card. The document is never
built from scratch; everything else is passed through untouched.

Lambda@Edge cannot read an origin response body, so this rewrite is done here
(a normal regional Lambda) rather than at the edge.

Routing table is baked into ``routes.json`` next to this file at synth time,
which avoids the 4 KB Lambda environment-variable limit.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

# Side header carrying the caller's bearer token (see synth.FORWARDED_AUTH_HEADER):
# the OAC-signed origin request occupies `Authorization`, so a viewer-request
# CloudFront Function copies the caller's token here for us to forward downstream.
FORWARDED_AUTH_HEADER = "x-agwcd-authorization"

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_ROUTES_FILE = Path(__file__).parent / "routes.json"
# Baked at synth time. Tolerate its absence (e.g. unit tests import this module
# before a synth has generated the file); real deploys always include it.
_ROUTES: list[dict] = (
    json.loads(_ROUTES_FILE.read_text()).get("routes", [])
    if _ROUTES_FILE.exists()
    else []
)
_ROUTES_BY_PATH: dict[str, dict] = {r["path"]: r for r in _ROUTES}

ORIGIN_VERIFY_HEADER = os.environ.get("ORIGIN_VERIFY_HEADER", "")
# Map of gateway base URL -> origin-verify secret *ARN*, for the gateways that
# have verification enabled. The ARN is not sensitive; the value is fetched from
# Secrets Manager at runtime (cached per warm container). Downstream fetches to
# those gateways carry the header; all others are sent without it.
ORIGIN_VERIFY_MAP: dict[str, str] = json.loads(
    os.environ.get("ORIGIN_VERIFY_MAP", "{}")
)
# A downstream doc can be slow (an A2A agent card is rendered by invoking the
# agent runtime — observed ~5-7s). Default generously; the stack sets this and
# the Lambda's total timeout to stay under CloudFront's 30s origin limit.
FETCH_TIMEOUT_SECONDS = float(os.environ.get("FETCH_TIMEOUT_SECONDS", "20"))
# Cap the downstream read so a hostile/broken origin can't exhaust memory or
# blow the Lambda response limit. Default 1 MiB — discovery docs are tiny.
MAX_DOWNSTREAM_BYTES = int(os.environ.get("MAX_DOWNSTREAM_BYTES", str(1024 * 1024)))

_secret_cache: dict[str, str] = {}


def _secret_value(arn: str) -> str:
    """Fetch (and cache) a Secrets Manager secret value by ARN."""
    if arn not in _secret_cache:
        import boto3  # lazy: keeps module import offline for unit tests

        client = boto3.client("secretsmanager")
        _secret_cache[arn] = client.get_secret_value(SecretId=arn)["SecretString"]
    return _secret_cache[arn]


def _origin_verify_value_for(url: str) -> str | None:
    """Return the origin-verify secret value for the gateway serving ``url``."""
    for base, arn in ORIGIN_VERIFY_MAP.items():
        # Exact match or a real path boundary — never a bare prefix, so
        # ``https://gw1...`` cannot match ``https://gw1....evil``.
        if url == base or url.startswith(base + "/"):
            return _secret_value(arn)
    return None


def apply_override(kind: str, doc: dict, overrides: dict[str, str]) -> dict:
    """Override only the custom-domain-facing fields of a discovery document.

    Pure function (no I/O) so it can be unit-tested directly.
    """
    if kind == "prm":
        # RFC 9728: the `resource` value MUST equal the URL the client used.
        # Everything else (authorization_servers, scopes, …) is preserved from
        # the gateway's own PRM, which is authoritative for the gateway's single
        # inbound authorizer.
        doc["resource"] = overrides["resource"]
    elif kind == "agent_card":
        # A2A: `url` is the primary connection endpoint.
        doc["url"] = overrides["url"]
        gateway_base = overrides.get("gateway_base")
        agent_base = overrides.get("agent_base")
        if gateway_base and agent_base:
            for iface in doc.get("additionalInterfaces") or []:
                url = iface.get("url", "")
                if url == gateway_base or url.startswith(gateway_base + "/"):
                    iface["url"] = agent_base + url[len(gateway_base) :]
    else:
        raise ValueError(f"unknown discovery kind {kind!r}")
    return doc


def match_route(path: str) -> dict | None:
    """Find the discovery route for a request path (query stripped)."""
    return _ROUTES_BY_PATH.get(path.split("?", 1)[0])


def _fetch_downstream(url: str, auth: str | None = None) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    value = _origin_verify_value_for(url)
    if ORIGIN_VERIFY_HEADER and value:
        req.add_header(ORIGIN_VERIFY_HEADER, value)
    # Forward the caller's bearer token so an auth-protected downstream doc
    # (e.g. an A2A agent card behind the gateway authorizer) can be retrieved.
    if auth:
        req.add_header("Authorization", auth)
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
        # Read one byte past the cap: if we get it, the body is oversized.
        raw = resp.read(MAX_DOWNSTREAM_BYTES + 1)
    if len(raw) > MAX_DOWNSTREAM_BYTES:
        raise ValueError("downstream document exceeds MAX_DOWNSTREAM_BYTES")
    return json.loads(raw.decode("utf-8"))


def _challenge_response(err: urllib.error.HTTPError, route: dict) -> dict:
    """Mirror a downstream 401/403 as an OAuth challenge toward the custom
    domain, so the client can discover the auth server and retry with a token."""
    rm = route.get("resource_metadata")
    if rm:
        www = f'Bearer error="invalid_token", resource_metadata="{rm}"'
    else:
        # No custom-domain PRM known for this route — relay the downstream one.
        try:
            www = err.headers.get("WWW-Authenticate")
        except Exception:  # noqa: BLE001
            www = None
    headers = {"content-type": "application/json", "cache-control": "no-store"}
    if www:
        headers["www-authenticate"] = www
    return {
        "statusCode": err.code,
        "headers": headers,
        "body": json.dumps({"error": "unauthorized"}),
    }


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(body),
    }


def handler(event, context):
    """Lambda Function URL handler (payload format 2.0)."""
    path = event.get("rawPath") or event.get("requestContext", {}).get("http", {}).get(
        "path", ""
    )
    route = match_route(path)
    if route is None:
        logger.warning("No discovery route for path %s", path)
        return _response(404, {"error": "not_found"})

    auth = (event.get("headers") or {}).get(FORWARDED_AUTH_HEADER)
    try:
        doc = _fetch_downstream(route["downstream_url"], auth)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            logger.info("Downstream %s challenged %s", e.code, route["downstream_url"])
            return _challenge_response(e, route)
        logger.error("Downstream fetch failed for %s: %s", route["downstream_url"], e)
        return _response(502, {"error": "downstream_unavailable"})
    except Exception as e:  # noqa: BLE001 — surface any fetch/parse failure as 502
        logger.error("Downstream fetch failed for %s: %s", route["downstream_url"], e)
        return _response(502, {"error": "downstream_unavailable"})

    result = apply_override(route["kind"], doc, route.get("overrides", {}))
    logger.info("Served %s discovery doc for %s", route["kind"], path)
    return _response(200, result)
