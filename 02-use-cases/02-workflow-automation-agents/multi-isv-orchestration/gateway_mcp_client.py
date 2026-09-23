# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lightweight raw-HTTP client for AgentCore Gateway's MCP endpoint.

Provides helpers for Cognito M2M token acquisition, paginated tool listing,
and tool invocation via JSON-RPC 2.0. Used by all notebooks in this tutorial
so cells stay focused on the integration logic rather than transport plumbing.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

import requests


DEFAULT_PROTOCOL_VERSION = "2025-03-26"


def get_cognito_m2m_token(token_endpoint: str, client_id: str, client_secret: str, scope: str) -> str:
    """Obtain an access token via OAuth2 client_credentials grant."""
    response = requests.post(
        token_endpoint,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


class GatewayMCPClient:
    """Minimal client wrapping JSON-RPC POSTs to the gateway's MCP endpoint."""

    def __init__(
        self,
        gateway_url: str,
        get_token: Callable[[], str],
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        session_id: Optional[str] = None,
    ) -> None:
        self.gateway_url = gateway_url
        self._get_token = get_token
        self._protocol_version = protocol_version
        self._session_id = session_id

    def _headers(self) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "MCP-Protocol-Version": self._protocol_version,
            "Authorization": f"Bearer {self._get_token()}",
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": f"{method.replace('/', '-')}-request",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        resp = requests.post(self.gateway_url, headers=self._headers(), json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json()

    def list_all_tools(self) -> List[Dict[str, Any]]:
        """Return tools from all targets, following per-target pagination via nextCursor."""
        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params = {"cursor": cursor} if cursor else None
            resp = self._rpc("tools/list", params)
            result = resp.get("result", {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Invoke a single tool and return the JSON-RPC result."""
        return self._rpc("tools/call", {"name": name, "arguments": arguments})

    def search_tools(self, query: str) -> List[Dict[str, Any]]:
        """Use the gateway's semantic search to narrow tools for a query."""
        resp = self._rpc(
            "tools/call",
            {"name": "x_amz_bedrock_agentcore_search", "arguments": {"query": query}},
        )
        result = resp.get("result", {})
        content = result.get("content", [])
        for item in content:
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except (json.JSONDecodeError, KeyError):
                    pass
        return []


def wait_for_target_ready(
    client: Any,
    gateway_id: str,
    target_name: str,
    region: str,
    timeout: int = 300,
) -> str:
    """Poll gateway targets until the named target reaches READY status."""
    import boto3

    agentcore = boto3.client("bedrock-agentcore-control", region_name=region)
    start = time.time()
    while time.time() - start < timeout:
        resp = agentcore.list_gateway_targets(gatewayIdentifier=gateway_id)
        for item in resp.get("items", []):
            if item.get("name") == target_name:
                status = item.get("status")
                print(f"  Target '{target_name}' status: {status}")
                if status == "READY":
                    return item.get("targetId", "")
                if status in ("FAILED", "SYNCHRONIZE_UNSUCCESSFUL"):
                    raise RuntimeError(f"Target '{target_name}' failed with status: {status}")
        time.sleep(10)  # nosemgrep: arbitrary-sleep — polling interval for async target provisioning
    raise TimeoutError(f"Target '{target_name}' did not reach READY within {timeout}s")
