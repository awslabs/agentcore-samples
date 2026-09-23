# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Logging configuration for the Streamlit app.
Logs to both console and file for debugging.
"""

import logging
from pathlib import Path
from datetime import datetime

# Create logs directory
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Log file path
LOG_FILE = LOG_DIR / f"app_{datetime.now().strftime('%Y%m%d')}.log"


def setup_logging(level=logging.DEBUG):
    """
    Configure logging to write to both console and file.

    Args:
        level: Logging level (default DEBUG for troubleshooting)
    """
    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler
    file_handler = logging.FileHandler(LOG_FILE, mode="a")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    root_logger.handlers = []

    # Add handlers
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Log startup
    logging.info(f"Logging initialized. Log file: {LOG_FILE}")

    return LOG_FILE


def get_log_file_path():
    """Get the current log file path."""
    return LOG_FILE
