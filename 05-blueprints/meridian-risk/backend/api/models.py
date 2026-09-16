# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Request models shared by the console API routers."""

from pydantic import BaseModel, Field


class AssessRequest(BaseModel):
    customer_id: str = Field(..., description="Corporate customer ID, e.g. CUST001")
    assessment_type: str = Field(
        default="full", pattern="^(full|credit_only|compliance_only)$"
    )
    context: str | None = Field(default=None, description="Optional analyst notes")


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    max_results: int = Field(default=10, ge=1, le=50)


class ToolInvokeRequest(BaseModel):
    tool_name: str
    customer_id: str


class StatusRequest(BaseModel):
    status: str = Field(..., pattern="^(PENDING_APPROVAL|APPROVED|REJECTED|DEPRECATED)$")
    reason: str = Field(default="Updated from the demo console")
