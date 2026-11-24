"""
AgentCore Runtime entrypoint that runs Claude Code against an AgentCore Gateway.

A coding agent running unattended: it authenticates to the gateway as a workload with
the OAuth 2.0 client-credentials grant, so no interactive login is needed, and the
container holds no Bedrock credentials of its own.

Implements the AgentCore Runtime HTTP contract:

    GET  /ping         -> {"status": "Healthy"} | {"status": "HealthyBusy"}
    POST /invocations  -> {"prompt": "..."} -> {"response": "...", "status": "success"}

A token is fetched per invocation from AgentCore Identity and passed as
ANTHROPIC_AUTH_TOKEN, never via apiKeyHelper, which sends the credential in both
Authorization and x-api-key and is rejected by AgentCore Gateway. Since each invocation
spawns a short-lived `claude -p`, a token per turn is enough; a turn that outlasts the
token would need a refreshing proxy inside the container.

Required environment:

    OAUTH_PROVIDER_NAME  AgentCore Identity OAuth2 credential provider holding the
                         client credentials. The secret value never travels through
                         the environment.
    OAUTH_SCOPE          scope to request, e.g. bedrock-gateway/invoke
    ANTHROPIC_BASE_URL   gateway inference base, e.g. https://<id>...amazonaws.com/inference
    ANTHROPIC_MODEL      e.g. mantle/anthropic.claude-sonnet-5
"""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3

PORT = 8080
WORKSPACE = os.environ.get("AGENT_WORKSPACE", "/mnt/workspace")
# Overridable so the server can be exercised on the host, outside the image, where the
# in-image config path does not exist.
CLAUDE_CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR", "/opt/claude-config")


# ── Health state ──────────────────────────────────────────────────────────────
# /ping reports HealthyBusy while a turn is in flight, which keeps the runtime session
# alive. time_of_last_update is set only on an actual status change, because advancing
# it on every ping would stop the idle timeout ever firing.

_busy_lock = threading.Lock()
_busy = 0
_status = "Healthy"
_status_changed_at = int(time.time())


def _set_busy(delta: int) -> None:
    global _busy, _status, _status_changed_at
    with _busy_lock:
        _busy += delta
        new = "HealthyBusy" if _busy > 0 else "Healthy"
        if new != _status:
            _status = new
            _status_changed_at = int(time.time())


# ── OAuth ─────────────────────────────────────────────────────────────────────


def mint_token(workload_token: str) -> str:
    # Client-credentials exchange, run by AgentCore Identity: the agent authenticates
    # as itself, no user present, and its code never handles the client secret. The
    # workload token tells AgentCore Identity which agent is asking. Failures are
    # logged in full to stderr and re-raised as a generic error, so the message the
    # caller sees carries no resource identifiers.
    try:
        resp = boto3.client("bedrock-agentcore").get_resource_oauth2_token(
            workloadIdentityToken=workload_token,
            resourceCredentialProviderName=os.environ["OAUTH_PROVIDER_NAME"],
            scopes=[os.environ["OAUTH_SCOPE"]],
            oauth2Flow="M2M",
        )
    except Exception as exc:  # noqa: BLE001 - message is sanitised before re-raising
        sys.stderr.write(f"[agent] token retrieval failed: {type(exc).__name__}: {exc}\n")
        sys.stderr.flush()
        raise RuntimeError("credential retrieval failed") from None
    return resp["accessToken"]


# ── Claude Code turn ──────────────────────────────────────────────────────────


def run_claude(prompt: str, workload_token: str) -> tuple[bool, str]:
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = CLAUDE_CONFIG_DIR
    env["ANTHROPIC_AUTH_TOKEN"] = mint_token(workload_token)
    # Mandatory: without it the gateway rejects Claude Code's experimental beta flags
    # with "400 invalid beta flag".
    env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1"
    env.setdefault("HOME", "/root")
    os.makedirs(WORKSPACE, exist_ok=True)

    started = time.time()
    proc = subprocess.run(
        ["claude", "-p", prompt, "--allowedTools", "Read,Write,Bash"],
        cwd=WORKSPACE,
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,  # exit code is handled below
    )
    elapsed = time.time() - started
    if proc.returncode != 0:
        # InvokeAgentRuntime reports any non-200 as a generic 500, so the cause is
        # logged here for CloudWatch.
        sys.stderr.write(
            f"[agent] claude FAILED after {elapsed:.0f}s exit={proc.returncode}\n"
            f"[agent] stdout: {proc.stdout.strip()[:2000]}\n"
            f"[agent] stderr: {proc.stderr.strip()[:2000]}\n"
        )
        sys.stderr.flush()
        return False, f"exit {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    sys.stderr.write(f"[agent] claude ok after {elapsed:.0f}s\n")
    sys.stderr.flush()
    return True, proc.stdout.strip()


# ── HTTP server ───────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        # Log to stderr so output lands in CloudWatch.
        sys.stderr.write("[agent] " + (fmt % args) + "\n")

    def _json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path.rstrip("/") != "/ping":
            self._json(404, {"error": "not found"})
            return
        payload = {"status": _status}
        if _status == "HealthyBusy":
            payload["time_of_last_update"] = _status_changed_at
        self._json(200, payload)

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/invocations":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("content-length") or 0)
        # The platform fronts this port, but the header is still client-supplied;
        # bound the read so a bogus Content-Length cannot exhaust memory.
        if length > 1_000_000:
            self._json(413, {"error": "request too large", "status": "error"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON", "status": "error"})
            return
        prompt = body.get("prompt")
        if not prompt:
            self._json(400, {"error": "missing 'prompt'", "status": "error"})
            return

        session = self.headers.get("X-Amzn-Bedrock-AgentCore-Runtime-Session-Id", "-")
        sys.stderr.write(f"[agent] invocation session={session} prompt={prompt[:80]!r}\n")
        # AgentCore Runtime sends a workload access token only when the caller names a
        # runtime user, as invoke.py does. Without it there is no identity to request a
        # gateway token for.
        workload_token = self.headers.get("WorkloadAccessToken")
        if not workload_token:
            sys.stderr.write("[agent] invocation carried no WorkloadAccessToken header; invoke with runtimeUserId\n")
            sys.stderr.flush()
            self._json(500, {"error": "internal error", "status": "error"})
            return
        _set_busy(1)
        try:
            ok, out = run_claude(prompt, workload_token)
        except Exception as exc:  # noqa: BLE001 - any failure must still answer the caller
            _set_busy(-1)
            # Log the detail, return a generic message: credential-path exceptions
            # can carry resource identifiers.
            sys.stderr.write(f"[agent] invocation failed: {type(exc).__name__}: {exc}\n")
            sys.stderr.flush()
            self._json(500, {"error": "internal error", "status": "error"})
            return
        _set_busy(-1)
        if ok:
            self._json(200, {"response": out, "status": "success"})
        else:
            # Full stdout/stderr already went to CloudWatch; the response body should
            # not carry gateway URLs or anything the agent echoed.
            self._json(500, {"error": "agent run failed, see runtime logs", "status": "error"})


def main() -> int:
    missing = [n for n in ("OAUTH_PROVIDER_NAME", "OAUTH_SCOPE", "ANTHROPIC_BASE_URL") if not os.environ.get(n)]
    if missing:
        sys.stderr.write(f"[agent] missing required env: {', '.join(missing)}\n")
        return 2
    sys.stderr.write(f"[agent] listening on 0.0.0.0:{PORT}, workspace={WORKSPACE}\n")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
