"""Send MCP JSON-RPC requests to an AgentCore Runtime (client side).

McpClient uses boto3 invoke_agent_runtime (IAM auth); sdk_http_client signs the MCP Python SDK v2 client's
requests with SigV4. MCP 2026-07-28 has no initialize handshake: each request carries its protocol version
and client capabilities in params._meta, and the MCP-Protocol-Version, Mcp-Method, and Mcp-Name headers
(boto3 sends them as mcpProtocolVersion, mcpMethod, and mcpName).
"""

from __future__ import annotations

import itertools
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import boto3
import httpx2
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError

LATEST = "2026-07-28"
EARLIER = "2025-11-25"
SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
_ids = itertools.count(1)


def new_session_id() -> str:
    return f"mcp-e2e-{uuid.uuid4().hex}"  # runtimeSessionId needs at least 33 characters


@dataclass
class Reply:
    status: int
    message: dict[str, Any]  # the JSON-RPC response: {"result": ...} or {"error": ...}
    notifications: list[dict[str, Any]] = field(default_factory=list)  # e.g. notifications/progress

    @property
    def result(self) -> dict[str, Any]:
        return self.message.get("result", {})

    @property
    def error(self) -> dict[str, Any] | None:
        return self.message.get("error")

    @property
    def structured(self) -> Any:
        return self.result.get("structuredContent")


def parse(body: str) -> list[dict[str, Any]]:
    """Return the JSON-RPC messages in a JSON body or a server-sent-events stream."""
    if body.lstrip().startswith("{"):
        return [json.loads(body)]
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:") and line[5:].strip()]


def request_body(
    method: str,
    params: dict[str, Any] | None,
    version: str = LATEST,
    capabilities: dict[str, Any] | None = None,
    progress_token: str | None = None,
) -> dict[str, Any]:
    """A JSON-RPC request. For 2026-07-28 it carries the protocol version and client capabilities in _meta."""
    params = dict(params or {})
    if version == LATEST:
        params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": LATEST,
            "io.modelcontextprotocol/clientInfo": {"name": "mcp-e2e-invoke", "version": "1.0.0"},
            "io.modelcontextprotocol/clientCapabilities": capabilities or {},
            **params.get("_meta", {}),
        }
    if progress_token:
        params.setdefault("_meta", {})["progressToken"] = progress_token
    return {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params}


class McpClient:
    """One MCP client for one agent runtime. Pass session_id to keep requests on one runtime session."""

    def __init__(
        self, runtime_arn: str, region: str, session_id: str | None = None, capabilities: dict[str, Any] | None = None
    ) -> None:
        self.arn = runtime_arn
        self.session_id = session_id
        self.capabilities = capabilities or {}
        self.boto = boto3.client("bedrock-agentcore", region_name=region)
        # boto3 raises ClientError with an empty message for HTTP 4xx/5xx; keep the body, which holds the
        # JSON-RPC error (2026-07-28 errors keep their HTTP status, for example 400 for -32602).
        self._error_body = ""
        self.boto.meta.events.register("after-call.bedrock-agentcore.InvokeAgentRuntime", self._keep_error_body)

    def _keep_error_body(self, http_response: Any = None, **_: Any) -> None:
        if http_response is not None and http_response.status_code >= 400:
            self._error_body = http_response.content.decode(errors="replace")

    def _invoke_args(
        self, method: str, params: dict[str, Any] | None, name: str | None, version: str, progress_token: str | None
    ) -> dict[str, Any]:
        body = request_body(method, params, version, self.capabilities, progress_token)
        headers: dict[str, Any] = {"mcpMethod": method}
        if method != "initialize":
            # Every 2026-07-28 request carries the version; an earlier client sends it after initialize.
            headers["mcpProtocolVersion"] = version
        if name:
            headers["mcpName"] = name
        if self.session_id:
            headers["runtimeSessionId"] = self.session_id
        return {
            "agentRuntimeArn": self.arn,
            "payload": json.dumps(body).encode(),
            "contentType": "application/json",
            "accept": "application/json, text/event-stream",
            **headers,
        }

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        name: str | None = None,
        version: str = LATEST,
        progress_token: str | None = None,
    ) -> Reply:
        """Send one request and return the reply. JSON-RPC errors are returned, not raised."""
        self._error_body = ""
        try:
            response = self.boto.invoke_agent_runtime(
                **self._invoke_args(method, params, name, version, progress_token)
            )
        except ClientError as error:
            status = error.response["ResponseMetadata"]["HTTPStatusCode"]
            try:
                messages = parse(self._error_body)
            except ValueError:
                messages = []
            if not messages:  # not a JSON-RPC body, for example an IAM or throttling error
                raise
            return Reply(status, messages[-1])
        messages = parse(response["response"].read().decode())
        if not messages:
            raise RuntimeError(f"{method}: empty response")
        return Reply(response.get("statusCode", 200), messages[-1], messages[:-1])

    def stream(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        name: str | None = None,
        progress_token: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Send one request and yield its JSON-RPC messages as they arrive: notifications, then the response.

        Stopping early closes the response stream, which is how an MCP 2026-07-28 client cancels a request.
        """
        response = self.boto.invoke_agent_runtime(**self._invoke_args(method, params, name, LATEST, progress_token))
        body = response["response"]
        try:
            # The default chunk size (1 KB) waits for a full chunk, holding back small events like progress.
            for line in body.iter_lines(chunk_size=1):
                if line.startswith(b"data:") and line[5:].strip():
                    yield json.loads(line[5:])
                elif line.lstrip().startswith(b"{"):
                    yield json.loads(line)
        finally:
            body.close()

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None, **extra: Any) -> Reply:
        progress_token = extra.pop("progress_token", None)
        version = extra.pop("version", LATEST)
        return self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}, **extra},
            name=name,
            version=version,
            progress_token=progress_token,
        )

    def initialize(self) -> Reply:
        """The MCP 2025-11-25 handshake, for showing that the same server still answers earlier clients."""
        return self.request(
            "initialize",
            {
                "protocolVersion": EARLIER,
                "capabilities": self.capabilities,
                "clientInfo": {"name": "mcp-e2e-invoke", "version": "1.0.0"},
            },
            version=EARLIER,
        )


def show(label: str, reply: Reply, keys: tuple[str, ...] | None = None) -> None:
    """Print one exchange compactly."""
    if reply.error:
        print(f"── {label}  HTTP {reply.status}  error {reply.error['code']}: {reply.error['message']}")
        return
    shown = reply.structured if reply.structured is not None else reply.result
    if keys and isinstance(shown, dict):
        shown = {k: shown.get(k) for k in keys}
    text = json.dumps(shown, default=str)
    print(f"── {label}  HTTP {reply.status}\n   {text[:400]}{'…' if len(text) > 400 else ''}")


# ---------------------------------------------------------------- MCP Python SDK v2 client over SigV4
def runtime_url(runtime_arn: str, region: str) -> str:
    return f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{quote(runtime_arn, safe='')}/invocations?qualifier=DEFAULT"


# httpx2 sets these itself after signing, so they stay out of the signature.
UNSIGNED = frozenset({"authorization", "host", "content-length", "connection", "user-agent", "accept-encoding"})


class SigV4(httpx2.Auth):
    """Sign each request the MCP SDK transport sends with your AWS credentials (IAM inbound auth)."""

    requires_request_body = True

    def __init__(self, region: str) -> None:
        self.region = region
        self.credentials = boto3.Session().get_credentials()

    def auth_flow(self, request: httpx2.Request):  # type: ignore[override]
        signed = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers={k: v for k, v in request.headers.items() if k.lower() not in UNSIGNED},
        )
        SigV4Auth(self.credentials.get_frozen_credentials(), "bedrock-agentcore", self.region).add_auth(signed)
        request.headers.update(dict(signed.headers))
        yield request


def sdk_http_client(region: str, session_id: str | None = None) -> httpx2.AsyncClient:
    """An httpx2 client for mcp.client.streamable_http_client, signed with SigV4."""
    headers = {SESSION_HEADER: session_id} if session_id else {}
    return httpx2.AsyncClient(headers=headers, auth=SigV4(region), timeout=httpx2.Timeout(30, read=300))
