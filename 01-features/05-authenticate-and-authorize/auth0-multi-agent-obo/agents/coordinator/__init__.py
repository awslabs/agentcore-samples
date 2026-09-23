# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Coordinator Agent for AgentCore Identity Sample.

This is the supervisor agent that orchestrates requests across multiple
action agents (profile, accounts, transactions, cards) in a financial
services multi-agent system.
"""

from .agent import CoordinatorAgent, create_agent
from .auth_context import (
    AuthContextManager,
    extract_user_context,
    validate_user_authorization,
)
from .subagent_router import SubAgentRouter

__all__ = [
    "CoordinatorAgent",
    "create_agent",
    "AuthContextManager",
    "extract_user_context",
    "validate_user_authorization",
    "SubAgentRouter",
]
