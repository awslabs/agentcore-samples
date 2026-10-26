"""sandbox_client — resilient wrapper for invoking the Sandbox runtime.

Handles:
- Forwarding the coding agent's inbound runtimeSessionId to the sandbox
- Retry with exponential backoff on transient failures (sandbox crash/restart)
- Informing the caller when the sandbox restarted (so the agent knows state may have changed)

Auth is SigV4 via the coding agent's execution role.
"""
import json
import os
import time

import boto3
from botocore.exceptions import ClientError

_client = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-east-1"))

MAX_RETRIES = 3
RETRY_DELAYS = [5, 10, 20]  # seconds between retries (exponential)

_last_boot_id: str = ""


def invoke_sandbox(action: str, session_id: str, ticket_prefix: str, sandbox_arn: str = "", **args) -> dict:
    """Call the sandbox runtime with retry logic. Detects sandbox restarts via boot_id changes.

    sandbox_arn selects WHICH sandbox to drive (e.g. the Swift sandbox for a Swift ticket).
    The orchestrator passes the runtime-appropriate ARN per ticket; falls back to the
    SANDBOX_ARN env var for backwards compatibility / local testing.
    """
    global _last_boot_id

    sandbox_arn = sandbox_arn or os.environ.get("SANDBOX_ARN", "")
    if not sandbox_arn:
        return {"error": "no sandbox_arn provided and SANDBOX_ARN not set"}
    if not session_id or len(session_id) < 33:
        return {"error": f"invalid session_id (need >=33 chars): {session_id!r}"}
    if not ticket_prefix:
        return {"error": "ticket_prefix is required"}

    body = {"action": action, "ticket_prefix": ticket_prefix, **args}
    payload = json.dumps(body).encode("utf-8")

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = _client.invoke_agent_runtime(
                agentRuntimeArn=sandbox_arn,
                runtimeSessionId=session_id,
                payload=payload,
                contentType="application/json",
                accept="application/json",
            )
            raw = resp["response"].read()
            try:
                result = json.loads(raw)
            except (ValueError, TypeError):
                result = {"raw": raw.decode("utf-8", "replace")}

            # Detect sandbox restart (boot_id changed)
            current_boot = result.get("sandbox_boot_id", "")
            if _last_boot_id and current_boot and current_boot != _last_boot_id:
                result["_sandbox_restarted"] = True
                result["_previous_boot_id"] = _last_boot_id
                result["_notice"] = (
                    "SANDBOX RESTARTED: The sandbox microVM was replaced since the last call. "
                    "Previously installed packages may be lost. The sandbox will attempt to "
                    "restore from its checkpoint. You may need to re-install dependencies."
                )
            if current_boot:
                _last_boot_id = current_boot

            return result

        except ClientError as e:
            code = e.response["Error"]["Code"]
            last_error = f"{code}: {e.response['Error'].get('Message', str(e))}"
            # RuntimeClientError = sandbox returned non-200 (crash, OOM, etc.)
            # ThrottlingException, ServiceUnavailableException = transient
            if code in ("RuntimeClientError", "ThrottlingException",
                        "ServiceUnavailableException", "InternalServerException"):
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    time.sleep(delay)
                    continue
            # Non-retryable error
            return {"error": last_error, "retryable": False}

        except Exception as e:
            last_error = str(e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                continue
            return {"error": last_error, "retryable": False}

    return {"error": f"sandbox unreachable after {MAX_RETRIES + 1} attempts: {last_error}", "retryable": True}
