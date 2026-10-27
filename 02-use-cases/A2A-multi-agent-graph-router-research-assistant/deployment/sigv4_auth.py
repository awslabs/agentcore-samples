"""Boto3-based authentication for cross-runtime A2A agent invocation.

Instead of manually signing httpx requests with SigV4 (which is fragile and
must exactly match the data-plane API's signing expectations), this module
uses boto3's ``invoke_agent_runtime`` directly.  A custom httpx async
transport wraps the boto3 call so that the standard A2AAgent / A2A client
pipeline works unchanged.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

import boto3
import httpx
from a2a.client import ClientConfig, ClientFactory
from strands.agent.a2a_agent import A2AAgent

if TYPE_CHECKING:
    from a2a.types import AgentCard

logger = logging.getLogger(__name__)


def _parse_agent_url(url: str) -> tuple[str, str | None]:
    """Extract the agent runtime ARN/ID and optional accountId from a URL.

    Args:
        url: An AgentCore invocation URL of the form
            ``https://…/runtimes/<agent-id>/invocations?accountId=<acct>``

    Returns:
        Tuple of (agent_runtime_id_or_arn, account_id_or_None).
    """
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    # Path: /runtimes/<id>/invocations
    parts = parsed.path.strip("/").split("/")
    min_path_segments = 2
    agent_id = parts[1] if len(parts) >= min_path_segments else parts[0]
    qs = parse_qs(parsed.query)
    account_id = qs.get("accountId", [None])[0]
    return agent_id, account_id


class Boto3InvokeTransport(httpx.AsyncBaseTransport):
    """httpx async transport that routes requests through boto3 invoke_agent_runtime.

    This avoids manual SigV4 signing entirely — boto3 handles auth, retries,
    and credential resolution (including ECS task roles inside AgentCore).
    """

    def __init__(self, region: str) -> None:
        """Initialize transport.

        Args:
            region: AWS region for the bedrock-agentcore client.
        """
        self._region = region
        # Increase read timeout to 300s (default 60s is too short for agents
        # that make multiple model/tool calls before returning).
        from botocore.config import Config

        self._client = boto3.client(
            "bedrock-agentcore",
            region_name=region,
            config=Config(read_timeout=300, retries={"max_attempts": 0}),
        )

    @staticmethod
    def _read_response_body(response: dict) -> bytes:
        """Read the streaming boto3 response into bytes.

        Args:
            response: The boto3 invoke_agent_runtime response dict.

        Returns:
            The response body as bytes.
        """
        chunks = []
        for chunk in response.get("response", []):
            if isinstance(chunk, bytes):
                chunks.append(chunk)
            elif isinstance(chunk, dict) and "chunk" in chunk:
                chunks.append(chunk["chunk"])
        if chunks:
            return b"".join(chunks)

        # Fallback: iter_lines for SSE/streaming responses
        if "response" in response:
            raw_parts = []
            for line in response["response"].iter_lines(chunk_size=1024):
                if line:
                    decoded = line.decode("utf-8") if isinstance(line, bytes) else line
                    raw_parts.append(decoded)
            return "\n".join(raw_parts).encode("utf-8")
        return b""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Intercept the httpx request and call invoke_agent_runtime instead.

        Runs the synchronous boto3 call in a thread executor so it doesn't
        block the async event loop — critical for parallel sub-agent invocations.

        Args:
            request: The outgoing httpx request (from A2A client).

        Returns:
            An httpx.Response wrapping the boto3 response.
        """
        import asyncio

        url = str(request.url)
        agent_id, account_id = _parse_agent_url(url)
        body = request.content if request.content else b""

        kwargs: dict = {
            "agentRuntimeArn": agent_id,
            "payload": body,
            "runtimeSessionId": str(uuid.uuid4()),
            "contentType": "application/json",
        }
        if account_id:
            kwargs["accountId"] = account_id

        loop = asyncio.get_event_loop()

        try:
            response = await loop.run_in_executor(
                None, lambda: self._client.invoke_agent_runtime(**kwargs)
            )
            response_body = await loop.run_in_executor(None, self._read_response_body, response)

            return httpx.Response(
                status_code=response.get("statusCode", 200),
                headers={"content-type": response.get("contentType", "application/json")},
                content=response_body,
            )
        except self._client.exceptions.AccessDeniedException as exc:
            logger.error("AccessDenied invoking %s: %s", agent_id, exc)
            return httpx.Response(status_code=403, content=json.dumps({"error": str(exc)}).encode())
        except (
            ValueError,
            KeyError,
            TypeError,
            ConnectionError,
            self._client.exceptions.RuntimeClientError,
        ) as exc:
            logger.error("Error invoking %s: %s", agent_id, exc)
            return httpx.Response(status_code=500, content=json.dumps({"error": str(exc)}).encode())
        except Exception as exc:  # noqa: BLE001
            # Catch boto3 read timeouts, connection errors, and other unexpected failures
            logger.error("Unexpected error invoking %s: %s: %s", agent_id, type(exc).__name__, exc)
            return httpx.Response(status_code=502, content=json.dumps({"error": str(exc)}).encode())


class SigV4A2AAgent(A2AAgent):
    """A2AAgent that invokes remote AgentCore runtimes via boto3.

    Uses ``Boto3InvokeTransport`` to route A2A JSON-RPC messages through
    boto3's ``invoke_agent_runtime``, which handles SigV4 signing, credential
    resolution, and retries automatically.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        region: str = "us-west-2",
        name: str | None = None,
        description: str | None = None,
        timeout: int = 300,
    ) -> None:
        """Initialize boto3-backed A2AAgent.

        Args:
            endpoint: AgentCore runtime URL for the remote A2A agent.
            region: AWS region for boto3 client.
            name: Agent name (populated from card if not set).
            description: Agent description (populated from card if not set).
            timeout: HTTP timeout in seconds.
        """
        transport = Boto3InvokeTransport(region)
        signing_client = httpx.AsyncClient(timeout=timeout, transport=transport)

        config = ClientConfig(httpx_client=signing_client, streaming=True)
        factory = ClientFactory(config)

        super().__init__(
            endpoint,
            name=name,
            description=description,
            timeout=timeout,
            a2a_client_factory=factory,
        )

    async def get_agent_card(self) -> AgentCard:
        """Return a locally-constructed agent card (no HTTP fetch).

        AgentCore's runtime URL doesn't expose /.well-known/agent-card.json
        directly (it returns 403 for GET requests). Instead, we construct
        a minimal card from the endpoint URL and agent metadata.

        Returns:
            A locally-constructed AgentCard.
        """
        from a2a.types import AgentCapabilities
        from a2a.types import AgentCard as A2AAgentCard

        if self._agent_card is not None:
            return self._agent_card

        self._agent_card = A2AAgentCard(
            name=self.name or "remote-agent",
            description=self.description or "Remote A2A agent on AgentCore",
            url=self.endpoint,
            version="1.0.0",
            capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
            defaultInputModes=["text/plain"],
            defaultOutputModes=["text/plain"],
            skills=[],
        )

        logger.debug("agent=<%s>, endpoint=<%s> | using local agent card (boto3)", self.name, self.endpoint)
        return self._agent_card


def arn_to_runtime_url(arn: str) -> str:
    """Convert an AgentCore runtime ARN to its invocation URL.

    Uses the agent-ID format with an accountId query parameter instead of
    URL-encoding the full ARN, which avoids path-encoding issues with the
    AgentCore data-plane API gateway.

    Args:
        arn: The agent runtime ARN
            (e.g. arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/my-agent-abc123).

    Returns:
        The HTTPS invocation URL for the runtime.
    """
    parts = arn.split(":")
    region = parts[3]
    account_id = parts[4]
    agent_id = parts[5].split("/", 1)[1]  # "runtime/my-agent-abc123" -> "my-agent-abc123"
    return f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{agent_id}/invocations?accountId={account_id}"
