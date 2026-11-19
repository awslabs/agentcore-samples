# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""CDK deployment configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CdkConfig(BaseSettings):
    """CDK deployment configuration. Loaded from environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    aws_region: str = Field(
        default="us-east-1",
        validation_alias="AWS_REGION",
        description="AWS Region for deployment",
    )
    aws_account: str | None = Field(
        default=None,
        validation_alias="CDK_DEFAULT_ACCOUNT",
        description="AWS account ID",
    )
    keycloak_domain: str = Field(
        default="keycloak.agentcore-sample.example.com",
        validation_alias="KEYCLOAK_DOMAIN",
        description=(
            "Private DNS name for Keycloak. The sample creates a Route 53 private hosted zone for the parent "
            "domain and issues the ALB certificate for this name from the private CA."
        ),
    )
    keycloak_image: str = Field(
        default="quay.io/keycloak/keycloak:26.8.2",
        validation_alias="KEYCLOAK_IMAGE",
        description="Keycloak container image",
    )
    realm_name: str = Field(default="agentcore", validation_alias="KEYCLOAK_REALM", description="Keycloak realm")
    client_id: str = Field(
        default="agentcore-m2m",
        validation_alias="KEYCLOAK_CLIENT_ID",
        description="Confidential Keycloak client used for the client_credentials (M2M) grant",
    )
    audience: str = Field(
        default="agentcore-gateway",
        validation_alias="KEYCLOAK_AUDIENCE",
        description="Audience added to access tokens and allowed by the AgentCore Gateway authorizer",
    )
