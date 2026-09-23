# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Tools for the Customer Profile Agent.
"""

from .get_profile import get_profile_tool
from .update_phone import update_phone_tool

__all__ = [
    "get_profile_tool",
    "update_phone_tool",
]
