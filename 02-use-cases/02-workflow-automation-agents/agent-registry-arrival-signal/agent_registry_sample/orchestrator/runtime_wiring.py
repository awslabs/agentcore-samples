# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U5 runtime wiring: builds the collaborators the on_arrival_event path
needs, from environment/config, at invocation time.

Kept separate from event_path.py's pure logic so the logic is testable with
injected doubles (the tests never construct live AWS clients or a live a2a-sdk
client). This module is the production wiring:

  - the agent-registry data-plane client (boto3 >= 1.43.116) for batch_get
    (never bedrock-agentcore for the registry read);
  - U1's idempotency DynamoDB table resource (name from env);
  - the allowed peer accounts (own account, plus PEER_ALLOWED_ACCOUNTS);
  - the A2A send callable (SigV4 InvokeAgentRuntime through boto3).

A2A-auth path: an AgentCore Runtime agent is not reachable by a plain HTTPS
POST to a public A2A host. Its only endpoint is the SigV4-gated
bedrock-agentcore `.../runtimes/<arn>/invocations` binding, and a plain GET on
it (such as the a2a-sdk A2ACardResolver card fetch) returns HTTP 405. The
invocation goes through the `bedrock-agentcore` InvokeAgentRuntime API, which
boto3 SigV4-signs automatically. Authorization comes from the peer's
resource-based policy, which names the orchestrator's execution role (the
consumer-group model). The orchestrator's own identity policy carries no
InvokeAgentRuntime grant. The service client is called directly, with no
separate HTTP client.
"""

from __future__ import annotations

import json
import os
from urllib.parse import unquote

import boto3

# Env var names the AgentCore Runtime deployment supplies (wired at deploy).
_IDEMPOTENCY_TABLE_ENV = "IDEMPOTENCY_TABLE_NAME"
_ALLOWED_ACCOUNTS_ENV = "PEER_ALLOWED_ACCOUNTS"  # comma-separated AWS account ids
_REGION_ENV = "AWS_REGION"


def _data_plane_client():
    """The agent-registry data-plane client (batch_get read-back).

    boto3 >= 1.43.116 ships this client. Never the preview bedrock-agentcore
    namespace for the registry read.
    """
    region = os.environ.get(_REGION_ENV, "us-east-1")
    return boto3.client("agent-registry", region_name=region)


def _idempotency_table():
    """U1's idempotency DynamoDB table (recordId PK, state SK, TTL attr
    expiresAt), by name from the deploy-wired env var."""
    region = os.environ.get(_REGION_ENV, "us-east-1")
    table_name = os.environ[_IDEMPOTENCY_TABLE_ENV]
    return boto3.resource("dynamodb", region_name=region).Table(table_name)


def _own_account() -> str | None:
    """This runtime's own AWS account id (via STS). The secure default trust
    boundary is that an agent may invoke peers in its own account."""
    region = os.environ.get(_REGION_ENV, "us-east-1")
    try:
        return boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    except Exception:  # noqa: BLE001 - STS unreachable: rely on the env var alone
        return None


def _allowed_accounts() -> set[str]:
    """The set of peer AWS account ids this orchestrator may invoke.

    Every AgentCore runtime shares one host, so a host allowlist would allow
    everything. The account is the identity that varies. Defaults to the
    orchestrator's own account (trust same-account peers). PEER_ALLOWED_ACCOUNTS
    (comma-separated) widens it to additional accounts.
    """
    accounts = {
        a.strip() for a in os.environ.get(_ALLOWED_ACCOUNTS_ENV, "").split(",") if a.strip()
    }
    own = _own_account()
    if own:
        accounts.add(own)
    return accounts


def _runtime_arn_from_invocations_url(url: str) -> str:
    """Extract the agentRuntimeArn from an AgentCore invocations URL
    (.../runtimes/<url-encoded-arn>/invocations). The ARN is the URL-encoded
    path segment between /runtimes/ and /invocations."""
    marker = "/runtimes/"
    if marker in url and "/invocations" in url:
        seg = url.split(marker, 1)[1].split("/invocations", 1)[0]
        return unquote(seg)
    # Already an ARN (e.g. a card that stored the bare ARN), so return as-is.
    return url


def _build_a2a_send():
    """Build the A2A send callable backed by the bedrock-agentcore
    InvokeAgentRuntime API.

    Returns a synchronous callable (url, MessageSendParams) -> response. The
    `url` is the peer's invocations endpoint (from its registry card). The
    callable derives the agentRuntimeArn from it and calls invoke_agent_runtime
    with the A2A JSON-RPC message/send envelope as the payload. boto3
    SigV4-signs the request (no custom httpx auth). The response body is decoded
    and returned as {"parts": [{"text": <reply>}]} so the shared helper's
    _extract_text reads it unchanged (the same seam U6 and test doubles use).
    """
    region = os.environ.get(_REGION_ENV, "us-east-1")
    client = boto3.client("bedrock-agentcore", region_name=region)

    def send(url: str, params):
        arn = _runtime_arn_from_invocations_url(url)
        # Build the A2A JSON-RPC message/send envelope from the MessageSendParams
        # the shared helper constructed (the peer serves A2A via serve_a2a).
        msg = params.message
        text = ""
        for part in getattr(msg, "parts", []) or []:
            root = getattr(part, "root", part)
            text = getattr(root, "text", None) or text
        rpc = {
            "jsonrpc": "2.0",
            "id": getattr(msg, "message_id", None) or "1",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": text}],
                    "messageId": getattr(msg, "message_id", None) or "1",
                }
            },
        }
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            payload=json.dumps(rpc).encode("utf-8"),
            contentType="application/json",
            accept="application/json",
        )
        return {"parts": [{"text": _decode_invoke_response(resp)}]}

    return send


def _decode_invoke_response(resp: dict) -> str:
    """Decode the InvokeAgentRuntime response body to the reply text.

    Handles a StreamingBody of JSON-RPC, a plain JSON object, SSE lines, or raw
    text. Returns the best-effort text."""
    body = resp.get("response")
    raw = body.read() if hasattr(body, "read") else body
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    raw = (raw or "").strip()
    # Try JSON (JSON-RPC result or a wrapped object).
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        # Possibly SSE, so take the last data line. Otherwise return raw text.
        data_lines = [ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:")]
        if data_lines:
            try:
                obj = json.loads(data_lines[-1])
            except ValueError:
                return data_lines[-1]
        else:
            return raw
    # Look for text in JSON-RPC result message parts, a {"response": "..."}
    # wrapper, or a bare string.
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        if isinstance(obj.get("response"), str):
            return obj["response"]
        result = obj.get("result", obj)
        if isinstance(result, dict):
            # For an A2A task result, the final assembled reply is in
            # result.artifacts[].parts[].text. Collect and join all text parts
            # across artifacts (in practice the first artifact's single part
            # carries the full text).
            texts: list[str] = []
            for artifact in result.get("artifacts", []) or []:
                for part in (artifact.get("parts", []) if isinstance(artifact, dict) else []) or []:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        texts.append(part["text"])
            if texts:
                return "".join(texts)
            # Fallbacks: a direct message/send result (result.parts) or a
            # wrapped message (result.message.parts).
            for part in result.get("parts", []) or []:
                if isinstance(part, dict) and "text" in part:
                    return part["text"]
            msg = result.get("message") or {}
            for part in (msg.get("parts") if isinstance(msg, dict) else []) or []:
                if isinstance(part, dict) and "text" in part:
                    return part["text"]
        if isinstance(result, str):
            return result
    return raw


def build_event_path_collaborators() -> dict:
    """Assemble the collaborators on_arrival_event_impl needs, from the
    deploy-wired environment. Called by the on_arrival_event tool at
    invocation time."""
    return {
        "ddb_table": _idempotency_table(),
        "data_plane_client": _data_plane_client(),
        "allowed_accounts": _allowed_accounts(),
        "send_message": _build_a2a_send(),
    }


# Env var U6 (search-path) adds, the registry id the search is scoped to
# (registryIds = [this one]), supplied at deploy time. The allowed accounts and
# the A2A send callable come from U5's wiring, so the shared verify_and_invoke
# path is the same.
_REGISTRY_ID_ENV = "AGENT_REGISTRY_ID"


def build_search_path_collaborators() -> dict:
    """Assemble the collaborators search_and_invoke_impl needs (U6), from the
    deploy-wired environment.

    Reuses U5's data-plane client, allowed accounts, and SigV4 send. U6 adds
    only the registry id the single-registry search is scoped to.

    Called by the search_and_invoke tool at invocation time.
    """
    return {
        "registry_id": os.environ[_REGISTRY_ID_ENV],
        "data_plane_client": _data_plane_client(),
        "allowed_accounts": _allowed_accounts(),
        "send_message": _build_a2a_send(),
    }
