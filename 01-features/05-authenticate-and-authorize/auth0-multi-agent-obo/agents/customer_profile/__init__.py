# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Customer Profile Agent for AgentCore Identity Sample.

This agent manages customer personal details and profile information
for a financial services company. It validates JWT claims from exchanged
tokens (RFC 8693 with attenuated profile:* scopes) before performing
any operations.
"""

from .agent import create_agent
from .profile_service import ProfileService

__all__ = ["create_agent", "ProfileService"]
