"""
runtime_proxy.py — Forwards FastAPI requests to the deployed AgentCore Runtime.

When AGENTCORE_RUNTIME_ARN is set in .env, the FastAPI backend calls this
instead of running agents locally. The frontend is completely unaware of the
difference — it still talks to localhost:8000 as before.
"""

import json
import logging
import os
import urllib.parse

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

logger = logging.getLogger(__name__)

RUNTIME_ARN = os.getenv("AGENTCORE_RUNTIME_ARN", "")
RUNTIME_ENDPOINT = os.getenv("AGENTCORE_RUNTIME_ENDPOINT", "DEFAULT")
REGION = os.getenv("AWS_REGION", "us-east-1")


def _is_enabled() -> bool:
    return bool(RUNTIME_ARN)


def _invoke(payload: dict) -> dict:
    """Sign and POST payload to the AgentCore Runtime /invocations endpoint."""
    session = boto3.Session(profile_name=os.getenv("AWS_PROFILE", "default"), region_name=REGION)

    encoded_arn = urllib.parse.quote(RUNTIME_ARN, safe="")
    url = (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com"
        f"/runtimes/{encoded_arn}/invocations"
        f"?qualifier={RUNTIME_ENDPOINT}"
    )

    body = json.dumps(payload).encode("utf-8")
    credentials = session.get_credentials().get_frozen_credentials()
    aws_request = AWSRequest(method="POST", url=url, data=body, headers={"Content-Type": "application/json"})
    SigV4Auth(credentials, "bedrock-agentcore", REGION).add_auth(aws_request)

    try:
        resp = requests.post(url, data=body, headers=dict(aws_request.headers), timeout=90)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        logger.warning("Runtime proxy timed out after 90s")
        raise Exception(
            "Code execution timed out. The code may contain infinite loops or be waiting for input that cannot be provided."
        )
    except requests.exceptions.RequestException as e:
        logger.warning("Runtime proxy request failed: %s", e)
        raise


def generate_code(prompt: str, session_id: str, actor_id: str = None) -> dict:
    """Forward a generate_code request to the runtime and return FastAPI-compatible response."""
    if not _is_enabled():
        return None  # caller falls back to local agents

    result = _invoke(
        {"action": "generate_code", "prompt": prompt, "session_id": session_id, "actor_id": actor_id or session_id}
    )

    return {
        "success": result.get("success", False),
        "code": result.get("code", ""),
        "session_id": result.get("session_id", session_id),
        "agent_used": "agentcore_runtime",
        "memory_enabled": result.get("memory_enabled", False),
        "csv_file_used": None,
    }


def execute_code(
    code: str, session_id: str, interactive: bool = False, inputs: list = None, actor_id: str = None
) -> dict:
    """Forward an execute_code request to the runtime and return FastAPI-compatible response."""
    if not _is_enabled():
        return None  # caller falls back to local agents

    result = _invoke(
        {
            "action": "execute_code",
            "code": code,
            "session_id": session_id,
            "actor_id": actor_id or session_id,
            "interactive": interactive,
            "inputs": inputs or [],
        }
    )

    return {
        "success": result.get("success", False),
        "result": result.get("result", ""),
        "session_id": result.get("session_id", session_id),
        "agent_used": result.get("agent_used", "agentcore_runtime"),
        "executor_type": "agentcore_runtime",
        "interactive": interactive,
        "inputs_used": inputs,
        "images": result.get("images", []),
        "is_chart_code": bool(result.get("images")),
        "memory_enabled": result.get("memory_enabled", False),
    }
