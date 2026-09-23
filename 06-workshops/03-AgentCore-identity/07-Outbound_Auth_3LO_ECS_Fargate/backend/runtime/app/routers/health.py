# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Health check endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.runtime.app.config import Settings, get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    environment: str


@router.get("/ping", response_model=HealthResponse)
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Health check for ECS and ALB."""
    return HealthResponse(status="healthy", environment=settings.environment)
