"""Echo-reply agent for AgentCore Runtime benchmarking.

The service exposes the AgentCore Runtime contract:

- ``POST /invocations`` -> echoes the request back (NO LLM / agentic work).
- ``GET /ping``         -> health check used by the runtime.

Why echo-only: the benchmark measures runtime behavior (cold start, latency,
throughput), not model quality. Skipping the LLM call — and any heavy
dependencies — keeps the image minimal so the numbers reflect the runtime
itself, not library load time.

Cold start: ``cold_start`` is ``True`` for the first invocation served by a
fresh process and ``False`` afterwards. The benchmark reads this flag to split
cold vs warm latency.
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request

# Process start time and a one-shot cold-start flag.
_PROCESS_START = time.time()
_cold_lock = threading.Lock()
_cold_pending = True

app = FastAPI(title="Echo Benchmark Agent", version="1.0.0")


def _consume_cold_start() -> bool:
    """Return True only for the first invocation in this process."""
    global _cold_pending
    with _cold_lock:
        cold = _cold_pending
        _cold_pending = False
    return cold


@app.post("/invocations")
async def invoke(request: Request) -> dict[str, Any]:
    """Echo the request body back, with cold-start and timing metadata.

    The body is parsed leniently (any JSON, or raw text) rather than validated
    against a fixed schema: different runtimes deliver the payload differently
    (AgentCore wraps/forwards it), and a strict model returned HTTP 422. Since
    this is an echo, we accept whatever arrives.
    """
    cold = _consume_cold_start()
    raw = await request.body()
    try:
        echo: Any = json.loads(raw) if raw else None
    except ValueError:
        echo = raw.decode("utf-8", "replace")

    return {
        "output": {
            "echo": echo,
            "cold_start": cold,
            "pid": os.getpid(),
            "uptime_s": round(time.time() - _PROCESS_START, 3),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    }


@app.get("/ping")
async def ping() -> dict[str, str]:
    """Health check endpoint required by the AgentCore Runtime."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
