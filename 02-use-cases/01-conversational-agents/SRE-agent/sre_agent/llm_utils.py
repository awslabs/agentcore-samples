#!/usr/bin/env python3
"""
Centralized LLM utilities with improved error handling.

This module provides a single point for LLM creation with proper error handling
for authentication, access, and configuration issues.
"""

import logging
from typing import Any, Dict

from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrock

from .constants import SREConstants

logger = logging.getLogger(__name__)


class LLMProviderError(Exception):
    """Exception raised when LLM provider creation fails."""

    pass


class LLMAuthenticationError(LLMProviderError):
    """Exception raised when LLM authentication fails."""

    pass


class LLMAccessError(LLMProviderError):
    """Exception raised when LLM access is denied."""

    pass


def create_llm_with_error_handling(provider: str = "bedrock", **kwargs):
    """Create LLM instance with proper error handling and helpful error messages.

    Args:
        provider: LLM provider ("anthropic" or "bedrock")
        **kwargs: Additional configuration overrides

    Returns:
        LLM instance

    Raises:
        LLMProviderError: For general provider errors
        LLMAuthenticationError: For authentication failures
        LLMAccessError: For access/permission failures
        ValueError: For unsupported providers
    """
    if provider not in ["anthropic", "bedrock"]:
        raise ValueError(
            f"Unsupported provider: {provider}. Use 'anthropic' or 'bedrock'"
        )

    logger.info(f"Creating LLM with provider: {provider}")

    try:
        config = SREConstants.get_model_config(provider, **kwargs)

        if provider == "anthropic":
            logger.info(f"Creating Anthropic LLM - Model: {config['model_id']}")
            return _create_anthropic_llm(config)
        else:  # bedrock
            logger.info(
                f"Creating Bedrock LLM - Model: {config['model_id']}, Region: {config['region_name']}"
            )
            return _create_bedrock_llm(config)

    except Exception as e:
        error_msg = _get_helpful_error_message(provider, e)
        logger.error(f"Failed to create LLM: {error_msg}")

        # Classify the error type for better handling
        if _is_auth_error(e):
            raise LLMAuthenticationError(error_msg) from e
        elif _is_access_error(e):
            raise LLMAccessError(error_msg) from e
        else:
            raise LLMProviderError(error_msg) from e


def _create_anthropic_llm(config: Dict[str, Any]):
    """Create Anthropic LLM instance."""
    return ChatAnthropic(
        model=config["model_id"],
        max_tokens=config["max_tokens"],
        temperature=config["temperature"],
    )


def _create_bedrock_llm(config: Dict[str, Any]):
    """Create Bedrock LLM instance."""
    return ChatBedrock(
        model_id=config["model_id"],
        region_name=config["region_name"],
        model_kwargs={
            "temperature": config["temperature"],
            "max_tokens": config["max_tokens"],
        },
    )


def _is_auth_error(error: Exception) -> bool:
    """Check if error is authentication-related."""
    error_str = str(error).lower()
    auth_keywords = [
        "authentication",
        "unauthorized",
        "invalid credentials",
        "api key",
        "access key",
        "token",
        "permission denied",
        "403",
        "401",
    ]
    return any(keyword in error_str for keyword in auth_keywords)


def _is_access_error(error: Exception) -> bool:
    """Check if error is access/permission-related."""
    error_str = str(error).lower()
    access_keywords = [
        "access denied",
        "forbidden",
        "not authorized",
        "insufficient permissions",
        "quota exceeded",
        "rate limit",
        "service unavailable",
        "region not supported",
    ]
    return any(keyword in error_str for keyword in access_keywords)


def _get_helpful_error_message(provider: str, error: Exception) -> str:
    """Generate helpful error message based on provider and error type."""
    base_error = str(error)

    if provider == "anthropic":
        if _is_auth_error(error):
            return (
                f"Anthropic authentication failed: {base_error}\n"
                "Solutions:\n"
                "  1. Set ANTHROPIC_API_KEY environment variable\n"
                "  2. Check if your API key is valid and active\n"
                "  3. Try running: export ANTHROPIC_API_KEY='your-key-here'\n"
                "  4. Or switch to Bedrock: sre-agent --provider bedrock"
            )
        elif _is_access_error(error):
            return (
                f"Anthropic access denied: {base_error}\n"
                "Solutions:\n"
                "  1. Check if your account has sufficient credits\n"
                "  2. Verify your API key has the required permissions\n"
                "  3. Check rate limits and usage quotas\n"
                "  4. Or switch to Bedrock: sre-agent --provider bedrock"
            )
        else:
            return (
                f"Anthropic provider error: {base_error}\n"
                "Solutions:\n"
                "  1. Check your internet connection\n"
                "  2. Verify Anthropic service status\n"
                "  3. Try again in a few minutes\n"
                "  4. Or switch to Bedrock: sre-agent --provider bedrock"
            )

    else:  # bedrock
        if _is_auth_error(error):
            return (
                f"Amazon Bedrock authentication failed: {base_error}\n"
                "Solutions:\n"
                "  1. Configure AWS credentials (aws configure)\n"
                "  2. Set AWS_PROFILE environment variable\n"
                "  3. Check IAM permissions for Bedrock access\n"
                "  4. Verify your AWS credentials are valid\n"
                "  5. Or switch to Anthropic: sre-agent --provider anthropic"
            )
        elif _is_access_error(error):
            return (
                f"Amazon Bedrock access denied: {base_error}\n"
                "Solutions:\n"
                "  1. Enable Claude models in Bedrock console\n"
                "  2. Request model access for your AWS account\n"
                "  3. Check if the region supports Bedrock\n"
                "  4. Verify IAM permissions for bedrock:InvokeModel\n"
                "  5. Or switch to Anthropic: sre-agent --provider anthropic"
            )
        else:
            return (
                f"Amazon Bedrock provider error: {base_error}\n"
                "Solutions:\n"
                "  1. Check AWS service status\n"
                "  2. Verify the region supports Bedrock\n"
                "  3. Try a different AWS region\n"
                "  4. Check your internet connection\n"
                "  5. Or switch to Anthropic: sre-agent --provider anthropic"
            )


def validate_provider_access(provider: str = "bedrock", **kwargs) -> bool:
    """Validate if the specified provider is accessible.

    Args:
        provider: LLM provider to validate
        **kwargs: Additional configuration

    Returns:
        True if provider is accessible, False otherwise
    """
    try:
        create_llm_with_error_handling(provider, **kwargs)
        # Try a simple test call to validate access
        # Note: This is a minimal validation - actual usage may still fail
        logger.info(f"Provider {provider} validation successful")
        return True
    except Exception as e:
        logger.warning(f"Provider {provider} validation failed: {e}")
        return False


def get_recommended_provider() -> str:
    """Get recommended provider based on availability.

    Returns:
        Recommended provider name
    """
    # Try bedrock first (default), then anthropic
    for provider in ["bedrock", "anthropic"]:
        if validate_provider_access(provider):
            logger.info(f"Recommended provider: {provider}")
            return provider

    logger.warning("No providers are immediately accessible - defaulting to bedrock")
    return "bedrock"
