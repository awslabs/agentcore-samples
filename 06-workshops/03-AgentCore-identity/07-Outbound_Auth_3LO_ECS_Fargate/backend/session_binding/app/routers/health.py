# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Health check endpoints."""

from fastapi import APIRouter, Depends

from backend.session_binding.app.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/ping", include_in_schema=False)
async def health(settings: Settings = Depends(get_settings)) -> dict:
    """Health check for ECS and ALB."""
    return {
        "status": "healthy",
        "env": settings.environment,
        "service": settings.service_name,
    }
