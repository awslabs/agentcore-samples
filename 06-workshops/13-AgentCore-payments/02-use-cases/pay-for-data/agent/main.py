#!/usr/bin/env python3
"""
Heurist Finance Agent — AgentCore Runtime entry point.

Pay-for-data agent that:
  - Calls paid Heurist endpoints via x402 (HTTP 402 → ProcessPayment → retry)
  - Uses AgentCore Code Interpreter for sandboxed pandas/matplotlib analysis
  - Uploads chart/CSV/report artifacts to S3 and returns presigned URLs
  - Stateless — payment config (manager ARN, session, instrument) comes from
    the invocation payload; the container holds no credentials

If `CI_ARTIFACTS_BUCKET` is not set, the agent degrades gracefully: charts
become markdown tables, text is returned inline.

Required IAM permissions for the execution role (see notebook Step 8):
  Payments       — ProcessPayment, GetPaymentInstrument, GetPaymentSession,
                   GetPaymentInstrumentBalance, GetResourcePaymentToken
                   on payment-manager/* and its instrument/* and session/*
  Code Interpreter — Start/Stop/Invoke CodeInterpreterSession on code-interpreter/*
  S3             — PutObject + GetObject on <bucket>/<prefix>/*
  Bedrock        — InvokeModel + InvokeModelWithResponseStream on the model
                   ARN AND on inference-profile/* (for CRIS-fronted models like
                   Claude Sonnet 4.6)
  CloudWatch     — added automatically by `agentcore deploy`

Environment variables (set via .env bundled in the container image):
  CI_ARTIFACTS_BUCKET    S3 bucket for artifact storage (optional but recommended)
  CI_ARTIFACTS_PREFIX    S3 key prefix (default: "heurist-finance-artifacts")
  CI_ARTIFACTS_TTL       Presigned URL TTL seconds (default: 3600)
  HEURIST_AGENT_IDS      Comma-separated Heurist agent IDs to load
  BEDROCK_MODEL_ID       Override the default Bedrock model ID
  AGENT_NAME             Name reported in payment observability
  BYPASS_TOOL_CONSENT    Set to "true" so http_request skips its TTY confirm prompt
                         (Runtime containers have no TTY)

Invocation payload:
  prompt                (str, required)  — research request
  payment_manager_arn   (str, required)
  user_id               (str, required)
  payment_session_id    (str, required)  — created by app backend with budget
  payment_instrument_id (str, required)
  bedrock_model_id      (str, optional)  — per-invocation model override

Response:
  {
    "response":  "<markdown research summary>",
    "artifacts": [                          # empty list if no artifacts produced
      {"name": "chart.png", "url": "https://...", "expires_in": 3600},
      {"name": "report.md", "url": "https://...", "expires_in": 3600}
    ]
  }
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# MINIMAL MODULE-LEVEL IMPORTS
#
# Only import what's needed for BedrockAgentCoreApp to start and respond to
# the /ping health check within the Runtime's 120s initialization timeout.
# All heavy imports (strands, bedrock_agentcore.payments, boto3 clients,
# catalog loading) are deferred to first request via _ensure_initialized().
#
# This is critical because `opentelemetry-instrument` (the CMD prefix in the
# Dockerfile) instruments every import at load time. With the full dependency
# tree (strands + bedrock_agentcore + boto3 + botocore), instrumentation
# alone can exceed 120s on cold start. Deferring keeps startup fast while
# preserving full OTel trace propagation for all request-time operations.
# ---------------------------------------------------------------------------
import json
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App instance — must be at module level for the @app.entrypoint decorator.
# BedrockAgentCoreApp is lightweight; it just starts a uvicorn server with
# /ping and /invoke endpoints.
# ---------------------------------------------------------------------------
app = BedrockAgentCoreApp()

# ---------------------------------------------------------------------------
# Environment config (lightweight — just reads env vars)
# ---------------------------------------------------------------------------
REGION = os.environ.get("AWS_REGION", "us-west-2")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")

CI_ARTIFACTS_BUCKET = os.environ.get("CI_ARTIFACTS_BUCKET", "")
CI_ARTIFACTS_PREFIX = os.environ.get("CI_ARTIFACTS_PREFIX", "heurist-finance-artifacts").rstrip("/")
CI_ARTIFACTS_TTL = int(os.environ.get("CI_ARTIFACTS_TTL", "3600"))

_raw_agent_ids = os.environ.get("HEURIST_AGENT_IDS", "")
DEFAULT_HEURIST_AGENT_IDS = (
    "ExaSearchDigestAgent",
    "YahooFinanceAgent",
    "FredMacroAgent",
    "SecEdgarAgent",
)
HEURIST_AGENT_IDS: tuple[str, ...] = (
    tuple(a.strip() for a in _raw_agent_ids.split(",") if a.strip()) if _raw_agent_ids else DEFAULT_HEURIST_AGENT_IDS
)

# ---------------------------------------------------------------------------
# Lazy-initialized heavy dependencies.
#
# Deferred from module load to first request so the container can respond to
# the Runtime /ping health check within the 120s init timeout. The
# opentelemetry-instrument wrapper adds significant overhead to module
# imports; deferring keeps cold-start under the limit while preserving full
# OTel trace propagation at request time (all boto3 calls, LLM calls, and
# tool calls are still instrumented).
# ---------------------------------------------------------------------------
_init_lock = threading.Lock()
_initialized = False
_CI_CLIENT = None
_S3_CLIENT = None
_catalog_ref = ""
_http_request_tool = None


def _ensure_initialized() -> None:
    """Lazily import heavy deps and initialize service clients on first request."""
    global _initialized, _CI_CLIENT, _S3_CLIENT, _catalog_ref, _http_request_tool

    if _initialized:
        return

    with _init_lock:
        if _initialized:
            return

        import boto3
        from strands_tools import http_request
        from strands_tools.code_interpreter import AgentCoreCodeInterpreter
        from catalog import format_catalog_for_prompt, get_tools_for_agents

        _http_request_tool = http_request
        _CI_CLIENT = AgentCoreCodeInterpreter(region=REGION, session_name="runtime-init")
        _S3_CLIENT = boto3.client("s3", region_name=REGION)

        try:
            _heurist_tools = get_tools_for_agents(HEURIST_AGENT_IDS, refresh=False)
            _catalog_ref = format_catalog_for_prompt(_heurist_tools)
            logger.info("Loaded %d Heurist tools from catalog cache.", len(_heurist_tools))
        except Exception as e:
            logger.warning("Could not load Heurist catalog: %s", e)
            _catalog_ref = "(catalog unavailable — sync_registry was not run before image build)"

        _initialized = True
        logger.info("Agent dependencies initialized successfully.")


# ---------------------------------------------------------------------------
# Per-invocation state (thread-local for concurrent request isolation)
# ---------------------------------------------------------------------------
_invocation = threading.local()


def _artifacts() -> list[dict]:
    if not hasattr(_invocation, "artifacts"):
        _invocation.artifacts = []
    return _invocation.artifacts


def _session_name() -> str:
    if not hasattr(_invocation, "session_name"):
        _invocation.session_name = f"heurist-{uuid.uuid4().hex[:12]}"
    return _invocation.session_name


def _reset_invocation_state() -> None:
    _invocation.artifacts = []
    _invocation.session_name = f"heurist-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# CI result extraction helpers
# ---------------------------------------------------------------------------


def _extract_ci_text(tool_result: dict) -> str:
    """Extract the printed text output from a Code Interpreter tool result."""
    import ast

    content = tool_result.get("content", [])
    if not content:
        raise ValueError("Code Interpreter returned empty content")
    text_blob = content[0].get("text", "")
    if not text_blob:
        raise ValueError("Code Interpreter returned no text")
    try:
        parsed = ast.literal_eval(text_blob)
        return parsed[0]["text"]
    except Exception:
        return text_blob


# ---------------------------------------------------------------------------
# Artifact tools — defined as module-level functions with @tool decorator.
# They use the lazily-initialized _S3_CLIENT and _CI_CLIENT globals which
# are guaranteed to be set before any tool is called (handle_request calls
# _ensure_initialized() before constructing the Agent).
# ---------------------------------------------------------------------------
import re
from pathlib import Path


def _safe_s3_key_name(raw: str) -> str:
    """Return a safe S3 key filename component."""
    name = Path(raw).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._")
    return name or "artifact"


# We need the @tool decorator from strands, but importing strands at module
# level is what causes the slow startup. Solution: define the tool functions
# as plain functions and wrap them with @tool inside _ensure_initialized().
# However, the Agent() constructor needs the tool references at request time.
#
# Simpler approach: import just the decorator (it's lightweight) and define
# tools normally. The heavy part is strands.Agent and strands.models, not
# the @tool decorator itself.
from strands import tool


@tool
def export_artifact_to_s3(remote_path: str, artifact_name: str | None = None) -> dict[str, Any]:
    """Export a file from the AgentCore Code Interpreter sandbox to S3.

    Use this after creating a chart (PNG), CSV, or any file in the Code
    Interpreter session. Returns a presigned URL the caller can download.

    If S3 is not configured (CI_ARTIFACTS_BUCKET not set), returns an error
    with a suggestion to represent the data as a markdown table instead.

    Args:
        remote_path:   Path to the file inside the CI sandbox (e.g. "/tmp/chart.png")
        artifact_name: Optional override for the output filename
    """
    import base64

    if not CI_ARTIFACTS_BUCKET:
        return {
            "error": "S3 artifact storage is not configured (CI_ARTIFACTS_BUCKET not set).",
            "suggestion": (
                "Represent charts as markdown tables using the underlying data. "
                "Use save_report_to_s3 for text/CSV content, which returns it inline."
            ),
        }

    sn = _session_name()
    export_code = f"""
import base64, json, mimetypes
from pathlib import Path
p = Path({remote_path!r})
if not p.exists():
    raise FileNotFoundError(f"File not found in CI sandbox: {{str(p)}}")
print(json.dumps({{
    "name": p.name,
    "mime_type": mimetypes.guess_type(str(p))[0] or "application/octet-stream",
    "b64": base64.b64encode(p.read_bytes()).decode(),
    "size": p.stat().st_size,
}}))
"""
    ci_result = _CI_CLIENT.code_interpreter(
        {
            "action": {
                "type": "executeCode",
                "session_name": sn,
                "language": "python",
                "code": export_code,
            }
        }
    )

    try:
        payload = json.loads(_extract_ci_text(ci_result))
    except Exception as exc:
        return {"error": f"Could not parse CI export output: {exc}"}

    if "b64" not in payload:
        return {"error": f"Unexpected CI payload — missing b64 field: {payload}"}

    file_bytes = base64.b64decode(payload["b64"])
    safe_name = _safe_s3_key_name(artifact_name or payload.get("name", "artifact"))
    s3_key = f"{CI_ARTIFACTS_PREFIX}/{sn}/{safe_name}"

    _S3_CLIENT.put_object(
        Bucket=CI_ARTIFACTS_BUCKET,
        Key=s3_key,
        Body=file_bytes,
        ContentType=payload.get("mime_type", "application/octet-stream"),
    )

    url = _S3_CLIENT.generate_presigned_url(
        "get_object",
        Params={"Bucket": CI_ARTIFACTS_BUCKET, "Key": s3_key},
        ExpiresIn=CI_ARTIFACTS_TTL,
    )

    artifact = {
        "name": safe_name,
        "url": url,
        "s3_key": s3_key,
        "size_bytes": len(file_bytes),
        "mime_type": payload.get("mime_type", "application/octet-stream"),
        "expires_in": CI_ARTIFACTS_TTL,
    }
    _artifacts().append(artifact)

    logger.info("Exported artifact %s → s3://%s/%s", safe_name, CI_ARTIFACTS_BUCKET, s3_key)
    return {
        "status": "success",
        "name": safe_name,
        "url": url,
        "expires_in": CI_ARTIFACTS_TTL,
    }


@tool
def save_report_to_s3(content: str, filename: str) -> dict[str, Any]:
    """Save a text report (markdown, CSV, JSON) to S3 and return a presigned URL.

    Use this for structured text output — financial summaries, data tables,
    model outputs. For binary files produced in the Code Interpreter sandbox,
    use export_artifact_to_s3 instead.

    If S3 is not configured, the content is returned inline.

    Args:
        content:  The text content to save
        filename: Desired filename (e.g. "macro_summary.md", "prices.csv")
    """
    if not CI_ARTIFACTS_BUCKET:
        return {
            "status": "inline",
            "note": "S3 not configured — content returned inline.",
            "filename": filename,
            "content": content,
        }

    safe_name = _safe_s3_key_name(filename)
    s3_key = f"{CI_ARTIFACTS_PREFIX}/{_session_name()}/{safe_name}"

    content_type = "text/plain"
    if safe_name.endswith(".md"):
        content_type = "text/markdown"
    elif safe_name.endswith(".csv"):
        content_type = "text/csv"
    elif safe_name.endswith(".json"):
        content_type = "application/json"
    elif safe_name.endswith(".html"):
        content_type = "text/html"

    encoded = content.encode("utf-8")
    _S3_CLIENT.put_object(
        Bucket=CI_ARTIFACTS_BUCKET,
        Key=s3_key,
        Body=encoded,
        ContentType=content_type,
    )

    url = _S3_CLIENT.generate_presigned_url(
        "get_object",
        Params={"Bucket": CI_ARTIFACTS_BUCKET, "Key": s3_key},
        ExpiresIn=CI_ARTIFACTS_TTL,
    )

    artifact = {
        "name": safe_name,
        "url": url,
        "s3_key": s3_key,
        "size_bytes": len(encoded),
        "mime_type": content_type,
        "expires_in": CI_ARTIFACTS_TTL,
    }
    _artifacts().append(artifact)

    logger.info("Saved report %s → s3://%s/%s", safe_name, CI_ARTIFACTS_BUCKET, s3_key)
    return {
        "status": "success",
        "name": safe_name,
        "url": url,
        "expires_in": CI_ARTIFACTS_TTL,
    }


@tool
def list_invocation_artifacts() -> dict[str, Any]:
    """List all artifacts exported to S3 during this invocation.

    Call this to verify what has been exported before composing the final response.
    """
    arts = _artifacts()
    return {
        "count": len(arts),
        "artifacts": [{"name": a["name"], "url": a["url"], "expires_in": a["expires_in"]} for a in arts],
    }


# ---------------------------------------------------------------------------
# System prompt builder (invocation-specific: includes CI session name)
# ---------------------------------------------------------------------------


def _build_system_prompt(ci_session: str) -> str:
    s3_instructions = (
        (
            f"- Charts/images: save to `/tmp/<name>` inside the CI session, then call "
            f"`export_artifact_to_s3` with that path to upload to S3 and get a download URL.\n"
            f"- Text reports/CSVs: call `save_report_to_s3` directly — no need to write to CI first.\n"
            f"- Presigned URLs are valid for {CI_ARTIFACTS_TTL} seconds.\n"
            f"- After exporting, include the URL in your response so the caller can access the file."
        )
        if CI_ARTIFACTS_BUCKET
        else (
            "- S3 artifact storage is not configured in this deployment.\n"
            "- Represent all chart data as markdown tables using the underlying numbers.\n"
            "- Use `save_report_to_s3` for text content — it will return the content inline."
        )
    )

    return f"""You are a finance research and data visualization agent.

You have access to paid financial data endpoints via the Heurist network. Use the
`http_request` tool to call the endpoint URLs listed below. All endpoints accept POST
requests with JSON bodies.

**Payment is handled automatically.** When an endpoint returns HTTP 402, the system
settles USDC on-chain and retries the request. You do not need to handle payments.

{_catalog_ref}

## Working Rules

- Use http_request for all Heurist endpoint calls. Always method="POST", params as JSON body.
- Parallelize independent data fetches — issue multiple http_request calls in the same tool-use round when they don't depend on each other's results. Payment is handled per-call.
- Use AgentCore Code Interpreter for pandas/matplotlib analysis.
- Never fabricate data. Only use values returned by tools.
- If a tool call fails, report the error and stop.

## Code Interpreter — session: `{ci_session}`

**Session lifecycle**
- Start with `initSession` if the session is not initialized.
- Use `writeFiles` to pass datasets into the sandbox as JSON/CSV files.
- Use `executeCode` for analysis and charting.
- The session is private to this invocation and auto-expires.

**Artifact export**
{s3_instructions}

**CI action examples:**
- Init: `{{"action": {{"type": "initSession", "session_name": "{ci_session}", "description": "analysis"}}}}`
- Write: `{{"action": {{"type": "writeFiles", "session_name": "{ci_session}", "content": [{{"path": "data.json", "text": "{{...}}"}}]}}}}`
- Execute: `{{"action": {{"type": "executeCode", "session_name": "{ci_session}", "language": "python", "code": "import pandas as pd; ..."  }}}}`

## Context
- Today: {datetime.now().strftime("%Y-%m-%d")}
- Region: {REGION}
- S3 artifacts: {"enabled (bucket: " + CI_ARTIFACTS_BUCKET + ")" if CI_ARTIFACTS_BUCKET else "not configured — text/table output only"}
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@app.entrypoint
def handle_request(payload: dict, context=None) -> dict:
    """Handle an invocation from the app backend.

    The app backend creates a payment session with an appropriate budget before
    invoking. Session ID and instrument ID are passed in the payload — the agent
    cannot create or modify sessions (enforced at the IAM level).

    Required payload fields:
        prompt                (str) — the research request
        payment_manager_arn   (str) — ARN of the Payment Manager
        user_id               (str) — user identity for payment isolation
        payment_session_id    (str) — active session with a spending limit
        payment_instrument_id (str) — funded embedded wallet

    Optional payload fields:
        bedrock_model_id      (str) — per-invocation model override
    """
    # Lazy-init heavy deps on first request (keeps cold-start under 120s)
    _ensure_initialized()
    _reset_invocation_state()
    ci_session = _session_name()

    # Import heavy deps (already cached after _ensure_initialized)
    import boto3
    from bedrock_agentcore.payments.integrations.strands import (
        AgentCorePaymentsPlugin,
        AgentCorePaymentsPluginConfig,
    )
    from botocore.config import Config as BotoConfig
    from strands import Agent
    from strands.models import BedrockModel

    # Unwrap the agentcore invoke double-wrapping:
    # `agentcore invoke '{"key": "val"}'` → payload = {"prompt": '{"key":"val"}'}
    raw_prompt = payload.get("prompt", "")
    if isinstance(raw_prompt, str) and raw_prompt.strip().startswith("{"):
        try:
            inner = json.loads(raw_prompt)
            if isinstance(inner, dict) and "payment_manager_arn" in inner:
                payload = inner
        except json.JSONDecodeError:
            pass

    prompt = payload.get("prompt", "").strip()
    payment_manager_arn = payload.get("payment_manager_arn", "").strip()
    user_id = payload.get("user_id", "").strip()
    session_id = payload.get("payment_session_id", "").strip()
    instrument_id = payload.get("payment_instrument_id", "").strip()

    missing = [
        name
        for name, val in [
            ("prompt", prompt),
            ("payment_manager_arn", payment_manager_arn),
            ("user_id", user_id),
            ("payment_session_id", session_id),
            ("payment_instrument_id", instrument_id),
        ]
        if not val
    ]
    if missing:
        return {"error": f"Missing required payload fields: {', '.join(missing)}"}

    model_id = payload.get("bedrock_model_id", MODEL_ID)

    payment_plugin = AgentCorePaymentsPlugin(
        config=AgentCorePaymentsPluginConfig(
            payment_manager_arn=payment_manager_arn,
            user_id=user_id,
            payment_instrument_id=instrument_id,
            payment_session_id=session_id,
            region=REGION,
            agent_name=os.environ.get("AGENT_NAME", "HeuristFinanceAgent"),
        )
    )

    # Claude Sonnet 4.6 supports up to 64k output tokens. Multi-step workflows
    # (5+ paid tool calls + Code Interpreter + chart export + markdown
    # report) routinely need more than the SDK's default 4k cap, which
    # otherwise raises Strands' MaxTokensReachedException mid-run.
    # The custom client config keeps long single-turn streamed responses
    # from tripping the default 60s bedrock-runtime read timeout.
    model = BedrockModel(
        boto_session=boto3.Session(region_name=REGION),
        boto_client_config=BotoConfig(
            read_timeout=int(os.environ.get("AGENT_BEDROCK_READ_TIMEOUT", "1500")),
            connect_timeout=15,
            retries={"max_attempts": 1},
        ),
        model_id=model_id,
        streaming=True,
        temperature=0,
        max_tokens=int(os.environ.get("AGENT_MAX_TOKENS", "32000")),
    )

    agent = Agent(
        system_prompt=_build_system_prompt(ci_session),
        model=model,
        tools=[
            _http_request_tool,
            _CI_CLIENT.code_interpreter,
            export_artifact_to_s3,
            save_report_to_s3,
            list_invocation_artifacts,
        ],
        plugins=[payment_plugin],
    )

    result = agent(prompt)

    content = result.message.get("content", [])
    text = next(
        (block.get("text", "") for block in content if isinstance(block, dict) and "text" in block),
        str(result),
    )

    return {
        "response": text,
        "artifacts": [{"name": a["name"], "url": a["url"], "expires_in": a["expires_in"]} for a in _artifacts()],
    }


if __name__ == "__main__":
    app.run()
