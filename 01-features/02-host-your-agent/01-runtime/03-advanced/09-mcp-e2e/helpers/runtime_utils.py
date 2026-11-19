"""Server-side helpers shared by the MCP servers in this folder. deploy.py copies this file into each zip.

AgentCore Runtime with platformVersion="V2" snapshots the server after import and restores new microVMs
from that snapshot (we have seen one runtime version use more than one). Anything created at import (a
random ID, a key, a timestamp, a cached secret) is therefore frozen into the snapshot and shared by many
microVMs. These helpers create such values on first use instead. Reusable setup such as boto3 clients
belongs at import, as the Runtime V2 guide recommends.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Any

import uvicorn
from mcp.server import MCPServer
from mcp.server.mcpserver import RequestStateSecurity
from mcp.server.request_state import AESGCMRequestStateCodec, InvalidRequestState
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Loopback by default. deploy.py sets MCP_HOST=0.0.0.0, which AgentCore Runtime requires; the runtime
# publishes no port and authenticates every request before it reaches the server.
HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8000"))

_instance: dict[str, Any] = {}


def instance_id() -> str:
    """A per-microVM ID, created on the first request (an ID created at import would be shared)."""
    if not _instance:
        _instance["id"] = uuid.uuid4().hex[:12]
    return _instance["id"]


class Health:
    """Status reported by GET /ping. AgentCore Runtime keeps a session alive while it reports HealthyBusy."""

    def __init__(self) -> None:
        self.busy = 0
        # When the status last changed. Set on the first ping, not here: this object is created at import, so a
        # time taken now would be the snapshot's time.
        self.changed_at: int | None = None

    @property
    def status(self) -> str:
        return "HealthyBusy" if self.busy else "Healthy"

    def job_started(self) -> None:
        self._set_busy(self.busy + 1)

    def job_finished(self) -> None:
        self._set_busy(max(0, self.busy - 1))

    def _set_busy(self, busy: int) -> None:
        before = self.status
        self.busy = busy
        if self.status != before:  # only on a change: a time that moves on every ping keeps idle sessions alive
            self.changed_at = int(time.time())

    def ping(self) -> dict[str, str | int]:
        if self.changed_at is None:
            self.changed_at = int(time.time())
        return {"status": self.status, "time_of_last_update": self.changed_at}


health = Health()


def add_ping(mcp: MCPServer) -> None:
    """Serve GET /ping. AgentCore Runtime uses it for health checks instead of the MCP ping method.

    Keep time_of_last_update: on Runtime V2, a HealthyBusy status without it did not keep an idle session alive.
    """

    @mcp.custom_route("/ping", methods=["GET"])
    async def ping(request: Request) -> JSONResponse:
        return JSONResponse(health.ping())


class SecretsManagerCodec:
    """Seals MRTR requestState with a key from AWS Secrets Manager, read on first use.

    Every microVM must use the same key, or a retry that reaches another microVM fails with -32602
    "Invalid or expired requestState". Reading the key on first use, not at import, keeps it out of the
    V2 snapshot. The ring holds the current and the previous version of the secret, so state sealed
    before a rotation still opens; a token sealed with a key this microVM hasn't loaded yet makes it
    reload the secret, at most once a minute.
    """

    RELOAD_SECONDS = 60

    def __init__(self, secret_arn: str) -> None:
        self.secret_arn = secret_arn
        self._codec: AESGCMRequestStateCodec | None = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()  # sync tools run in worker threads

    def _load(self) -> AESGCMRequestStateCodec:
        import boto3

        client = boto3.client("secretsmanager")
        keys = [client.get_secret_value(SecretId=self.secret_arn)["SecretString"]]  # AWSCURRENT seals
        try:
            previous = client.get_secret_value(SecretId=self.secret_arn, VersionStage="AWSPREVIOUS")
            if previous["SecretString"] not in keys:
                keys.append(previous["SecretString"])
        except client.exceptions.ResourceNotFoundException:
            pass  # the secret has never been rotated
        self._loaded_at = time.monotonic()
        return AESGCMRequestStateCodec(keys)

    def _get(self, reload: bool = False) -> AESGCMRequestStateCodec:
        with self._lock:
            if self._codec is None or reload:
                self._codec = self._load()
            return self._codec

    def seal(self, payload: bytes) -> str:
        return self._get().seal(payload)

    def unseal(self, token: str) -> bytes:
        try:
            return self._get().unseal(token)
        except InvalidRequestState:
            if time.monotonic() - self._loaded_at < self.RELOAD_SECONDS:
                raise
            return self._get(reload=True).unseal(token)  # perhaps sealed with a key rotated in since


def request_state_security() -> RequestStateSecurity:
    """The requestState policy: the shared Secrets Manager key when deployed, a process key locally."""
    secret_arn = os.getenv("REQUEST_STATE_SECRET_ARN")
    if secret_arn:
        return RequestStateSecurity(codec=SecretsManagerCodec(secret_arn))
    logger.warning("REQUEST_STATE_SECRET_ARN is not set: using a process-local requestState key (local runs only)")
    return RequestStateSecurity.ephemeral()


def serve(mcp: MCPServer) -> None:
    """Run the server on the AgentCore Runtime MCP contract: port 8000, path /mcp.

    stateless_http applies to MCP 2025-11-25 and earlier clients; 2026-07-28 is always stateless.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = mcp.streamable_http_app(stateless_http=True, host=HOST)
    uvicorn.run(app, host=HOST, port=PORT)
