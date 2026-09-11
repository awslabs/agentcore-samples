"""
Strands agent hosted inside a Lambda MicroVM, exporting OTEL telemetry
to Amazon Bedrock AgentCore Observability via the ADOT SDK.

The MicroVM invocation model:
- Lambda gives each MicroVM a dedicated HTTPS endpoint (per-VM proxy).
- Every request to the endpoint carries an X-aws-proxy-auth token, minted
  by the caller via lambda-microvms:create-microvm-auth-token.
- Requests route to port 8080 by default (override with X-aws-proxy-port).

This module runs a small HTTP server on port 8080 that accepts POSTs at
/invoke with a JSON body {"prompt": "..."} and returns the Strands agent's
response. ADOT auto-instrumentation is enabled via the CMD wrapper
(opentelemetry-instrument), so no instrumentation code lives here.

Session propagation:
- The AgentCore-preferred header is X-Amzn-Bedrock-AgentCore-Runtime-Session-Id.
- If present, we attach it to OTEL baggage as session.id, which the AWS
  distro forwards on downstream spans.
- W3C traceparent is also honored by the auto-instrumentation.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from opentelemetry import baggage, context, trace
from strands import Agent
from strands.models.bedrock import BedrockModel

# ---- logging ----------------------------------------------------------------

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("microvm-agent")

# ---- config from env --------------------------------------------------------

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get(
    "AGENT_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)
SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "microvm-strands-agent")
PORT = int(os.environ.get("PORT", "8080"))

# MicroVM-injected identity (for logging + span attributes)
MICROVM_IMAGE_NAME = os.environ.get("AWS_LAMBDA_MICROVM_IMAGE_NAME", "")
MICROVM_IMAGE_VERSION = os.environ.get("AWS_LAMBDA_MICROVM_IMAGE_VERSION", "")

tracer = trace.get_tracer(SERVICE_NAME)

# ---- agent (lazy-init) ------------------------------------------------------
#
# CRITICAL GOTCHA: A MicroVM image is a full snapshot of the running process,
# taken during BUILD. If we construct a boto3 client at module import time,
# its credential cache captures the BUILD ROLE's credentials — and after
# snapshot resume, boto3's RefreshableCredentials still thinks those creds
# are valid, so every AWS call goes out as the build role instead of the
# execution role. Bedrock returns AccessDeniedException.
#
# Fix: build the BedrockModel + Agent LAZILY, on the first request after
# resume. A fresh boto3 client at that point walks the standard credential
# chain (AWS_CONTAINER_CREDENTIALS_FULL_URI etc.) and picks up the exec
# role's fresh credentials.

log.info(
    "boot: region=%s model=%s service=%s image=%s v=%s",
    AWS_REGION,
    MODEL_ID,
    SERVICE_NAME,
    MICROVM_IMAGE_NAME,
    MICROVM_IMAGE_VERSION,
)

_bedrock_model: BedrockModel | None = None
_agent: Agent | None = None


def _get_agent() -> Agent:
    """Build the agent on first request; reuse afterwards."""
    global _bedrock_model, _agent
    if _agent is None:
        log.info("lazy-init: constructing BedrockModel + Agent (fresh creds)")
        _bedrock_model = BedrockModel(model_id=MODEL_ID, region_name=AWS_REGION)
        _agent = Agent(
            model=_bedrock_model,
            system_prompt=(
                "You are a helpful travel assistant. Give short, useful "
                "answers (2-4 sentences)."
            ),
        )
    return _agent


def _run_agent(prompt: str, session_id: str) -> dict[str, Any]:
    """Invoke the agent, wrapped in a top-level span carrying session.id."""
    ctx = baggage.set_baggage("session.id", session_id)
    token = context.attach(ctx)
    try:
        with tracer.start_as_current_span("agent.invoke") as span:
            span.set_attribute("gen_ai.system", "aws.bedrock")
            span.set_attribute("gen_ai.request.model", MODEL_ID)
            span.set_attribute("session.id", session_id)
            span.set_attribute("aws.lambda.microvm.image_name", MICROVM_IMAGE_NAME)
            span.set_attribute(
                "aws.lambda.microvm.image_version", MICROVM_IMAGE_VERSION
            )
            span.set_attribute("agent.prompt.length", len(prompt))
            agent = _get_agent()
            t0 = time.time()
            result = agent(prompt)
            elapsed_ms = int((time.time() - t0) * 1000)
            span.set_attribute("agent.duration_ms", elapsed_ms)
            text = str(result)
            span.set_attribute("agent.response.length", len(text))
            return {
                "session_id": session_id,
                "prompt": prompt,
                "response": text,
                "duration_ms": elapsed_ms,
                "model": MODEL_ID,
            }
    finally:
        context.detach(token)


# ---- HTTP server ------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "microvm-agent/1.0"

    def _json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: Any) -> None:  # route to our logger
        log.info("http %s - " + fmt, self.address_string(), *args)

    # ---- routes ----

    # ---- MicroVM lifecycle hooks (answer regardless of method) ----

    def _hook_response(self) -> bool:
        """Answer /ready and /validate hooks; return True if handled."""
        # Strip query string just in case
        raw_path = self.path.split("?", 1)[0].rstrip("/")
        if raw_path.endswith("/ready") or raw_path == "/ready":
            log.info("hook: %s %s -> 200 ready", self.command, self.path)
            self._json(200, {"ready": True})
            return True
        if raw_path.endswith("/validate") or raw_path == "/validate":
            # A trivial validation: the module imported cleanly. We do NOT
            # touch the agent object here — that would trigger lazy-init
            # with the BUILD role's credentials, defeating the whole point.
            log.info("hook: %s %s -> 200 validated", self.command, self.path)
            self._json(200, {"validated": True})
            return True
        return False

    def do_HEAD(self) -> None:
        if self._hook_response():
            return
        self.send_response(200)
        self.end_headers()

    def do_PUT(self) -> None:
        if self._hook_response():
            return
        self._json(405, {"error": "method not allowed"})

    def do_GET(self) -> None:
        if self._hook_response():
            return
        if self.path in ("/", "/health"):
            self._json(
                200,
                {
                    "status": "ok",
                    "service": SERVICE_NAME,
                    "microvm_image_name": MICROVM_IMAGE_NAME,
                    "microvm_image_version": MICROVM_IMAGE_VERSION,
                    "region": AWS_REGION,
                    "model": MODEL_ID,
                },
            )
            return
        self._json(404, {"error": "not found", "path": self.path})

    def do_POST(self) -> None:
        if self._hook_response():
            return
        if self.path != "/invoke":
            self._json(404, {"error": "not found", "path": self.path})
            return

        # Parse body
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            raw = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError as e:
            self._json(400, {"error": "invalid json", "detail": str(e)})
            return

        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            self._json(400, {"error": "missing 'prompt'"})
            return

        # Session propagation
        session_id = (
            self.headers.get("X-Amzn-Bedrock-AgentCore-Runtime-Session-Id")
            or payload.get("session_id")
            or f"microvm-sess-{uuid.uuid4().hex[:12]}"
        )

        try:
            result = _run_agent(prompt, session_id)
            self._json(200, result)
        except Exception as e:
            log.exception("agent invocation failed")
            self._json(500, {"error": type(e).__name__, "detail": str(e)})


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log.info("listening on 0.0.0.0:%d (POST /invoke)", PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
