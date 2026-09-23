# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Session binding endpoint."""

import logging
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from backend.session_binding.app.config import Settings, get_settings
from backend.shared.alb_auth import get_user_email_from_jwt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth2", tags=["oauth"])

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

ERROR_CODE_TO_STATUS = {
    "UnauthorizedException": 401,
    "AccessDeniedException": 401,
    "ValidationException": 400,
    "ResourceNotFoundException": 404,
}


def get_current_user(
    x_amzn_oidc_data: str = Header(..., include_in_schema=False),
    settings: Settings = Depends(get_settings),
) -> str:
    """Extract user email from ALB OIDC JWT."""
    return get_user_email_from_jwt(x_amzn_oidc_data, settings.aws_region)


@router.get("/session-binding", response_class=HTMLResponse)
async def oauth_session_binding(
    session_id: str = Query(..., description="Session URI from AgentCore Identity"),
    user_id: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Handle OAuth2 session binding from external providers."""
    logger.debug(f"Session binding - session_id: {session_id}, user_id: {user_id}")

    client = boto3.client("bedrock-agentcore", region_name=settings.identity_region)

    try:
        client.complete_resource_token_auth(
            sessionUri=session_id,
            userIdentifier={"userId": user_id},
        )
        logger.debug(f"✓ Session binding completed for user: {user_id}")
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]
        status = ERROR_CODE_TO_STATUS.get(error_code, 500)
        logger.exception(f"✗ Failed to complete session binding: {error_code}")
        raise HTTPException(status_code=status, detail=error_msg) from e

    return HTMLResponse(content=(TEMPLATES_DIR / "success.html").read_text())
