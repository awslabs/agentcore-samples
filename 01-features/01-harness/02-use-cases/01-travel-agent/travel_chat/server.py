#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["fastapi", "uvicorn", "boto3", "sse-starlette"]
# ///
"""
Travel Guide Chat Server — FastAPI backend for the Travel Agent harness.

Proxies chat messages to invoke_harness with SSE streaming.

Usage:
    export HARNESS_ARN="arn:aws:bedrock-agentcore:REGION:ACCOUNT:harness/HARNESS_ID"
    python server.py

    # Or with uvicorn directly
    HARNESS_ARN=<arn> uvicorn server:app --host 0.0.0.0 --port 8000

Then open http://localhost:8000 in your browser.
"""

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

import boto3
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Fail with a usable message instead of a bare KeyError traceback on import when
# the one required piece of configuration is missing.
HARNESS_ARN = os.getenv("HARNESS_ARN")
if not HARNESS_ARN:
    raise SystemExit(
        "HARNESS_ARN is not set. Create a harness first (e.g. run travel_agent.py), "
        "then:\n"
        '  export HARNESS_ARN="arn:aws:bedrock-agentcore:REGION:ACCOUNT:harness/HARNESS_ID"\n'
        "  python server.py"
    )

REGION = os.getenv("AWS_DEFAULT_REGION")

# Match the model the CLI script uses. `model` is optional on InvokeHarness, but
# passing it explicitly keeps the chat server and travel_agent.py on the same
# model instead of relying on a server-side default that could differ or change.
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

# The three error members the InvokeHarness stream can carry (per the boto3
# model). They arrive as events rather than raising, so they are checked
# explicitly while iterating; a failed call raises ClientError and is caught by
# the surrounding try/except.
STREAM_ERROR_KEYS = (
    "internalServerException",
    "validationException",
    "runtimeClientError",
)


def make_client():
    return boto3.client("bedrock-agentcore", region_name=REGION)


client = make_client()
sessions = {}


@asynccontextmanager
async def lifespan(app):
    logger.info(f"Chat app started. Harness: {HARNESS_ARN}")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))


@app.post("/session")
async def new_session():
    sid = str(uuid.uuid4()).upper()
    sessions[sid] = []
    return {"session_id": sid}


@app.post("/chat")
async def chat(req: dict):
    msg = req.get("message", "").strip()
    sid = req.get("session_id", "")
    if not msg or not sid:
        raise HTTPException(400, "message and session_id required")

    if sid not in sessions:
        sessions[sid] = []
    sessions[sid].append({"role": "user", "content": [{"text": msg}]})

    async def stream():
        try:
            resp = client.invoke_harness(
                harnessArn=HARNESS_ARN,
                runtimeSessionId=sid,
                messages=sessions[sid],
                model={"bedrockModelConfig": {"modelId": MODEL_ID}},
            )
            full = ""
            for event in resp["stream"]:
                if "contentBlockDelta" in event:
                    txt = event["contentBlockDelta"].get("delta", {}).get("text", "")
                    if txt:
                        full += txt
                        yield {"data": json.dumps({"type": "text_delta", "text": txt})}
                    continue
                # The stream carries errors as their own event members; without
                # this the model turn could fail mid-way (bad request, or the
                # model hitting its token limit) and the client would just see
                # the stream end with no error and a truncated or empty reply.
                err = next((event[k] for k in STREAM_ERROR_KEYS if k in event), None)
                if err is not None:
                    raise RuntimeError(str(err))
            if full:
                sessions[sid].append({"role": "assistant", "content": [{"text": full}]})
            yield {"data": json.dumps({"type": "done"})}
        except Exception as e:
            logger.exception("invoke_harness stream failed")
            yield {"data": json.dumps({"type": "error", "message": str(e)})}

    return EventSourceResponse(stream())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)  # nosec B104
