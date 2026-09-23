"""
Security configuration and validation utilities for the bearer token injection demo.

This module provides security-focused configuration and validation functions
to ensure secure handling of bearer tokens and API requests.
"""

import re
import os
from typing import Dict, Any, Optional
from urllib.parse import urlparse


class SecurityConfig:
    """Security configuration constants and validation methods."""

    # Maximum request body size (1MB)
    MAX_REQUEST_BODY_SIZE = 1024 * 1024

    # Maximum token length
    MAX_TOKEN_LENGTH = 2048

    # Allowed tool name pattern (alphanumeric, hyphens, underscores)
    TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

    # Required security headers
    SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    # API rate limits
    DEFAULT_RATE_LIMIT = 100
    DEFAULT_BURST_LIMIT = 200
    DEFAULT_DAILY_QUOTA = 1000

    @staticmethod
    def validate_bearer_token(token: str) -> bool:
        """
        Validate bearer token format and length.

        Args:
            token: Bearer token to validate

        Returns:
            True if token is valid, False otherwise
        """
        if not token or not isinstance(token, str):
            return False

        # Remove 'Bearer ' prefix if present
        if token.startswith("Bearer "):
            token = token[7:]

        # Check length
        if len(token) > SecurityConfig.MAX_TOKEN_LENGTH:
            return False

        # Basic format validation (base64-like characters)
        if not re.match(r"^[A-Za-z0-9+/=_-]+$", token):
            return False

        return True

    @staticmethod
    def validate_tool_name(tool_name: str) -> bool:
        """
        Validate tool name format.

        Args:
            tool_name: Tool name to validate

        Returns:
            True if tool name is valid, False otherwise
        """
        if not tool_name or not isinstance(tool_name, str):
            return False

        if len(tool_name) > 100:  # Reasonable length limit
            return False

        return bool(SecurityConfig.TOOL_NAME_PATTERN.match(tool_name))

    @staticmethod
    def validate_url(url: str, require_https: bool = True) -> bool:
        """
        Validate URL format and security requirements.

        Args:
            url: URL to validate
            require_https: Whether to require HTTPS protocol

        Returns:
            True if URL is valid, False otherwise
        """
        if not url or not isinstance(url, str):
            return False

        try:
            parsed = urlparse(url)

            if require_https and parsed.scheme != "https":
                return False

            if not parsed.netloc:
                return False

            return True
        except (ValueError, TypeError):
            return False

    @staticmethod
    def sanitize_log_data(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize data for logging by removing sensitive information.

        Args:
            data: Data dictionary to sanitize

        Returns:
            Sanitized data dictionary
        """
        sensitive_keys = {
            "token",
            "password",
            "secret",
            "key",
            "authorization",
            "x-asana-token",
            "bearer",
            "api_key",
            "access_token",
        }

        sanitized = {}
        for key, value in data.items():
            key_lower = key.lower()
            if any(sensitive in key_lower for sensitive in sensitive_keys):
                sanitized[key] = "[REDACTED]"
            elif isinstance(value, dict):
                sanitized[key] = SecurityConfig.sanitize_log_data(value)
            else:
                sanitized[key] = value

        return sanitized

    @staticmethod
    def get_environment_config() -> Dict[str, str]:
        """
        Get security-related environment configuration.

        Returns:
            Dictionary of environment configuration
        """
        return {
            "DEMO_USERNAME": os.environ.get("DEMO_USERNAME", "testuser"),
            "DEMO_SECRET_NAME": os.environ.get("DEMO_SECRET_NAME", "asana_integration_demo_agent"),
            "ROLE_NAME": os.environ.get("ROLE_NAME", "AgentCoreGwyAsanaIntegrationRole"),
            "POLICY_NAME": os.environ.get("POLICY_NAME", "AgentCoreGwyAsanaIntegrationPolicy"),
            "MAX_REQUEST_SIZE": os.environ.get("MAX_REQUEST_SIZE", str(SecurityConfig.MAX_REQUEST_BODY_SIZE)),
            "RATE_LIMIT": os.environ.get("RATE_LIMIT", str(SecurityConfig.DEFAULT_RATE_LIMIT)),
        }


def validate_request_payload(payload: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Validate incoming request payload for security issues.

    Args:
        payload: Request payload to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(payload, dict):
        return False, "Payload must be a dictionary"

    # Validate tool_name
    tool_name = payload.get("tool_name")
    if not SecurityConfig.validate_tool_name(tool_name):
        return False, "Invalid tool_name format"

    # Validate string fields don't contain suspicious content
    string_fields = ["name", "notes", "project", "task_gid", "workspace"]
    for field in string_fields:
        value = payload.get(field)
        if value is not None:
            if not isinstance(value, str):
                return False, f"Field {field} must be a string"

            if len(value) > 1000:  # Reasonable length limit
                return False, f"Field {field} is too long"

            # Basic XSS prevention
            if any(char in value for char in ["<", ">", '"', "'"]):
                return False, f"Field {field} contains invalid characters"

    return True, None


def create_secure_response_headers() -> Dict[str, str]:
    """
    Create secure HTTP response headers.

    Returns:
        Dictionary of security headers
    """
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Headers": "Content-Type,X-Asana-Token,Authorization",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    }

    # Add security headers
    headers.update(SecurityConfig.SECURITY_HEADERS)

    # Note: In production, restrict CORS origins
    # For demo purposes, we allow all origins
    headers["Access-Control-Allow-Origin"] = "*"

    return headers
