"""
Shared helpers for talking to Microsoft Graph.

Used by every setup step that touches Entra (02, 02a, 02b, 04b) plus
cleanup.py and diagnose_entra_apps.py. Prefixed with an underscore
because it's not a runnable script.

Graph token acquisition strategy (in order):
  1. GRAPH_ACCESS_TOKEN env var (escape hatch - normally left blank).
  2. `az account get-access-token --resource https://graph.microsoft.com`
     using the current az CLI login.
  3. Fail with a clear error message.

This design keeps secrets out of .env - the automated path uses `az
login` once per session, and each setup script fetches a fresh token
on demand.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import requests

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def get_graph_token() -> str:
    """Return a Microsoft Graph access token.

    Prefers GRAPH_ACCESS_TOKEN from the environment; falls back to
    `az account get-access-token`. Exits with a helpful message if
    neither works.
    """
    tok = (os.environ.get("GRAPH_ACCESS_TOKEN") or "").strip()
    if tok:
        return tok

    try:
        result = subprocess.run(
            [
                "az",
                "account",
                "get-access-token",
                "--resource",
                "https://graph.microsoft.com",
                "--query",
                "accessToken",
                "-o",
                "tsv",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print(
            "✗ `az` CLI not found and GRAPH_ACCESS_TOKEN is not set.\n"
            "  Options:\n"
            "    1. Install Azure CLI and run `az login` once, OR\n"
            "    2. Set GRAPH_ACCESS_TOKEN in .env to a bearer token, OR\n"
            "    3. Run `python setup/print_manual_entra_instructions.py`\n"
            "       to configure Entra by hand.",
            file=sys.stderr,
        )
        sys.exit(1)

    if result.returncode != 0:
        print(
            f"✗ `az account get-access-token` failed:\n"
            f"  {result.stderr.strip() or '(no stderr)'}\n"
            f"  Are you logged in? Try: az login",
            file=sys.stderr,
        )
        sys.exit(1)

    token = result.stdout.strip()
    if not token:
        print(
            "✗ `az account get-access-token` returned an empty token.\n  Try re-running `az login`.",
            file=sys.stderr,
        )
        sys.exit(1)
    return token


def get_tenant_id() -> str:
    """Return the tenant ID of the current az session, if any.

    Best-effort - returns an empty string if az is unavailable or
    unauthenticated. Callers should fall back to ENTRA_TENANT_ID from
    .env when this returns empty.
    """
    try:
        result = subprocess.run(
            [
                "az",
                "account",
                "show",
                "--query",
                "tenantId",
                "-o",
                "tsv",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _headers(token: str, extra: dict | None = None) -> dict:
    h = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _print_graph_error(action: str, resp: requests.Response) -> None:
    print(f"✗ {action} → HTTP {resp.status_code}", file=sys.stderr)
    try:
        body = resp.json()
        # Graph errors have {"error": {"code": ..., "message": ...}}.
        err = body.get("error") if isinstance(body, dict) else None
        if err:
            print(f"  code:    {err.get('code')}", file=sys.stderr)
            print(f"  message: {err.get('message')}", file=sys.stderr)
        else:
            print(f"  body:    {resp.text[:600]}", file=sys.stderr)
    except (ValueError, KeyError):
        print(f"  body:    {resp.text[:600]}", file=sys.stderr)


def graph_get(path: str, token: str, params: dict | None = None) -> dict | list:
    """GET /v1.0/{path}. Raises on non-2xx after printing a Graph error."""
    resp = requests.get(
        f"{GRAPH_BASE}/{path.lstrip('/')}",
        headers=_headers(token),
        params=params,
        timeout=15,
    )
    if resp.status_code >= 400:
        _print_graph_error(f"GET {path}", resp)
        resp.raise_for_status()
    return resp.json()


def graph_post(path: str, token: str, body: dict, *, expect_status: tuple[int, ...] = (200, 201)) -> dict:
    """POST /v1.0/{path}. Returns the response body if status matches."""
    resp = requests.post(
        f"{GRAPH_BASE}/{path.lstrip('/')}",
        headers=_headers(token),
        data=json.dumps(body),
        timeout=30,
    )
    if resp.status_code not in expect_status:
        _print_graph_error(f"POST {path}", resp)
        resp.raise_for_status()
    if resp.text:
        try:
            return resp.json()
        except ValueError:
            return {}
    return {}


def graph_patch(path: str, token: str, body: dict, *, expect_status: tuple[int, ...] = (200, 204)) -> None:
    """PATCH /v1.0/{path}. Raises if the status is unexpected."""
    resp = requests.patch(
        f"{GRAPH_BASE}/{path.lstrip('/')}",
        headers=_headers(token),
        data=json.dumps(body),
        timeout=30,
    )
    if resp.status_code not in expect_status:
        _print_graph_error(f"PATCH {path}", resp)
        resp.raise_for_status()


def graph_delete(path: str, token: str, *, expect_status: tuple[int, ...] = (200, 204, 404)) -> int:
    """DELETE /v1.0/{path}. 404 is treated as success (idempotent teardown)."""
    resp = requests.delete(
        f"{GRAPH_BASE}/{path.lstrip('/')}",
        headers=_headers(token),
        timeout=15,
    )
    if resp.status_code not in expect_status:
        _print_graph_error(f"DELETE {path}", resp)
        resp.raise_for_status()
    return resp.status_code


def find_application_by_display_name(display_name: str, token: str) -> dict | None:
    """Return the first /applications/... whose displayName equals display_name."""
    resp = graph_get(
        "applications",
        token,
        params={
            "$filter": f"displayName eq '{display_name}'",
            "$select": "id,appId,displayName,identifierUris,keyCredentials",
        },
    )
    apps = resp.get("value") if isinstance(resp, dict) else None
    if not apps:
        return None
    return apps[0]


def find_service_principal_by_app_id(app_id: str, token: str) -> dict | None:
    resp = graph_get(
        "servicePrincipals",
        token,
        params={
            "$filter": f"appId eq '{app_id}'",
            "$select": "id,appId,displayName",
        },
    )
    sps = resp.get("value") if isinstance(resp, dict) else None
    if not sps:
        return None
    return sps[0]
