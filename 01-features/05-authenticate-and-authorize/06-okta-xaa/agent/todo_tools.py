"""Strands tools that call the todo resource API using XAA-brokered tokens.

The agent's entrypoint stores the caller's Okta ID token in a context variable
before running the model. Each tool exchanges that ID token (via the XAA two-leg
flow) for a resource access token and calls the API on the user's behalf.

Two ways to reach the resource API (RESOURCE_CALL_MODE):
  * "http"   (default, local dev): plain HTTPS to RESOURCE_API_URL with the
             access token as `Authorization: Bearer …`.
  * "lambda" (deployed): the resource app runs on AWS Lambda. We call it with
             `lambda:InvokeFunction` (RESOURCE_LAMBDA_NAME) — no public Function
             URL needed — passing a synthetic HTTP-API v2.0 event that the
             Lambda's Mangum handler parses like a normal request. The Okta
             access token rides in the `X-Resource-Token` header. boto3 signs
             the InvokeFunction call with the runtime's execution role.

The resource app validates the Okta access token on top of AWS IAM either way.
"""

from __future__ import annotations

import contextvars
import json
import os

import httpx
from strands import tool
from xaa_client import XaaConfig, get_resource_access_token

# Set per-request by the agent entrypoint.
current_id_token: contextvars.ContextVar[str] = contextvars.ContextVar("current_id_token")

_cfg = XaaConfig.from_env()
_MODE = os.environ.get("RESOURCE_CALL_MODE", "http")
_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
_LAMBDA_NAME = os.environ.get("RESOURCE_LAMBDA_NAME", "")


class _Result:
    """Minimal response shim (status + parsed JSON) for both call modes."""

    def __init__(self, status_code: int, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


def _call_http(method: str, path: str, body: bytes | None, access_token: str) -> _Result:
    headers = {"Authorization": f"Bearer {access_token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    resp = httpx.request(method, f"{_cfg.resource_api_url}{path}", content=body, headers=headers, timeout=30)
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = {}
    return _Result(resp.status_code, data)


def _call_lambda(method: str, path: str, body: bytes | None, access_token: str) -> _Result:
    """Invoke the resource Lambda directly, passing an HTTP-API v2.0 event."""
    import boto3

    headers = {"x-resource-token": f"Bearer {access_token}"}
    if body is not None:
        headers["content-type"] = "application/json"
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": headers,
        "requestContext": {
            "http": {"method": method, "path": path, "protocol": "HTTP/1.1", "sourceIp": "127.0.0.1"},
            "stage": "$default",
            "requestId": "agentcore-invoke",
        },
        "isBase64Encoded": False,
    }
    if body is not None:
        event["body"] = body.decode()

    client = boto3.client("lambda", region_name=_REGION)
    resp = client.invoke(FunctionName=_LAMBDA_NAME, Payload=json.dumps(event).encode())
    payload = json.loads(resp["Payload"].read() or b"{}")
    status = int(payload.get("statusCode", 502))
    raw = payload.get("body", "")
    try:
        data = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        data = {"raw": raw}
    return _Result(status, data)


def _request(method: str, path: str, json_body: dict | None = None) -> _Result:
    access_token = get_resource_access_token(_cfg, current_id_token.get())
    body = json.dumps(json_body).encode() if json_body is not None else None
    if _MODE == "lambda":
        return _call_lambda(method, path, body, access_token)
    return _call_http(method, path, body, access_token)


def _raise_for_status(r: _Result) -> None:
    if r.status_code >= 400:
        raise RuntimeError(f"resource API error {r.status_code}: {r.json()}")


@tool
def list_todos() -> str:
    """List the current user's todo items."""
    r = _request("GET", "/todos")
    _raise_for_status(r)
    todos = r.json().get("todos", [])
    if not todos:
        return "You have no todos."
    lines = [f"- {'[done] ' if t['done'] else ''}{t['title']} (id={t['id']})" for t in todos]
    return "Your todos:\n" + "\n".join(lines)


@tool
def add_todo(title: str) -> str:
    """Add a new todo item with the given title."""
    r = _request("POST", "/todos", {"title": title})
    _raise_for_status(r)
    return f"Added todo: {r.json()['title']}"


@tool
def complete_todo(todo_id: str) -> str:
    """Mark the todo with the given id as done."""
    r = _request("PATCH", f"/todos/{todo_id}", {"done": True})
    if r.status_code == 404:
        return f"No todo found with id {todo_id}."
    _raise_for_status(r)
    return f"Marked '{r.json()['title']}' as done."
