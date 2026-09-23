#!/usr/bin/env python3
"""
Notebook initialization utility for AWS session setup.

This module provides a simple init_aws() function for Jupyter notebooks
that loads credentials from .env and creates a validated AWS session.

Usage in notebooks:
    from utils.notebook_init import init_aws

    session, region, account_id = init_aws()
"""

from .aws_session_utils import load_env_credentials, get_aws_session
from typing import Tuple
import boto3


def init_aws(
    env_path: str = ".env",
    profile_name: str = None,
    region_name: str = None,
    verbose: bool = True,
) -> Tuple[boto3.Session, str, str]:
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
