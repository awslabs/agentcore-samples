# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""CDK deployment configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PingFederateConfig(BaseSettings):
    """PingFederate DevOps credentials. Loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_prefix="PING_IDENTITY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    devops_user: str = Field(description="PingFederate DevOps user email")
    devops_key: str = Field(description="PingFederate DevOps key")


class CdkConfig(BaseSettings):
    """CDK deployment configuration."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    aws_region: str = Field(
        default="us-east-1",
        validation_alias="AWS_REGION",
        description="AWS region for deployment",
    )
    aws_account: str | None = Field(
        default=None,
        validation_alias="CDK_DEFAULT_ACCOUNT",
        description="AWS account ID",
    )
    suffix: str = Field(default="sample", description="Suffix for resource naming")
    deploy_lattice: bool = Field(
        default=False,
        validation_alias="DEPLOY_LATTICE",
        description="Deploy VPC Lattice resources (set to true for self-managed Lattice, false for AgentCore-managed)",
    )
    certificate_arn: str = Field(
        validation_alias="CERTIFICATE_ARN",
        description="ARN of a publicly trusted ACM certificate for the PingFederate domain",
    )
    ping_domain: str = Field(
        validation_alias="PING_DOMAIN",
        description="Domain name matching the ACM certificate (e.g., ping.example.com)",
    )

    ping_federate_config: PingFederateConfig = Field(
        default_factory=PingFederateConfig,
        description="PingFederate DevOps credentials",
    )
