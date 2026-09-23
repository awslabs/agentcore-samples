# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""CDK deployment configuration."""

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OidcConfig(BaseSettings):
    """OIDC configuration for ALB authentication. Loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(env_prefix="OIDC_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    issuer: str = Field(description="OIDC issuer URL")
    authorization_endpoint: str = Field(description="Authorization endpoint URL")
    token_endpoint: str = Field(description="Token endpoint URL")
    user_info_endpoint: str = Field(
        default="https://graph.microsoft.com/oidc/userinfo",
        description="UserInfo endpoint URL",
    )
    secret_name: str = Field(
        default="agent-oauth/credentials",
        description="Secrets Manager secret name for client credential",
    )
    scope: str = Field(default="openid email profile", description="OAuth scopes")


class DnsConfig(BaseSettings):
    """DNS configuration for Route 53. Loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(env_prefix="DNS_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    domain_name: str = Field(description="Domain name for the application")
    hosted_zone_id: str = Field(description="Route53 hosted zone ID")


class CdkConfig(BaseModel):
    """CDK deployment configuration."""

    aws_region: str = Field(default="eu-west-1", description="AWS region for main stack deployment")
    identity_aws_region: str = Field(default="eu-central-1", description="AWS region for identity stack deployment")
    aws_account: str | None = Field(default=None, description="AWS account ID")
    suffix: str = Field(default="sample", description="Suffix for resource naming")

    inference_profile_id: str = Field(
        default="eu.anthropic.claude-haiku-4-5-20251001-v1:0",
        description="Bedrock inference profile ID",
    )

    @property
    def model_id(self) -> str:
        """Extract model ID from inference profile by removing region prefix."""
        parts = self.inference_profile_id.split(".", 1)
        return parts[1] if len(parts) > 1 else parts[0]

    dns_config: DnsConfig = Field(
        default_factory=DnsConfig,
        description="DNS configuration for Route 53",
    )
    github_provider_name: str = Field(
        default="github-oauth-client-i5yd5",
        description="AgentCore Identity OAuth provider name for GitHub (registered in AgentCore Identity)",
    )
    github_api_base: str = Field(
        default="https://api.github.com",
        description="GitHub API base URL",
    )
    oidc_config: OidcConfig = Field(
        default=OidcConfig(),
        description="Entra ID OIDC configuration for ALB authentication",
    )
