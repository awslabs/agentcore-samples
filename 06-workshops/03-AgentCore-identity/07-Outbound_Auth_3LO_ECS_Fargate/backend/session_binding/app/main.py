# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Session binding service - FastAPI application."""

import logging

from fastapi import FastAPI

from backend.session_binding.app.config import get_settings
from backend.session_binding.app.routers import session_binding, health

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = FastAPI(
    title="Session Binding Service",
    description="Handles OAuth2 3LO session binding from AgentCore Identity",
    version="1.0.0",
)

app.include_router(session_binding.router)
app.include_router(health.router)
