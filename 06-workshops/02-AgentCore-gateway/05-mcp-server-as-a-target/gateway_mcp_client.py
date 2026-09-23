"""Lightweight raw-HTTP client for AgentCore Gateway's MCP endpoint.

Used by the prompts/resources/streaming/sessions/elicitation demos in the
notebooks in this directory so the cells can stay focused on the MCP method
being demonstrated rather than transport plumbing (bearer auth,
MCP-Protocol-Version negotiation, JSON-RPC envelope, cross-target
pagination, SSE streaming, session id management).

SDK clients (Strands MCPClient, the official mcp client) negotiate the
protocol version automatically; raw `requests.post` does not — hence the
explicit `MCP-Protocol-Version` header. The default matches the version
the gateway is created with in `01-mcp-server-target.ipynb` (Step 2.3).

Pagination note: `tools/list` (and the other list methods) page **per
target**. With one DEFAULT target plus one DYNAMIC target attached to the
same gateway, the first call returns one target's items plus a
`nextCursor`; calling again with that cursor returns the next target's
items, and so on. The `list_all_*` helpers below follow `nextCursor`
until exhausted and return the merged list.

Streaming note: tools that emit `notifications/progress`, log messages, or
elicitation/sampling requests are read via `stream_tool_call(...)`, a
generator that yields each parsed JSON-RPC frame from the SSE response.
The buffered `call_tool` only works for tools that return a single result
without server-emitted intermediate frames.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterator, List, Optional

import requests

DEFAULT_PROTOCOL_VERSION = "2025-11-25"


class GatewayMCPClient:
    """Tiny client wrapping JSON-RPC POSTs to the gateway's MCP endpoint."""

    def __init__(
        self,
        gateway_url: str,
        get_token: Callable[[], str],
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        session_id: Optional[str] = None,
    ) -> None:
        """Construct a client.

        ``session_id`` (optional) — a client-supplied ``Mcp-Session-Id`` to
        echo on every request. Useful when the upstream MCP server runs
        ``stateless_http=True`` (no server-issued session id) but you still
        want AgentCore Runtime to pin the request to a specific microvm.
        Pair it with a target ``metadataConfiguration`` that allowlists
        ``Mcp-Session-Id`` for both request and response headers.

        If ``initialize()`` is later called and the gateway returns a
        session id, the captured value replaces this one.
        """
        self.gateway_url = gateway_url
        self._get_token = get_token
        self._protocol_version = protocol_version
        self._session_id: Optional[str] = session_id

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def set_session_id(self, session_id: Optional[str]) -> None:
        """Override the client-side ``Mcp-Session-Id`` that gets echoed on
        every subsequent request."""
        self._session_id = session_id

    def _headers(
        self, accept: str = "application/json, text/event-stream"
    ) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": accept,
            "MCP-Protocol-Version": self._protocol_version,
            "Authorization": f"Bearer {self._get_token()}",
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _rpc(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": method.replace("/", "-") + "-request",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        return requests.post(
            self.gateway_url, headers=self._headers(), json=payload, timeout=3600
        ).json()

    def rpc_raw(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> requests.Response:
        """Like :meth:`_rpc` but returns the raw ``requests.Response`` so the
        caller can inspect HTTP status, response headers, and the un-parsed
        body. Useful for diagnostic / error-contract probes (missing or
        invalid `Mcp-Session-Id`, etc.) where a non-2xx status is expected
        and ``response.json()`` would either succeed (with an error body) or
        fail to parse — in either case, the status code is the signal.
        """
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": method.replace("/", "-") + "-raw",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        return requests.post(
            self.gateway_url, headers=self._headers(), json=payload, timeout=3600
        )

    def _paginate(self, method: str, items_key: str) -> List[Dict[str, Any]]:
        """Follow ``result.nextCursor`` across pages and return merged items."""
        items: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params = {"cursor": cursor} if cursor else None
            resp = self._rpc(method, params)
            result = resp.get("result", {})
            items.extend(result.get(items_key, []))
            cursor = result.get("nextCursor")
            if not cursor:
                return items

    # --- Lifecycle ------------------------------------------------------

    def initialize(
        self,
        capabilities: Optional[Dict[str, Any]] = None,
        client_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send `initialize` then `notifications/initialized`.

        Captures `Mcp-Session-Id` from the response header (set on
        session-enabled gateways) so subsequent requests echo it back.
        """
        body = {
            "jsonrpc": "2.0",
            "id": "initialize-request",
            "method": "initialize",
            "params": {
                "protocolVersion": self._protocol_version,
                "capabilities": capabilities or {},
                "clientInfo": client_info
                or {"name": "GatewayMCPClient", "version": "0.1"},
            },
        }
        r = requests.post(
            self.gateway_url, headers=self._headers(), json=body, timeout=3600
        )
        sid = r.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid

        # `notifications/initialized` (no response body expected)
        requests.post(
            self.gateway_url,
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=3600,
        )

        try:
            result = r.json()
        except ValueError:
            result = {"raw": r.text}

        return {
            "session_id": sid,
            "protocol_version": r.headers.get("mcp-protocol-version"),
            "http_status": r.status_code,
            "result": result,
        }

    # --- Tools ----------------------------------------------------------

    def list_tools(self, cursor: Optional[str] = None) -> Dict[str, Any]:
        params = {"cursor": cursor} if cursor else None
        return self._rpc("tools/list", params)

    def list_all_tools(self) -> List[Dict[str, Any]]:
        """Return tools from every target, following per-target pagination."""
        return self._paginate("tools/list", "tools")

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Buffered tool invocation. Only safe for tools that return a single
        result with no server-emitted intermediate frames. For tools that
        emit progress, logging, elicitation, or sampling, use
        :meth:`stream_tool_call` instead.
        """
        return self._rpc("tools/call", {"name": name, "arguments": arguments})

    def call_tool_json_only(
        self,
        name: str,
        arguments: Dict[str, Any],
        request_id: Any = "tools-call-request",
    ) -> Dict[str, Any]:
        """Buffered tool invocation forcing `Accept: application/json` only.

        Demonstrates backward-compatibility with non-streaming clients on a
        gateway that has streaming enabled — the gateway returns a single
        JSON document instead of an SSE stream.

        Returns a dict with `http_status`, `content_type`, and `body` (raw
        response text — the caller can `json.loads` if appropriate).
        """
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        r = requests.post(
            self.gateway_url,
            headers=self._headers(accept="application/json"),
            json=body,
            timeout=3600,
        )
        return {
            "http_status": r.status_code,
            "content_type": r.headers.get("content-type"),
            "body": r.text,
        }

    def stream_tool_call(
        self,
        name: str,
        arguments: Dict[str, Any],
        progress_token: Optional[str] = None,
        request_id: Any = "tools-call-request",
    ) -> Iterator[Dict[str, Any]]:
        """Generator yielding parsed JSON-RPC frames from a streaming tools/call.

        Yields, in arrival order:
          - `notifications/progress` messages (when `progress_token` is set)
          - `notifications/message` log frames
          - `elicitation/create` / `sampling/createMessage` server-initiated requests
          - `notifications/elicitation/complete` server notifications
          - the final tool-result frame keyed by `request_id`

        Handles both transports the gateway can use:
          - `Content-Type: text/event-stream` — yields each `data:` line as a frame.
          - `Content-Type: application/json` — yields the single buffered JSON
            body (e.g., when the gateway returns a one-shot error response
            instead of opening an SSE channel).

        After the matching response frame is yielded the generator exits.
        """
        params: Dict[str, Any] = {"name": name, "arguments": arguments}
        if progress_token is not None:
            params["_meta"] = {"progressToken": progress_token}
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": params,
        }
        with requests.post(
            self.gateway_url,
            headers=self._headers(accept="text/event-stream"),
            json=body,
            stream=True,
            timeout=3600,
        ) as resp:
            ct = resp.headers.get("content-type", "")
            if ct.startswith("application/json"):
                # Single buffered JSON document (no SSE).
                try:
                    yield resp.json()
                except ValueError:
                    pass
                return
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                try:
                    yield json.loads(raw[5:].strip())
                except json.JSONDecodeError:
                    continue

    def _post_response(self, request_id: Any, result: Dict[str, Any]) -> int:
        """POST a JSON-RPC response back to the gateway. Used to reply to
        server-initiated requests (`elicitation/create`, `sampling/createMessage`)
        that arrive on the SSE stream of an in-flight `tools/call`.
        """
        r = requests.post(
            self.gateway_url,
            headers=self._headers(),
            json={"jsonrpc": "2.0", "id": request_id, "result": result},
            timeout=3600,
        )
        return r.status_code

    def call_tool_streaming(
        self,
        name: str,
        arguments: Dict[str, Any],
        *,
        elicitation_callback: Optional[
            Callable[[Dict[str, Any]], Dict[str, Any]]
        ] = None,
        sampling_callback: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        notification_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        progress_token: Optional[str] = None,
        request_id: Any = "tools-call-streaming",
    ) -> Dict[str, Any]:
        """Call a tool and dispatch any server-initiated requests to callbacks.

        Callbacks (all optional, all sync):

          - ``elicitation_callback(params: dict) -> dict``
            Invoked when the server emits ``elicitation/create``. Should
            return a dict like ``{"action": "accept", "content": {...}}`` for
            form mode, or ``{"action": "accept"}`` for URL mode.
          - ``sampling_callback(params: dict) -> dict``
            Invoked when the server emits ``sampling/createMessage``. Should
            return a ``CreateMessageResult``-shaped dict
            (``{"role": "assistant", "content": {...}, "model": "..."}``).
          - ``progress_callback(params: dict) -> None``
            Invoked for each ``notifications/progress`` frame.
          - ``notification_callback(method: str, params: dict) -> None``
            Invoked for any other ``notifications/*`` (e.g. ``message``,
            ``elicitation/complete``).

        Returns ``{"result": ..., "error": ...}`` for the final response keyed
        by ``request_id``.
        """
        for msg in self.stream_tool_call(
            name, arguments, progress_token=progress_token, request_id=request_id
        ):
            method = msg.get("method")
            msg_id = msg.get("id")
            if method == "elicitation/create" and elicitation_callback is not None:
                reply = elicitation_callback(msg.get("params") or {})
                self._post_response(msg_id, reply)
            elif method == "sampling/createMessage" and sampling_callback is not None:
                reply = sampling_callback(msg.get("params") or {})
                self._post_response(msg_id, reply)
            elif method == "notifications/progress" and progress_callback is not None:
                progress_callback(msg.get("params") or {})
            elif (
                isinstance(method, str)
                and method.startswith("notifications/")
                and notification_callback is not None
            ):
                notification_callback(method, msg.get("params") or {})
            elif msg_id == request_id:
                return {"result": msg.get("result"), "error": msg.get("error")}
        return {"result": None, "error": None}

    # --- Prompts --------------------------------------------------------

    def list_prompts(self, cursor: Optional[str] = None) -> Dict[str, Any]:
        params = {"cursor": cursor} if cursor else None
        return self._rpc("prompts/list", params)

    def list_all_prompts(self) -> List[Dict[str, Any]]:
        return self._paginate("prompts/list", "prompts")

    def get_prompt(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return self._rpc("prompts/get", {"name": name, "arguments": arguments})

    # --- Resources ------------------------------------------------------

    def list_resources(self, cursor: Optional[str] = None) -> Dict[str, Any]:
        params = {"cursor": cursor} if cursor else None
        return self._rpc("resources/list", params)

    def list_all_resources(self) -> List[Dict[str, Any]]:
        return self._paginate("resources/list", "resources")

    def read_resource(self, uri: str) -> Dict[str, Any]:
        return self._rpc("resources/read", {"uri": uri})

    def list_resource_templates(self, cursor: Optional[str] = None) -> Dict[str, Any]:
        params = {"cursor": cursor} if cursor else None
        return self._rpc("resources/templates/list", params)

    def list_all_resource_templates(self) -> List[Dict[str, Any]]:
        return self._paginate("resources/templates/list", "resourceTemplates")
