"""Loopback-only browser UI; sign for one operator-selected runtime on the server."""

import argparse
import json
import re
from pathlib import Path
from uuid import UUID

import boto3
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware


class Signal(BaseModel):
    session: UUID
    action: str
    data: dict


def application(runtime_arn=None, client=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])

    @app.get("/")
    def index():
        return FileResponse(Path(__file__).with_name("index.html"), headers={"X-Frame-Options": "DENY"})

    @app.get("/mode")
    def mode():
        return {"hosted": runtime_arn is not None}

    @app.post("/signal")
    def signal(message: Signal, request: Request):
        if request.headers.get("origin") not in {"http://localhost:7860", "http://127.0.0.1:7860"}:
            raise HTTPException(403)
        if not runtime_arn or message.action not in {"ice_config", "offer", "ice_candidate", "disconnect"}:
            raise HTTPException(400)
        result = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=str(message.session),
            contentType="application/json",
            accept="application/json",
            payload=json.dumps({"action": message.action, "data": message.data}).encode(),
        )
        body = result["response"]
        try:
            return json.loads(body.read())
        finally:
            body.close()

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-arn", help="Omit to signal the local agent at localhost:8080")
    arn = parser.parse_args().runtime_arn
    client = None
    if arn:
        match = re.fullmatch(r"arn:aws:bedrock-agentcore:([a-z0-9-]+):[0-9]{12}:runtime/[a-zA-Z0-9_-]+", arn)
        if not match:
            parser.error("Invalid Runtime ARN")
        client = boto3.client("bedrock-agentcore", region_name=match[1])
    uvicorn.run(application(arn, client), host="127.0.0.1", port=7860)
