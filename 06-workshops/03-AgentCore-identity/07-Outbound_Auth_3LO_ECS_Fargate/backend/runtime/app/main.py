# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""FastAPI application for agent runtime."""

import logging

from fastapi import FastAPI

from backend.runtime.app.config import get_settings
from backend.runtime.app.routers import health, invocations

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = FastAPI(
    title=f"Agent Runtime - {settings.environment}",
    version="1.0.0",
)

app.include_router(health.router)
app.include_router(invocations.router)
