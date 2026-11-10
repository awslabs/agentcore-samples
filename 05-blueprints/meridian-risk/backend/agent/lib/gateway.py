# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AgentCore Gateway MCP client using SigV4 (AWS_IAM) inbound authorization.

The Gateway is created with authorizer_type = AWS_IAM, so the Runtime's own
execution-role credentials authorize MCP calls. That removes the Cognito
resource-server / machine-client / token-vault hop the OAuth flow needs — the
Runtime role's bedrock-agentcore:InvokeGateway permission is the whole story.

SigV4 signs the request body, and MCP's streamable-HTTP transport sends a
different JSON-RPC payload on every call over a long-lived session. So the
signature has to be computed per request, inside the transport, rather than once
at connection time — a pre-signed header set is only valid for the exact body it
was computed over and the Gateway returns 401 for anything else.
"""

import logging
import os

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient

logger = logging.getLogger(__name__)

SERVICE = "bedrock-agentcore"

# Headers botocore must not see when signing: hop-by-hop or client-generated
# values that either are not part of the canonical request or would be
# recomputed. Signing over them produces a signature the Gateway cannot verify.
_UNSIGNED_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "authorization",
    "x-amz-date",
    "x-amz-security-token",
    "x-amz-content-sha256",
}


class SigV4Auth_(httpx.Auth):
    """httpx auth hook that SigV4-signs every outgoing request.

    `requires_request_body` makes httpx materialize the body before the hook
    runs, which is required because the signature covers the payload hash.
    """

    requires_request_body = True

    def __init__(self, region: str, service: str = SERVICE) -> None:
        self._region = region
        self._service = service
        # Hold the session, not frozen credentials: boto3 refreshes the
        # container-role credentials the Runtime uses, and a long-lived agent
        # session can outlive the initial set.
        self._session = boto3.Session()

    def auth_flow(self, request: httpx.Request):
        credentials = self._session.get_credentials()
        if credentials is None:
            raise RuntimeError("No AWS credentials available to sign Gateway requests")

        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in _UNSIGNED_HEADERS
        }

        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=headers,
        )
        SigV4Auth(
            credentials.get_frozen_credentials(), self._service, self._region
        ).add_auth(aws_request)

        # Copy the computed auth headers back onto the real request.
        for key, value in aws_request.headers.items():
            request.headers[key] = value

        yield request


def list_all_tools(client: MCPClient) -> list:
    """List every tool the Gateway exposes, following MCP pagination.

    `MCPClient.list_tools_sync()` fetches a single page. The Gateway paginates
    `tools/list` per target and — once a non-MCP target (our inference
    connector) is attached — the *first* page can come back empty with a
    `nextCursor` set, because that target contributes no MCP tools. Taking only
    page one then yields zero tools and every specialist silently runs with no
    Gateway access at all: the assessment still completes, answering from the
    model's priors and recalled Memory instead of live data.

    Returns:
        Every tool across all pages, in page order.
    """
    tools: list = []
    token: str | None = None
    pages = 0

    while True:
        page = client.list_tools_sync(pagination_token=token)
        tools.extend(page)
        pages += 1
        token = getattr(page, "pagination_token", None)
        if not token:
            break
        # Defensive stop: a server that always returns a cursor would loop
        # forever, and an unbounded loop here would hang the assessment.
        if pages >= 20:
            logger.warning(
                "Stopped paginating Gateway tools after %d pages; "
                "last cursor was still set",
                pages,
            )
            break

    logger.info("[GATEWAY] %d tool(s) across %d page(s)", len(tools), pages)
    return tools


def create_gateway_mcp_client() -> MCPClient:
    """Build an MCP client bound to the AgentCore Gateway.

    Returns:
        An MCPClient whose tools are exposed under the "gateway" prefix.

    Raises:
        ValueError: If GATEWAY_URL is not configured.
    """
    gateway_url = os.environ.get("GATEWAY_URL")
    if not gateway_url:
        raise ValueError("GATEWAY_URL environment variable is required")

    region = os.environ.get("AWS_REGION", "us-east-1")
    logger.info("[GATEWAY] connecting to %s", gateway_url)

    return MCPClient(
        lambda: streamablehttp_client(
            url=gateway_url,
            auth=SigV4Auth_(region),
            timeout=120,
        ),
        prefix="gateway",
    )
