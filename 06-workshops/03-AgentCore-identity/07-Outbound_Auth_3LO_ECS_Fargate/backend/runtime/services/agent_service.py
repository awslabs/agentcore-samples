# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Agent service for orchestrating agent creation and execution."""

import logging
from typing import AsyncGenerator

from strands import Agent
from strands.models import BedrockModel
from strands.session import S3SessionManager

from backend.runtime.agent import agent_factory
from backend.runtime.agent.tools.github import GitHubConfig

logger = logging.getLogger(__name__)


class AgentService:
    """Service for managing agent lifecycle."""

    def __init__(
        self,
        aws_region: str,
        identity_aws_region: str,
        s3_bucket_name: str,
        inference_profile_id: str,
        session_binding_url: str,
        github_provider_name: str,
        github_api_base: str,
    ):
        """Initialize agent service with configuration."""
        self.aws_region = aws_region
        self.identity_aws_region = identity_aws_region
        self.s3_bucket_name = s3_bucket_name
        self.inference_profile_id = inference_profile_id
        self.session_binding_url = session_binding_url
        self.github_provider_name = github_provider_name
        self.github_api_base = github_api_base

    def create_agent(
        self,
        session_id: str,
        workload_access_token: str,
        user_id: str,
    ) -> Agent:
        """Create configured agent instance."""
        session_manager = S3SessionManager(
            region_name=self.aws_region,
            session_id=session_id,
            bucket=self.s3_bucket_name,
            prefix=user_id,
        )

        github_config = GitHubConfig(
            session_binding_url=self.session_binding_url,
            github_api_base=self.github_api_base,
            provider_name=self.github_provider_name,
            aws_region=self.identity_aws_region,
            workload_access_token=workload_access_token,
        )

        model = BedrockModel(region_name=self.aws_region, model_id=self.inference_profile_id)

        return agent_factory(session_manager=session_manager, model=model, github_config=github_config)

    async def stream_response(
        self,
        user_message: str,
        session_id: str,
        user_id: str,
        workload_access_token: str,
    ) -> AsyncGenerator[str, None]:
        """Create agent and stream response."""
        agent = self.create_agent(session_id, workload_access_token, user_id)
        async for event in agent.stream_async(user_message):
            if "data" in event and "current_tool_use" not in event:
                yield event["data"]
