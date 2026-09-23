# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Configuration management for OAuth sidecar service."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    log_level: str = "INFO"
    environment: str = "unknown"
    service_name: str = "session-binding"
    aws_region: str = "eu-central-1"
    identity_aws_region: str | None = None

    @property
    def identity_region(self) -> str:
        """Get Identity service region, fallback to AWS region."""
        return self.identity_aws_region or self.aws_region

    model_config = {"case_sensitive": False}


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
