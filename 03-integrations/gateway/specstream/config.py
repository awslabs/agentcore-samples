"""Shared configuration and payload builders for the SpecStream Gateway sample."""

from __future__ import annotations

import json
import os
from typing import Any

SPECSTREAM_API_BASE = "https://api.specstream.ai"
SPECSTREAM_MCP_ENDPOINT = "https://api.specstream.ai/mcp"

SUPPORTED_MODES = ("openapi", "mcp-passthrough")
DEFAULT_MODE = os.environ.get("SPECSTREAM_MODE", "openapi")

AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")

GATEWAY_NAME = "specstream-gateway"
GATEWAY_ROLE_NAME = "agentcore-specstream-gateway-role"
CREDENTIAL_PROVIDER_NAME = "specstream-api-key"
TARGET_NAME = "specstream"
MCP_PROTOCOL_VERSION = "2025-06-18"

STATE_FILE = os.path.join(os.path.dirname(__file__), ".deploy_state.json")


def load_env_file(env_path: str) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ without overwriting existing keys."""
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


def build_openapi_spec() -> dict[str, Any]:
    """Return the OpenAPI 3.0.1 specification for SpecStream Search and Extract APIs."""
    return {
        "openapi": "3.0.1",
        "info": {
            "title": "SpecStream Web Data API",
            "description": "Live web search and page extraction through SpecStream.",
            "version": "1.0.0",
        },
        "servers": [{"url": SPECSTREAM_API_BASE}],
        "paths": {
            "/v1/web-search": {
                "get": {
                    "operationId": "webSearch",
                    "summary": "Search the live web and return ranked sources and facts.",
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "required": True,
                            "description": "Natural-language search query (1 to 2,000 characters).",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "speed",
                            "in": "query",
                            "required": False,
                            "description": (
                                "Search tier: 'default' returns an answer and sources; "
                                "'fast' returns ranked sources at lower cost."
                            ),
                            "schema": {"type": "string", "enum": ["default", "fast"]},
                        },
                        {
                            "name": "num",
                            "in": "query",
                            "required": False,
                            "description": "Number of sources to return (1 to 100).",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                        {
                            "name": "passages",
                            "in": "query",
                            "required": False,
                            "description": "Include relevant page excerpts in the response when true.",
                            "schema": {"type": "boolean"},
                        },
                    ],
                    "responses": {
                        "200": {"description": "Search reply wrapped in a data object."},
                        "400": {"description": "Invalid query parameter."},
                        "401": {"description": "Missing or invalid API key."},
                        "402": {"description": "Insufficient balance or spending limit reached."},
                        "503": {"description": "Rate limit or upstream capacity; retry after Retry-After."},
                    },
                }
            },
            "/v1/extract": {
                "post": {
                    "operationId": "extractPages",
                    "summary": "Read live main text from 1 to 20 web pages or documents.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["urls"],
                                    "properties": {
                                        "urls": {
                                            "type": "array",
                                            "description": "1 to 20 HTTP or HTTPS URLs to extract.",
                                            "items": {"type": "string"},
                                        },
                                        "format": {
                                            "type": "string",
                                            "enum": ["markdown", "text", "html"],
                                            "description": "Output format for extracted page text.",
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Extraction reply wrapped in a data object."},
                        "400": {"description": "Invalid or empty urls list."},
                        "401": {"description": "Missing or invalid API key."},
                        "402": {"description": "Insufficient balance or spending limit reached."},
                        "503": {"description": "Rate limit reached; retry after Retry-After."},
                    },
                }
            },
        },
    }


def build_gateway_trust_policy(account_id: str, region: str) -> dict[str, Any]:
    """Build the IAM trust policy allowing AgentCore Gateway to assume the role."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }


def build_gateway_role_policy(mode: str) -> dict[str, Any]:
    """Build the inline IAM permissions policy for the gateway execution role."""
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported mode {mode!r}; expected one of {SUPPORTED_MODES}")

    statements: list[dict[str, Any]] = [
        {
            "Sid": "InvokeGateway",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:InvokeGateway"],
            "Resource": "*",
        }
    ]
    if mode == "openapi":
        statements.append(
            {
                "Sid": "ApiKeyCredentialAccess",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetApiKeyCredential",
                    "bedrock-agentcore:GetResourceApiKey",
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "secretsmanager:GetSecretValue",
                ],
                "Resource": "*",
            }
        )
    return {"Version": "2012-10-17", "Statement": statements}


def build_target_request(
    gateway_id: str,
    mode: str,
    credential_provider_arn: str | None = None,
    target_name: str = TARGET_NAME,
) -> dict[str, Any]:
    """Build the create_gateway_target request payload for the chosen mode."""
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported mode {mode!r}; expected one of {SUPPORTED_MODES}")

    if mode == "openapi":
        if not credential_provider_arn:
            raise ValueError("credential_provider_arn is required in 'openapi' mode")
        return {
            "gatewayIdentifier": gateway_id,
            "name": target_name,
            "description": "SpecStream Web Search and Extract APIs via OpenAPI target",
            "targetConfiguration": {
                "mcp": {
                    "openApiSchema": {
                        "inlinePayload": json.dumps(build_openapi_spec()),
                    }
                }
            },
            "credentialProviderConfigurations": [
                {
                    "credentialProviderType": "API_KEY",
                    "credentialProvider": {
                        "apiKeyCredentialProvider": {
                            "providerArn": credential_provider_arn,
                            "credentialLocation": "HEADER",
                            "credentialParameterName": "Authorization",
                            "credentialPrefix": "Bearer ",
                        }
                    },
                }
            ],
        }

    return {
        "gatewayIdentifier": gateway_id,
        "name": target_name,
        "description": "SpecStream remote MCP server via HTTP passthrough",
        "targetConfiguration": {
            "http": {
                "passthrough": {
                    "endpoint": SPECSTREAM_MCP_ENDPOINT,
                    "protocolType": "MCP",
                }
            }
        },
        "credentialProviderConfigurations": [
            {"credentialProviderType": "JWT_PASSTHROUGH"},
        ],
        "metadataConfiguration": {
            "allowedRequestHeaders": [
                "Mcp-Session-Id",
                "Content-Type",
                "Accept",
            ],
            "allowedResponseHeaders": [
                "Mcp-Session-Id",
                "Content-Type",
            ],
        },
    }


def build_passthrough_headers(
    api_key: str | None = None,
    session_id: str | None = None,
    protocol_version: str = MCP_PROTOCOL_VERSION,
) -> dict[str, str]:
    """Build headers for calling the SpecStream MCP passthrough target.

    When api_key is None or empty, Authorization is omitted so callers can still
    test gateway routing without failing client-side header construction.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": protocol_version,
    }
    if api_key and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def format_search_response(payload: dict[str, Any]) -> str:
    """Format a SpecStream /v1/web-search JSON payload into readable text."""
    if "error" in payload:
        err = payload["error"]
        code = err.get("code", "unknown_error")
        message = err.get("message", "Request failed")
        return f"SpecStream error ({code}): {message}"

    data = payload.get("data", {})
    lines: list[str] = []

    answer = data.get("answer")
    if isinstance(answer, dict) and answer.get("value"):
        lines.append(f"Answer: {answer['value']}")
        if answer.get("page"):
            lines.append(f"Cited source: {answer['page']}")

    sources = data.get("sources") or []
    if sources:
        lines.append("Sources:")
        for index, src in enumerate(sources, 1):
            title = src.get("title") or "Untitled"
            url = src.get("url") or ""
            snippet = src.get("snippet") or ""
            lines.append(f"  {index}. {title} ({url})")
            if snippet:
                lines.append(f"     {snippet}")

    if not lines:
        return "No results found."
    return "\n".join(lines)


def format_extract_response(payload: dict[str, Any]) -> str:
    """Format a SpecStream /v1/extract JSON payload into readable text."""
    if "error" in payload:
        err = payload["error"]
        code = err.get("code", "unknown_error")
        message = err.get("message", "Request failed")
        return f"SpecStream error ({code}): {message}"

    results = payload.get("data", {}).get("results") or []
    if not results:
        return "No extracted pages returned."

    lines: list[str] = []
    for item in results:
        url = item.get("url", "")
        status = item.get("status", "unknown")
        title = item.get("title") or url
        lines.append(f"[{status}] {title} ({url})")
        text = item.get("text")
        if text:
            preview = text[:300].strip()
            if len(text) > 300:
                preview += "..."
            lines.append(f"  {preview}")
    return "\n".join(lines)
