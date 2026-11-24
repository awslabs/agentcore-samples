"""One initialized, no-retry MCP confirmation through the operator's Gateway session.

The API has no MCP/Strands dependency. This small Streamable HTTP client sends the standard
handshake and a single bounded confirm_close request; it never accepts a model-supplied grant.
Transport MCP-Session-Id and AgentCore Policy session are distinct headers. Any uncertain
response is refused; the caller retires/fences that policy session before another admission.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

POLICY_HEADER = "x-amzn-bedrock-agentcore-policy-session-id"
PROTOCOL = "2025-11-25"  # mcp==2.1.1 LATEST_HANDSHAKE_VERSION, not its modern Discover revision
SUPPORTED = {"2024-11-05", "2025-03-26", "2025-06-18", PROTOCOL}
MAX_REPLY = 65536
SESSION = re.compile(r"^[A-Za-z0-9._/+:=-]{1,256}$")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # never forward an operator's bearer token to a redirected endpoint


class ProtocolError(Exception):
    pass


def _message(data: bytes, request_id: str) -> dict[str, Any] | None:
    try:
        row = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ProtocolError("malformed MCP response") from exc
    if not isinstance(row, dict) or row.get("jsonrpc") != "2.0":
        raise ProtocolError("malformed MCP JSON-RPC envelope")
    if "id" not in row:
        if row.get("method") == "notifications/progress":
            return None
        raise ProtocolError("unexpected MCP notification")
    if row["id"] != request_id:
        raise ProtocolError("MCP response belongs to another request")
    if "error" in row or not isinstance(row.get("result"), dict):
        raise ProtocolError("MCP request rejected or missing result")
    return row["result"]


def _sse_reply(response: Any, request_id: str, deadline: float) -> dict[str, Any]:
    """Stop at the first correlated terminal frame; an SSE stream need not close."""
    data: list[bytes] = []
    total = 0
    while time.monotonic() < deadline:
        line = response.readline(MAX_REPLY - total + 1)
        total += len(line)
        if total > MAX_REPLY:
            raise ProtocolError("MCP response exceeds bound")
        if not line:
            break
        if line.startswith(b"data:"):
            data.append(line[5:].strip())
        elif line.strip() == b"":
            if data:
                item = _message(b"\n".join(data), request_id)
                if item is not None:
                    return item
                data = []
        elif not (line.startswith(b":") or line.startswith((b"event:", b"id:", b"retry:"))):
            raise ProtocolError("malformed MCP SSE event")
    if data:
        item = _message(b"\n".join(data), request_id)
        if item is not None:
            return item
    raise ProtocolError("missing MCP response")


def _post(
    opener: Any,
    url: str,
    token: str,
    policy_session: str,
    transport_session: str | None,
    version: str | None,
    message: dict[str, Any],
    deadline: float,
) -> tuple[dict[str, Any] | None, str | None]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        POLICY_HEADER: policy_session,
    }
    if transport_session:
        headers["Mcp-Session-Id"] = transport_session
    if version:
        headers["MCP-Protocol-Version"] = version
    request = urllib.request.Request(
        url,
        data=json.dumps(message, separators=(",", ":")).encode(),
        headers=headers,
        method="POST",
    )
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        raise ProtocolError("MCP confirmation deadline exceeded")
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.getcode()
            if "id" not in message:
                if status not in (202, 204) or response.read(1):
                    raise ProtocolError("initialized notification not acknowledged")
                return None, None
            if status != 200:
                raise ProtocolError(f"MCP request HTTP {status}")
            session = response.headers.get("Mcp-Session-Id")
            if session is not None and not SESSION.fullmatch(session):
                raise ProtocolError("invalid MCP transport session header")
            if transport_session and session and transport_session != session:
                raise ProtocolError("MCP transport session changed")
            if transport_session is None and session and message.get("method") != "initialize":
                raise ProtocolError("MCP transport session appeared after initialization")
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type.startswith("application/json"):
                raw = response.read(MAX_REPLY + 1)
                if len(raw) > MAX_REPLY:
                    raise ProtocolError("MCP response exceeds bound")
                result = _message(raw, message["id"])
                if result is None:
                    raise ProtocolError("missing MCP response")
            elif content_type.startswith("text/event-stream"):
                result = _sse_reply(response, message["id"], deadline)
            else:
                raise ProtocolError("MCP response content type unsupported")
            return result, session
    except urllib.error.HTTPError as exc:
        raise ProtocolError(f"MCP request HTTP {exc.code}") from exc


def confirm_close(
    url: str, token: str, policy_session: str, name: str, args: dict[str, Any]
) -> tuple[bool, str]:
    """Fail closed on any malformed/foreign result; call the consequential tool at most once."""
    endpoint = urllib.parse.urlsplit(url)
    if (
        name != "TicketTools___confirm_close"
        or endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or not policy_session
        or not SESSION.fullmatch(policy_session)
        or not token
    ):
        return False, "invalid confirmation request"
    if (
        not isinstance(args, dict)
        or set(args) != {"customer_id", "ticket_id", "actor"}
        or not isinstance(args.get("actor"), dict)
        or set(args["actor"]) != {"user_sub", "customer_id"}
        or args["actor"].get("customer_id") != args.get("customer_id")
        or not all(
            isinstance(v, str) and v
            for v in (args.get("customer_id"), args.get("ticket_id"), args["actor"].get("user_sub"))
        )
    ):
        return False, "invalid confirmation arguments"
    opener = urllib.request.build_opener(NoRedirect())
    deadline = time.monotonic() + 30
    transport_session: str | None = None
    try:
        init_id = uuid.uuid4().hex
        init, transport_session = _post(
            opener,
            url,
            token,
            policy_session,
            None,
            None,
            {
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "ticketops-operator-console", "version": "1.0"},
                },
            },
            deadline,
        )
        version = init.get("protocolVersion") if init else None
        if (
            version not in SUPPORTED
            or not isinstance(init.get("capabilities"), dict)
            or not isinstance(init.get("serverInfo"), dict)
            or not isinstance(init["serverInfo"].get("name"), str)
        ):
            raise ProtocolError("MCP initialize negotiation incomplete")
        _post(
            opener,
            url,
            token,
            policy_session,
            transport_session,
            version,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            deadline,
        )
        list_id = uuid.uuid4().hex
        listed, _ = _post(
            opener,
            url,
            token,
            policy_session,
            transport_session,
            version,
            {"jsonrpc": "2.0", "id": list_id, "method": "tools/list"},
            deadline,
        )
        tools = listed.get("tools") if listed else None
        if (
            not isinstance(tools, list)
            or sum(isinstance(t, dict) and t.get("name") == name for t in tools) != 1
        ):
            raise ProtocolError("confirmation tool not uniquely listed")
        schema = next(
            t.get("inputSchema") for t in tools if isinstance(t, dict) and t.get("name") == name
        )
        properties = schema.get("properties") if isinstance(schema, dict) else None
        actor_schema = properties.get("actor") if isinstance(properties, dict) else None
        actor_fields = actor_schema.get("properties") if isinstance(actor_schema, dict) else None
        if (
            not isinstance(schema, dict)
            or schema.get("type") != "object"
            or not isinstance(properties, dict)
            or not isinstance(schema.get("required"), list)
            or not {"customer_id", "ticket_id", "actor"} <= set(schema["required"])
            or any(
                not isinstance(properties.get(k), dict) or properties[k].get("type") != "string"
                for k in ("customer_id", "ticket_id")
            )
            or not isinstance(actor_schema, dict)
            or actor_schema.get("type") != "object"
            or not isinstance(actor_fields, dict)
            or any(
                not isinstance(actor_fields.get(k), dict) or actor_fields[k].get("type") != "string"
                for k in ("user_sub", "customer_id")
            )
        ):
            raise ProtocolError("confirmation tool schema mismatch")
        call_id = uuid.uuid4().hex
        outcome, _ = _post(
            opener,
            url,
            token,
            policy_session,
            transport_session,
            version,
            {
                "jsonrpc": "2.0",
                "id": call_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            },
            deadline,
        )
        if outcome.get("isError", False) is not False:
            raise ProtocolError("confirmation tool rejected")
        content = outcome.get("content")
        if (
            not isinstance(content, list)
            or len(content) != 1
            or not isinstance(content[0], dict)
            or content[0].get("type") != "text"
            or not isinstance(content[0].get("text"), str)
        ):
            raise ProtocolError("confirmation tool result malformed")
        record = json.loads(content[0]["text"])
        if isinstance(record, str):
            record = json.loads(record)
        if (
            not isinstance(record, dict)
            or record.get("status") != "CONFIRMED"
            or record.get("customer_id") != args["customer_id"]
            or record.get("ticket_id") != args["ticket_id"]
            or record.get("confirmed_by") != args["actor"]
        ):
            raise ProtocolError("confirmation result target or actor mismatch")
        return True, "confirmed exact owned ticket through initialized MCP session"
    except (ProtocolError, ValueError, UnicodeError) as exc:
        return False, str(exc)[:160]
    # Transport exceptions deliberately propagate. The caller's existing retirement/UNKNOWN
    # fencing handles a request whose external commitment cannot be inferred from its reply.
