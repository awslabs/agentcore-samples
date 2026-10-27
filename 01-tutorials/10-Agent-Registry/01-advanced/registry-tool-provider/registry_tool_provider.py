"""AWS Agent Registry ToolProvider for Strands Agents.

Dynamically discovers and loads tools from AWS Agent Registry via semantic search.
Instead of hardcoding tools at startup, the agent gets only the tools relevant
to the current domain — bounded context, automatic, no LLM decision needed.

Usage:
    from strands import Agent
    from registry_tool_provider import RegistryToolProvider

    provider = RegistryToolProvider(
        registry_ids=["Vf4gtZ5mreKG"],
        domains=["order management", "CRM"],
        gateway_url="https://gw-xxx.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
        gateway_token_fn=lambda: get_my_token(),
    )
    agent = Agent(tool_providers=[provider])
    agent("Check my open orders and update the CRM")

Security notes:
    - Only APPROVED records are loaded by default (required_status parameter)
    - Tool names are validated against a strict regex
    - Tool descriptions are truncated to prevent prompt injection surface
    - Gateway and endpoint URLs must be HTTPS
    - Runtime ARNs can be restricted via allowed_runtime_arns allowlist
    - JSON payloads from Registry are size-bounded before parsing
"""

import json
import re
import time
import logging
import uuid
from typing import Any, Callable, Sequence

import boto3
from strands.tools.tool_provider import ToolProvider
from strands.tools.tools import PythonAgentTool

logger = logging.getLogger(__name__)

_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")
_MAX_DESCRIPTION_LEN = 256
_MAX_SCHEMA_BYTES = 1_048_576  # 1 MB


def _validate_https(url: str, label: str) -> None:
    if url and not url.startswith("https://"):
        raise ValueError(f"{label} must use HTTPS, got: {url[:40]}")


def _safe_json_loads(raw: str, label: str) -> Any:
    if len(raw) > _MAX_SCHEMA_BYTES:
        logger.warning("Rejecting oversized %s payload (%d bytes)", label, len(raw))
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _sanitize_description(desc: str) -> str:
    # Strip control characters and truncate
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", desc)
    return cleaned[:_MAX_DESCRIPTION_LEN]


class RegistryToolProvider(ToolProvider):
    """Discovers tools from AWS Agent Registry via semantic search.

    Args:
        registry_ids: Registry identifiers to search across.
        domains: Domain keywords for semantic search.
        gateway_url: Gateway URL for MCP tool invocation (must be HTTPS).
        gateway_token_fn: Callable returning a Bearer token for the Gateway.
        region: AWS region for Registry and Runtime API calls.
        endpoint_url: Custom Registry endpoint (must be HTTPS).
        max_results: Max records per search query.
        cache_ttl: Seconds to cache results. 0 disables caching.
        required_status: Only load records with this status. None disables filtering.
        allowed_runtime_arns: Allowlist of Runtime ARNs for A2A invocation. None allows all.
        fail_open: If True (default), search failures return empty. If False, raise.
    """

    def __init__(
        self,
        registry_ids: list[str],
        domains: list[str],
        gateway_url: str | None = None,
        gateway_token_fn: Callable[[], str] | None = None,
        region: str = "us-west-2",
        endpoint_url: str | None = None,
        max_results: int = 10,
        cache_ttl: int = 300,
        required_status: str | None = "APPROVED",
        allowed_runtime_arns: list[str] | None = None,
        fail_open: bool = True,
    ):
        _validate_https(gateway_url or "", "gateway_url")
        _validate_https(endpoint_url or "", "endpoint_url")

        self._registry_ids = registry_ids
        self._domains = domains
        self._gateway_url = gateway_url
        self._gateway_token_fn = gateway_token_fn
        self._region = region
        self._max_results = max_results
        self._cache_ttl = cache_ttl
        self._required_status = required_status
        self._allowed_runtime_arns = set(allowed_runtime_arns) if allowed_runtime_arns else None
        self._fail_open = fail_open
        self._cache: list[PythonAgentTool] = []
        self._cache_ts: float = 0
        self._consumers: set = set()

        kw = {"region_name": region}
        if endpoint_url:
            kw["endpoint_url"] = endpoint_url
        self._dp = boto3.client("bedrock-agentcore", **kw)

    # --- ToolProvider interface ---

    async def load_tools(self, **kwargs: Any) -> Sequence[PythonAgentTool]:
        if self._cache_ttl and (time.time() - self._cache_ts) < self._cache_ttl and self._cache:
            return self._cache

        seen: dict[str, PythonAgentTool] = {}
        for domain in self._domains:
            for record in self._search(domain):
                if self._required_status and record.get("status") != self._required_status:
                    continue
                for tool in self._record_to_tools(record):
                    seen[tool.tool_name] = tool

        self._cache = list(seen.values())
        self._cache_ts = time.time()
        logger.info("RegistryToolProvider: loaded %d tools from %d domain(s)", len(self._cache), len(self._domains))
        return self._cache

    def add_consumer(self, consumer_id: Any, **kwargs: Any) -> None:
        self._consumers.add(consumer_id)

    def remove_consumer(self, consumer_id: Any, **kwargs: Any) -> None:
        self._consumers.discard(consumer_id)
        if not self._consumers:
            self._cache.clear()

    # --- Registry search ---

    def _search(self, query: str) -> list[dict]:
        try:
            resp = self._dp.search_registry_records(
                registryIds=self._registry_ids,
                searchQuery=query,
                maxResults=self._max_results,
            )
            return resp.get("registryRecords", [])
        except Exception as e:
            if not self._fail_open:
                raise
            logger.warning("Registry search failed for '%s': %s", query, e)
            return []

    # --- Record → Tool conversion ---

    def _record_to_tools(self, record: dict) -> list[PythonAgentTool]:
        protocol = record.get("descriptorType", "")
        descriptors = record.get("descriptors", {})
        name = record.get("name", "unknown")

        if protocol == "MCP":
            return self._mcp_tools(name, descriptors)
        elif protocol == "A2A":
            return self._a2a_tool(name, record, descriptors)
        return self._custom_tool(name, record)

    def _mcp_tools(self, record_name: str, descriptors: dict) -> list[PythonAgentTool]:
        raw = descriptors.get("mcp", {}).get("tools", {}).get("inlineContent", "")
        if not raw:
            return []

        defs = _safe_json_loads(raw, f"tools:{record_name}")
        if defs is None:
            return []
        if isinstance(defs, dict):
            defs = defs.get("tools", [])

        tools = []
        for td in defs:
            tool_name = td.get("name", "")
            if not _TOOL_NAME_RE.match(tool_name):
                logger.warning("Skipping invalid tool name: %s", tool_name[:50])
                continue

            spec = {
                "name": tool_name,
                "description": _sanitize_description(td.get("description", f"Tool from {record_name}")),
                "inputSchema": {"json": td.get("inputSchema", {"type": "object", "properties": {}})},
            }

            gw, tk = self._gateway_url, self._gateway_token_fn

            def make_fn(n=tool_name, g=gw, t=tk):
                def fn(tool, **kwargs):
                    return _call_gateway(g, t, n, kwargs)
                return fn

            tools.append(PythonAgentTool(tool_name=tool_name, tool_spec=spec, tool_func=make_fn()))
        return tools

    def _a2a_tool(self, record_name: str, record: dict, descriptors: dict) -> list[PythonAgentTool]:
        card_raw = descriptors.get("a2a", {}).get("agentCard", {}).get("inlineContent", "")
        card = _safe_json_loads(card_raw, f"agentCard:{record_name}") if card_raw else None

        # Extract endpoint URL from agent card (url field per A2A spec)
        arn = ""
        if card:
            arn = card.get("url", "") or card.get("runtimeArn", "")
        # Fallback to top-level record field
        if not arn:
            arn = record.get("runtimeArn", "")
        if not arn:
            logger.warning("No runtime ARN or endpoint found for A2A record: %s", record_name)
            return []

        if self._allowed_runtime_arns is not None and arn not in self._allowed_runtime_arns:
            logger.warning("Runtime ARN not in allowlist, skipping: %s", arn[:80])
            return []

        safe = re.sub(r"[^a-zA-Z0-9_]", "_", record_name)
        desc = _sanitize_description(record.get("description", f"Invoke A2A agent: {record_name}"))

        if card:
            skills = card.get("skills", [])
            if skills:
                skill_names = ", ".join(s.get("name", "") for s in skills[:5])
                desc = _sanitize_description(f"{desc}. Skills: {skill_names}")

        spec = {
            "name": f"invoke_{safe}",
            "description": desc,
            "inputSchema": {"json": {
                "type": "object",
                "properties": {"message": {"type": "string", "description": "Message to send to the agent"}},
                "required": ["message"],
            }},
        }

        region = self._region

        def fn(tool, message: str = "", **kwargs):
            return _call_runtime(region, arn, message)

        return [PythonAgentTool(tool_name=f"invoke_{safe}", tool_spec=spec, tool_func=fn)]

    def _custom_tool(self, record_name: str, record: dict) -> list[PythonAgentTool]:
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", record_name)
        if not _TOOL_NAME_RE.match(safe):
            return []

        spec = {
            "name": safe,
            "description": _sanitize_description(record.get("description", f"Custom resource: {record_name}")),
            "inputSchema": {"json": {
                "type": "object",
                "properties": {"input": {"type": "string"}},
            }},
        }

        def fn(tool, **kwargs):
            return json.dumps({"status": "custom_protocol", "record": record_name})

        return [PythonAgentTool(tool_name=safe, tool_spec=spec, tool_func=fn)]


# --- Invocation helpers ---

def _call_gateway(gateway_url: str, token_fn: Callable | None, tool_name: str, arguments: dict) -> str:
    if not gateway_url:
        return json.dumps({"error": "No gateway_url configured"})

    import httpx
    try:
        token = token_fn() if callable(token_fn) else ""
        resp = httpx.post(
            gateway_url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": "1", "method": "tools/call",
                  "params": {"name": tool_name, "arguments": arguments}},
            timeout=120,
        )
    except Exception as e:
        # Never leak token in exception traces
        return json.dumps({"error": f"Gateway call failed: {type(e).__name__}"})

    body = resp.json()
    if "error" in body:
        return json.dumps(body["error"])
    content = body.get("result", {}).get("content", [])
    return content[0].get("text", json.dumps(content)) if content else json.dumps(body)


def _call_runtime(region: str, runtime_arn: str, message: str) -> str:
    if not runtime_arn:
        return json.dumps({"error": "No runtimeArn in registry record"})

    try:
        client = boto3.client("bedrock-agentcore", region_name=region)
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            sessionId=f"registry-{uuid.uuid4().hex[:12]}",
            payload=json.dumps({"messages": [{"role": "user", "content": [{"text": message}]}]}),
        )
        chunks = []
        for event in resp.get("body", []):
            if "chunk" in event:
                chunks.append(event["chunk"].get("bytes", b"").decode())
        return "".join(chunks) or json.dumps({"status": "no_response"})
    except Exception as e:
        return json.dumps({"error": f"Runtime invocation failed: {type(e).__name__}"})
