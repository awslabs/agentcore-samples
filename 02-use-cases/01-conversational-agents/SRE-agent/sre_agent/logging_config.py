#!/usr/bin/env python3

import logging
import os
from typing import Optional


def _configure_http_loggers(debug_enabled: bool = False) -> None:
    """Configure HTTP client loggers based on debug setting."""
    http_loggers = [
        "httpx",
        "httpcore",
        "streamable_http",
        "mcp.client.streamable_http",
        "anthropic._client",
        "anthropic._base_client",
    ]

    for logger_name in http_loggers:
        http_logger = logging.getLogger(logger_name)
        if debug_enabled:
            http_logger.setLevel(logging.DEBUG)
        else:
            http_logger.setLevel(logging.WARNING)


def configure_logging(debug: Optional[bool] = None) -> bool:
    """Configure logging with basicConfig based on debug setting.

    Args:
        debug: Enable debug logging. If None, checks DEBUG environment variable.

    Returns:
        bool: Whether debug logging is enabled
    """
    # Determine debug setting
    if debug is None:
        debug = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

    # Set log level based on debug setting
    log_level = logging.DEBUG if debug else logging.INFO

    # Configure logging with basicConfig
    logging.basicConfig(
        level=log_level,
        # Define log message format
        format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
    )

    # Configure HTTP loggers
    _configure_http_loggers(debug)

    # Configure MCP logger
    mcp_logger = logging.getLogger("mcp")
    if debug:
        mcp_logger.setLevel(logging.DEBUG)
    else:
        mcp_logger.setLevel(logging.WARNING)

    return debug


def should_show_debug_traces() -> bool:
    """Check if debug traces should be shown."""
    return os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
