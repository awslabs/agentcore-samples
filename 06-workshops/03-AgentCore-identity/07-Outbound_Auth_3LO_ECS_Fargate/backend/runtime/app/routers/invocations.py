# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Agent invocation endpoints."""

import logging

import boto3
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from backend.runtime.app.config import Settings, get_settings
from backend.runtime.app.dependencies import get_agent_service, get_current_user
from backend.runtime.app.models import InvocationRequest
from backend.runtime.services.agent_service import AgentService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent"])


@router.post("/invocations")
async def invoke_agent(
    request: InvocationRequest,
    user_id: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    agent_service: AgentService = Depends(get_agent_service),
) -> StreamingResponse:
    """Invoke agent with streaming response."""
    try:
        agentcore = boto3.client("bedrock-agentcore", region_name=settings.identity_aws_region)
        response = agentcore.get_workload_access_token_for_user_id(
            workloadName=settings.workload_identity_name, userId=user_id
        )
        workload_access_token = response["workloadAccessToken"]

        return StreamingResponse(
            content=agent_service.stream_response(
                user_message=request.user_message,
                session_id=request.session_id,
                user_id=user_id,
                workload_access_token=workload_access_token,
            ),
            media_type="text/event-stream",
        )

    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=f"Agent invocation failed: {str(e)}") from e
