"""A Strands agent on an AWS-managed EC2 instance, behind an AgentCore Gateway.

``BedrockAgentCoreApp`` provides the runtime's HTTP contract (``POST /invocations``
and ``GET /ping`` on ``0.0.0.0:8080``), so there is no hand-written HTTP server.

Two things this adds on top of a plain Strands agent, both explained in the README:
the client's ``traceparent`` becomes the parent of the Strands spans, and every
response carries deterministic infrastructure/EBS evidence that ``invoke.py`` checks.

The ``gen_ai.*`` attributes behind the GenAI Observability screen come from Strands
itself — no manual Bedrock instrumentation here.
"""

import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from opentelemetry import trace
from opentelemetry.context import attach, detach, set_value
from opentelemetry.propagate import extract
from strands import Agent, tool

app = BedrockAgentCoreApp()

# deploy.py injects BEDROCK_MODEL_ID; MODEL_ID is the name the official sample
# uses. We accept both so this file works in either context.
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID") or os.environ.get(
    "MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0"
)
STATE_DIR = os.environ.get("AGENT_STATE_DIR", "/mnt/data")

IMDS_BASE = os.environ.get("AWS_EC2_METADATA_SERVICE_ENDPOINT",
                           "http://169.254.169.254").rstrip("/")


# ────────────────────────────── IMDS ──────────────────────────────
def _imds(path, token=None, method="GET"):
    req = urllib.request.Request(f"{IMDS_BASE}/latest/{path}", method=method)
    if method == "PUT":
        req.add_header("X-aws-ec2-metadata-token-ttl-seconds", "60")
    elif token:
        req.add_header("X-aws-ec2-metadata-token", token)
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        # A 404 on public-ipv4 is the private-network requirement SUCCEEDING.
        return f"<HTTP {e.code}>"
    except Exception as e:
        return f"<error: {type(e).__name__}>"


def collect_infra():
    """Instance identity from IMDSv2, with instrumentation SUPPRESSED: the 404 on
    ``public-ipv4`` is the expected result, and auto-instrumentation would count it
    as an error. See README → Do not instrument the IMDS."""
    ctx = attach(set_value("suppress_instrumentation", True))
    try:
        tok = _imds("api/token", method="PUT")
        tok = None if tok.startswith("<") else tok
        public = _imds("meta-data/public-ipv4", tok)
        data = {
            "instance_id": _imds("meta-data/instance-id", tok),
            "instance_type": _imds("meta-data/instance-type", tok),
            "az": _imds("meta-data/placement/availability-zone", tok),
            "local_ipv4": _imds("meta-data/local-ipv4", tok),
            "public_ipv4": public,
            "no_public_ip": public.startswith("<"),
        }
    finally:
        detach(ctx)
    uname = platform.uname()
    data.update(hostname=uname.node, kernel=f"{uname.system} {uname.release}",
                arch=uname.machine, cpus=os.cpu_count())
    return data


def _memory_mib():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


# ────────────────────────────── state on EBS ──────────────────────────────
def record_invocation():
    """Increments a counter on the EBS volume. ``invocations > 1`` proves the
    filesystem survived; back to ``1`` mid-session proves the call landed on a
    different instance (sessionId routes, it does not pin)."""
    info = {"state_dir": STATE_DIR}
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        path = os.path.join(STATE_DIR, "counter.json")
        previous = {}
        if os.path.exists(path):
            with open(path) as f:
                previous = json.load(f)
        n = previous.get("invocations", 0) + 1
        with open(path, "w") as f:
            json.dump({"invocations": n,
                       "first_ts": previous.get("first_ts", time.time()),
                       "last_ts": time.time()}, f)
        info.update(write_ok=True, invocations=n,
                    persisted_from_previous_invocation=bool(previous))
    except Exception as e:
        info.update(write_ok=False, error=f"{type(e).__name__}: {e}")
    return info


# ────────────────────────────── tools ──────────────────────────────
@tool
def whoami() -> str:
    """Report the machine this agent is running on: kernel, arch, CPUs, memory."""
    d = collect_infra()
    d["memory_total"] = f"{_memory_mib()} MiB"
    verdict = "none — private subnet" if d.pop("no_public_ip") else "HAS A PUBLIC IP"
    d["public_ipv4"] = f"{d['public_ipv4']}  ({verdict})"
    return "\n".join(f"{k:14s} {v}" for k, v in d.items())


@tool
def run_command(command: str) -> str:
    """Run a short shell command on the instance and return its output.

    Useful for showing that this is an ordinary Linux machine — try
    ``nproc``, ``df -h /mnt/data``, or ``cat /etc/os-release``.
    """
    try:
        # check=False on purpose: a failing command's stderr is exactly what we want to show
        r = subprocess.run(command, shell=True, capture_output=True,
                           text=True, timeout=15, check=False)
    except subprocess.TimeoutExpired:
        return "Command timed out after 15s."
    return (r.stdout + r.stderr).strip()[:4000] or "(no output)"


agent = Agent(
    model=MODEL_ID,
    tools=[whoami, run_command],
    system_prompt=(
        "You are a concise assistant running on an AWS-managed EC2 instance on "
        "Amazon Bedrock AgentCore (Instances compute type / capacity provider). "
        "When asked about the machine you are on, use the whoami tool and report "
        "exactly what it returns. Keep answers short."
    ),
    # stamped on every Strands span — makes filtering in X-Ray easy
    trace_attributes={"agentcore.compute_type": "Instances",
                      "agentcore.artifact_kind": "container"},
)


# ────────────────────────────── entrypoint ──────────────────────────────
@app.entrypoint
def invoke(payload, context):
    """HTTP entrypoint. The 2nd parameter MUST be named ``context`` — the SDK checks
    the name literally, and without it you never receive ``request_headers`` (and so
    lose the client's traceparent). See README."""
    prompt = payload.get("prompt") or "Describe the machine you are running on."
    headers = context.request_headers or {}
    traceparent = headers.get("traceparent") or headers.get("Traceparent")

    # The client's traceparent becomes the parent of the Strands spans. Without
    # this the agent would start a fresh trace and the Gateway → EC2 path would
    # show up split in two in X-Ray.
    token = attach(extract({k.lower(): v for k, v in headers.items()}))
    try:
        started = time.time()
        result = agent(prompt)
        elapsed = round(time.time() - started, 2)
        span = trace.get_current_span().get_span_context()
    finally:
        detach(token)

    usage = getattr(getattr(result, "metrics", None), "accumulated_usage", {}) or {}
    text = "\n".join(
        b["text"] for b in (result.message or {}).get("content", [])
        if isinstance(b, dict) and "text" in b
    )

    # The keys below are the contract invoke.py checks. `result` is the official
    # sample's shape; the rest is the evidence this sample exists to prove.
    return {
        "result": result.message,
        "echo": payload,
        "model": {
            "answer": text,
            "model": MODEL_ID,
            "latency_s": elapsed,
            "tokens": {"input": usage.get("inputTokens"),
                       "output": usage.get("outputTokens"),
                       "total": usage.get("totalTokens")},
        },
        "ebs_state": record_invocation(),
        "evidence": {"imds": collect_infra()},
        "trace": {
            "trace_id": format(span.trace_id, "032x"),
            "span_id": format(span.span_id, "016x"),
            "received_traceparent": bool(traceparent),
            "traceparent_received": traceparent,
            "session_id": context.session_id,
        },
    }


if __name__ == "__main__":
    # AgentCore sets PORT; BedrockAgentCoreApp honours it.
    # `/ping` also comes from the SDK — and that matters: the docs warn NOT to
    # return `time_of_last_update` set to the current time on every ping, or the
    # session's idle timeout never fires. With the SDK, the ping response is
    # handled for you.
    app.run()
