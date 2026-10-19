# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Demo console API for the AgentCore FSI POC.

A thin layer over the four AgentCore services. It exists because the browser
cannot hold IAM credentials or perform SigV4 signing against AgentCore — this
process signs with its own execution role and exposes a small, explicit surface.

This module is the composition root: app setup, CORS, auth wiring, and the health
probe. One router per AgentCore service lives alongside it:

  routers_config.py    GET  /api/config            deployment identifiers
  routers_runtime.py   POST /api/assess            run an assessment (streams)
  routers_registry.py  GET  /api/registry/records  browse the governed catalog
                       POST /api/registry/search   semantic + keyword discovery
                       POST /api/registry/records/{id}/status  approval workflow
  routers_gateway.py   GET  /api/gateway/tools     list the Gateway's MCP tools
                       POST /api/gateway/invoke    invoke one tool directly
  routers_memory.py    GET  /api/memory/{customer} assessment timeline
"""

import logging
import os
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

import routers_config
import routers_gateway
import routers_memory
import routers_registry
import routers_runtime
from auth import verify_request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Interactive docs and the OpenAPI schema are disabled: they register directly
# on `app`, outside the router-level auth dependency, so leaving them on would
# let any caller who holds the SigV4 credential read the full API surface
# without a valid ID token.
app = FastAPI(
    title="AgentCore FSI Demo Console",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Origins allowed to call this API. Vite's dev server is always permitted; the
# deployed Amplify origin is injected at deploy time.
ALLOWED_ORIGINS = [
    origin
    for origin in [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        os.environ.get("CONSOLE_ORIGIN", ""),
    ]
    if origin
]

# When deployed behind a Lambda Function URL, the URL's own CORS configuration
# adds the Access-Control-* headers. Adding them here too produces a duplicated
# Access-Control-Allow-Origin, which browsers reject outright ("the header
# contains multiple values"). So the app only handles CORS in local development,
# where uvicorn is reached directly.
if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST", "OPTIONS"],
        # X-Amz-* and X-Id-Token are needed because the browser SigV4-signs
        # requests and carries the Cognito ID token in a separate header.
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Id-Token",
            "X-Amz-Date",
            "X-Amz-Security-Token",
            "X-Amz-Content-Sha256",
        ],
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Unauthenticated liveness probe.

    Deliberately does not report whether auth is enabled — that would tell an
    unauthenticated caller whether the AUTH_DISABLED bypass is active.
    """
    return {"status": "ok"}


# Every service route requires a valid Cognito ID token. Applying the dependency
# at mount time (rather than per-endpoint) means a newly added route is protected
# by default — the failure mode is a locked route, not an open one.
# /api/health is registered on `app` above so probes can reach it.
#
# Each router is mounted on `app` directly. Nesting routers (including them into
# an intermediate APIRouter and mounting that) does not work on this FastAPI
# version: the nested routes end up with a null path and silently disappear,
# leaving an app that serves only /api/health.
for service_router in (
    routers_config.router,
    routers_runtime.router,
    routers_registry.router,
    routers_gateway.router,
    routers_memory.router,
):
    app.include_router(
        service_router, prefix="/api", dependencies=[Depends(verify_request)]
    )
