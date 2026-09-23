# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Request and response models."""

from pydantic import BaseModel, Field


class InvocationRequest(BaseModel):
    """Agent invocation request."""

    session_id: str = Field(..., description="Session identifier")
    user_message: str = Field(..., description="User message to the agent")
