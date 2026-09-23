# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""GitHub tool configuration."""

from pydantic import BaseModel, Field


class GitHubConfig(BaseModel):
    """Configuration for GitHub tools."""

    session_binding_url: str = Field(..., description="Session Binding URL for the customer-managed service")
    github_api_base: str = Field(..., description="GitHub API base URL")
    provider_name: str = Field(..., description="Provider name in AgentCore Identity")
    workload_access_token: str = Field(..., description="AgentCore workload access token")
    aws_region: str = Field(default="eu-central-1", description="AWS region")
