"""
Talk A2A to the agent, and show which EC2 instance answered.

Run `python deploy.py` first.

    python invoke.py                          # message/send, tasks/get, whoami
    python invoke.py --prompt "what is 7+35?"

HOW THIS DIFFERS FROM AN HTTP AGENT
-----------------------------------
Sample 1 sends `{"prompt": "..."}` and gets a model answer back. Here the
payload is a JSON-RPC 2.0 envelope and the method names are A2A's:

    {"jsonrpc": "2.0", "id": "...", "method": "message/send",
     "params": {"message": {"role": "user", "kind": "message",
                            "messageId": "...",
                            "parts": [{"kind": "text", "text": "..."}]}}}

`InvokeAgentRuntime` carries that body through to `POST /` on port 9000.

Two details the older A2A spec got differently, and which cost a debugging
session if you copy an old example:

  * The method is `message/send`, NOT `tasks/send`. `tasks/send` was removed;
    a2a-sdk 0.3 rejects it as an unknown method.
  * A text part is `{"kind": "text", ...}`, NOT `{"type": "text", ...}`. The
    wire format is camelCase throughout (`messageId`, `contextId`, `taskId`).

The reply is a full A2A **task**, not a bare message:

    {"result": {"kind": "task", "id": ..., "contextId": ...,
                "status": {"state": "completed", ...},
                "artifacts": [{"name": "agent_response",
                               "parts": [{"kind": "text", "text": ...}]}],
                "history": [...]}}

The answer lives in `artifacts`, and `history` holds the whole exchange —
including one message per streamed chunk, which is why it is long.

THE AGENT CARD IS NOT REACHABLE THROUGH THIS API
------------------------------------------------
A2A discovery is a `GET /.well-known/agent-card.json`. `InvokeAgentRuntime` is
modelled as `POST /runtimes/{arn}/invocations` only — there is no GET and no way
to pick a path, so the card the agent serves cannot be fetched this way. There
is also no JSON-RPC fallback: `agent/getAuthenticatedExtendedCard` returns
`-32603 Authenticated card not supported`.

That is not a dead end on a CapacityProvider, and it is the one place where
owning the fleet genuinely buys you something here: the instances are in YOUR
VPC, so a peer agent in the same VPC can hit port 9000 on the instance directly
and read the card the normal A2A way. On serverless there is no such address.

STREAMING
---------
`message/stream` exists and the card advertises streaming, but it is pointless
through this API: `InvokeAgentRuntime` buffers the response body, so the SSE
frames only arrive once the task has already finished. Use `message/send`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "cp_config.json"

# InvokeAgentRuntime requires a session id of at least 33 characters.
SESSION_MIN_LEN = 33

DEFAULT_PROMPT = "Use the whoami tool and report exactly what it returns."


def data_client(region: str | None):
    """
    The data-plane client, straight from boto3 — but not with default settings.

    Note the service name: the data plane is `bedrock-agentcore`, a different
    service from the `bedrock-agentcore-control` client deploy.py uses.

    The long read timeout is NOT optional. A cold invoke waits for an EC2 instance
    to be launched, booted and seeded with your artifact before your agent sees
    the request — minutes, not seconds. botocore's default read timeout is 60 s,
    which would abort a perfectly healthy cold start.

    Retries are disabled on purpose: InvokeAgentRuntime is not idempotent, so a
    botocore-level retry during a slow cold start would open a SECOND session on
    a SECOND instance. The retry loop in `rpc()` below is the deliberate one —
    it reuses the same session id.
    """
    region = (
        region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or boto3.Session().region_name
    )
    if not region:
        sys.exit("No AWS region configured. Set AWS_REGION.")
    return boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(
            read_timeout=900,
            connect_timeout=30,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    )


def new_session_id(label: str = "a2a") -> str:
    return f"{label}-{uuid.uuid4().hex}".ljust(SESSION_MIN_LEN, "0")[:64]


class RpcError(RuntimeError):
    """A JSON-RPC error envelope came back instead of a result."""


def rpc(data, arn: str, session_id: str, method: str, params: dict | None = None,
        attempts: int = 6) -> tuple[dict, float]:
    """
    Send one JSON-RPC request, retrying the service's transient failures.

    As with MCP, that failure does NOT always surface as a botocore exception.
    The service cannot return a bare HTTP error on a JSON-RPC channel, so it
    answers 200 with an error envelope:

        {"jsonrpc": "2.0", "error": {"code": -32603, "message": "An internal
         error occurred while processing the request."}, "id": "..."}

    So the envelope has to be inspected, not just the HTTP call — a 200 here does
    not mean success. Two service-side codes have been observed with MCP, neither
    in the JSON-RPC spec's own range, and both apply here:

        -32603  "An internal error occurred while processing the request."
        -32010  "An error occurred when starting the runtime. Please check your
                 CloudWatch logs for more information."

    Latency is the signal that tells them apart from a real cold start:

        seconds  → no instance was placed; retry
        minutes  → an instance is really booting; never interrupt it

    Care is needed with one class of error, though: a *client* mistake (wrong
    method name, malformed part) also comes back as a JSON-RPC error, and
    retrying that six times just wastes six minutes. So -32601/-32602/-32600
    (unknown method, bad params, invalid request) are raised immediately.

    See Sample 2's README → "Errors come back inside the JSON-RPC envelope".
    """
    payload: dict = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method}
    if params is not None:
        payload["params"] = params

    # Client-side JSON-RPC errors. Retrying these cannot help.
    fatal_codes = {-32700, -32600, -32601, -32602}

    first_started = time.time()
    last = None
    for attempt in range(1, attempts + 1):
        started = time.time()
        try:
            response = data.invoke_agent_runtime(
                agentRuntimeArn=arn,
                runtimeSessionId=session_id,
                payload=json.dumps(payload).encode(),
                contentType="application/json",
                accept="application/json",
            )
            raw = response["response"].read().decode()
            body = json.loads(raw) if raw.strip() else {}
            if "error" in body:
                error = body["error"]
                code = error.get("code")
                message = f"JSON-RPC {code}: {error.get('message')}"
                if code in fatal_codes:
                    # A bug in this client, not a service flake. Fail loudly.
                    raise RuntimeError(message)
                raise RpcError(message)
            return body, time.time() - started
        except Exception as exc:  # noqa: BLE001 — transient service-side errors
            name = type(exc).__name__
            retryable = (
                "InternalServerException" in name
                or "RuntimeClientError" in name
                or isinstance(exc, RpcError)
            )
            if not retryable:
                raise
            last = exc
            if attempt < attempts:
                delay = min(15 * 2 ** (attempt - 1), 120)
                # Report this attempt's own latency, not the elapsed total — the
                # latency is what tells a flake apart from a cold start, and a
                # running total would bury it under the accumulated sleeps.
                print(f"    ({exc} after {time.time() - started:.1f}s"
                      f" — retry {attempt}/{attempts - 1} in {delay}s"
                      f"; {time.time() - first_started:.0f}s elapsed)", flush=True)
                time.sleep(delay)
    raise last


def send_message(data, arn: str, session_id: str, text: str,
                 context_id: str | None = None) -> tuple[dict, float]:
    """
    Send one A2A message and return the resulting task.

    Passing `contextId` is what makes a follow-up a follow-up: the server keeps
    the conversation under that context, so the agent remembers the earlier turn.
    """
    message: dict = {
        "role": "user",
        "kind": "message",
        "messageId": str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": text}],
    }
    if context_id:
        message["contextId"] = context_id
    return rpc(data, arn, session_id, "message/send", {"message": message})


def task_text(result: dict) -> str:
    """Pull the agent's answer out of an A2A task result."""
    task = result.get("result", {})
    parts = [
        part["text"]
        for artifact in task.get("artifacts", [])
        for part in artifact.get("parts", [])
        if part.get("kind") == "text"
    ]
    if parts:
        return "\n".join(parts)
    # No artifact: fall back to the last agent message in the history.
    for message in reversed(task.get("history", [])):
        if message.get("role") == "agent":
            return "".join(
                p.get("text", "") for p in message.get("parts", [])
            )
    return json.dumps(task, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    args = parser.parse_args()

    if not CONFIG.is_file():
        sys.exit(f"{CONFIG.name} not found — run `python deploy.py` first.")
    config = json.loads(CONFIG.read_text())

    data = data_client(config.get("region"))
    arn = config["runtimes"]["a2a"]["arn"]
    session_id = new_session_id()

    print(f"CapacityProvider : {config['capacityProviderId']}")
    print(f"Protocol         : {config['serverProtocol']}  (port 9000, JSON-RPC at /)")
    print(f"Model            : {config.get('modelId')}")
    print(f"session          : {session_id}\n")

    print("── 1. message/send — the A2A task ────────────────────────────────────")
    print("The first call is a cold start: an EC2 instance has to be launched and")
    print("seeded before the agent exists. Minutes, not seconds.\n", flush=True)
    result, elapsed = send_message(data, arn, session_id, args.prompt)
    task = result["result"]
    print(f"[{elapsed:.1f}s cold]")
    print(f"  kind      : {task.get('kind')}")
    print(f"  taskId    : {task.get('id')}")
    print(f"  contextId : {task.get('contextId')}")
    print(f"  state     : {task.get('status', {}).get('state')}")
    print(f"  history   : {len(task.get('history', []))} messages "
          f"(one per streamed chunk)\n")
    print(task_text(result))
    print()

    print("── 2. tasks/get — fetch the same task by id ──────────────────────────")
    print("A2A tasks are addressable after the fact. This is the part of the")
    print("protocol that has no equivalent in Sample 1's request/response.\n")
    fetched, elapsed = rpc(data, arn, session_id, "tasks/get", {"id": task["id"]})
    print(f"[{elapsed:.1f}s warm] state="
          f"{fetched['result'].get('status', {}).get('state')}\n")

    print("── 3. A follow-up in the same context ────────────────────────────────")
    print("Same contextId, so the agent still has the first turn in view.\n")
    result, elapsed = send_message(
        data, arn, session_id,
        "Add the CPU count you just reported to 40, using the add_numbers tool.",
        context_id=task["contextId"],
    )
    print(f"[{elapsed:.1f}s warm]")
    print(task_text(result))
    print()

    print("The A2A agent is running on an EC2 instance in your account, on the")
    print("instance type you chose. `serverProtocol: A2A` was the only change to")
    print("the runtime; the CapacityProvider config is identical to Sample 1's.")


if __name__ == "__main__":
    main()
