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

HARNESS_ARN = os.environ["HARNESS_ARN"]
REGION = os.getenv("AWS_DEFAULT_REGION")


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
            )
            full = ""
            for event in resp["stream"]:
                if "contentBlockDelta" in event:
                    txt = event["contentBlockDelta"].get("delta", {}).get("text", "")
                    if txt:
                        full += txt
                        yield {"data": json.dumps({"type": "text_delta", "text": txt})}
            if full:
                sessions[sid].append({"role": "assistant", "content": [{"text": full}]})
            yield {"data": json.dumps({"type": "done"})}
        except Exception as e:
            logger.error(str(e), exc_info=True)
            yield {"data": json.dumps({"type": "error", "message": str(e)})}

    return EventSourceResponse(stream())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)  # nosec B104
