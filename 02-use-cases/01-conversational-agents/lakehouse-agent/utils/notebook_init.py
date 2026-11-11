#!/usr/bin/env python3
"""
Notebook initialization utility for AWS session setup.

This module provides a simple init_aws() function for Jupyter notebooks
that loads credentials from .env and creates a validated AWS session.

It also provides run_coro(), which runs an async coroutine to completion from a
notebook cell (Jupyter's kernel already runs an event loop of its own).

Usage in notebooks:
    from utils.notebook_init import init_aws, run_coro

    session, region, account_id = init_aws()
    result = run_coro(some_async_function())
"""

import asyncio
import threading

import boto3

from .aws_session_utils import get_aws_session, load_env_credentials


def init_aws(
    env_path: str = ".env",
    profile_name: str | None = None,
    region_name: str | None = None,
    verbose: bool = True,
) -> tuple[boto3.Session, str, str]:
    """
    Initialize AWS session for notebook use with automatic SSO fallback.

    This function:
    1. Loads credentials from .env file (if it exists)
    2. Creates and validates AWS session
    3. Automatically falls back to AWS SSO if .env credentials are invalid/expired
    4. Returns session, region, and account_id

    Credential priority order:
    - Container IAM role (if running in Lambda/ECS/EKS)
    - Environment variables from .env file
      * If invalid/expired, automatically clears them and falls back to SSO
    - AWS SSO profile (from AWS_PROFILE or AWS_DEFAULT_PROFILE)
    - Default AWS credentials

    Args:
        env_path: Path to .env file. Default is '.env' in current directory.
        profile_name: Optional AWS profile name to use.
        region_name: Optional AWS region to use.
        verbose: If True, print status messages. Default True.

    Returns:
        Tuple of (boto3.Session, region_name: str, account_id: str)

    Example:
        >>> from utils.notebook_init import init_aws
        >>> session, region, account_id = init_aws()
        >>> s3_client = session.client('s3', region_name=region)
    """
    # Try to load credentials from .env file
    load_env_credentials(env_path=env_path, verbose=verbose)

    # Create and validate AWS session
    session, region, account_id = get_aws_session(profile_name=profile_name, region_name=region_name, verbose=verbose)

    return session, region, account_id


def run_coro(coro):
    """Run an async coroutine to completion from a notebook cell and return its result.

    A Jupyter cell cannot call asyncio.run() directly, because the kernel already runs
    an event loop. run_coro runs the coroutine with asyncio.run() in a short-lived worker
    thread that has its own fresh event loop, waits for it, and returns the result. It
    does not patch asyncio, so the kernel's own loop and message handling are untouched.

    Any exception the coroutine raises (including an ExceptionGroup from an MCP client)
    is re-raised unchanged in the calling thread.

    Example:
        >>> tools = run_coro(list_tools(token))
    """
    outcome = {}

    def _worker():
        try:
            outcome["value"] = asyncio.run(coro)
        except BaseException as e:
            outcome["error"] = e

    worker = threading.Thread(target=_worker, name="run_coro", daemon=True)
    worker.start()
    # Short joins keep the cell interruptible (a single blocking join is not).
    while worker.is_alive():
        worker.join(timeout=0.2)
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")
