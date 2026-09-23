# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""AgentCore Identity integration for workload access tokens."""

import logging

from bedrock_agentcore.services.identity import IdentityClient

logger = logging.getLogger(__name__)


def get_workload_access_token(user_id: str, workload_identity_name: str | None, aws_region: str | None) -> str | None:
    """Get workload access token from AgentCore Identity for the current invocation.

    Args:
        user_id: User identifier for the token request
        workload_identity_name: AgentCore workload identity name (None if not configured)
        aws_region: AWS region for the IdentityClient

    Returns:
        Workload access token string, or None if identity is not configured

    """
    if not workload_identity_name or not aws_region:
        logger.info("Identity not configured, skipping token acquisition")
        return None

    client = IdentityClient(aws_region)
    response = client.get_workload_access_token(workload_identity_name, user_id=user_id)
    return str(response["workloadAccessToken"])
