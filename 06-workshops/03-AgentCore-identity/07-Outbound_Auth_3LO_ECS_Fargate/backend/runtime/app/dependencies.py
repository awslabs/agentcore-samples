# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""FastAPI dependencies."""

import logging

from fastapi import Depends, Header

from backend.runtime.app.config import Settings, get_settings
from backend.runtime.services.agent_service import AgentService
from backend.shared.alb_auth import get_user_email_from_jwt

_agent_service: AgentService | None = None
logger = logging.getLogger(__name__)

_agent_service: AgentService | None = None


def get_current_user(
    x_amzn_oidc_data: str = Header(..., include_in_schema=False),
    settings: Settings = Depends(get_settings),
) -> str:
    """Extract user email from ALB OIDC JWT."""
    return get_user_email_from_jwt(x_amzn_oidc_data, settings.aws_region)


def get_agent_service(settings: Settings = Depends(get_settings)) -> AgentService:
    """Get AgentService instance."""
    global _agent_service
    if _agent_service is None:
        _agent_service = AgentService(
            aws_region=settings.aws_region,
            identity_aws_region=settings.identity_aws_region,
            s3_bucket_name=settings.s3_bucket_name,
            inference_profile_id=settings.inference_profile_id,
            session_binding_url=settings.session_binding_url,
            github_provider_name=settings.github_provider_name,
            github_api_base=settings.github_api_base,
        )
    return _agent_service
