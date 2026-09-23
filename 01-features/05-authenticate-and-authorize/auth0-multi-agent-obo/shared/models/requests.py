# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Request and response models for agent communication."""

from dataclasses import dataclass, field
from typing import Any, Optional
from .user_context import UserContext
from .profile import CustomerProfile


@dataclass
class AgentRequest:
    """
    Standard request format for agent interactions.

    This encapsulates a user message along with their authenticated context
    and any additional metadata needed for processing.
    """

    session_id: str
    user_message: str
    user_context: UserContext
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "session_id": self.session_id,
            "user_message": self.user_message,
            "user_context": {
                "user_id": self.user_context.user_id,
                "customer_id": self.user_context.customer_id,
                "email": self.user_context.email,
                "name": self.user_context.name,
                "account_types": self.user_context.account_types,
                "scopes": self.user_context.scopes,
                "kyc_status": self.user_context.kyc_status,
                "token_expiry": self.user_context.token_expiry.isoformat(),
                "raw_claims": self.user_context.raw_claims,
            },
            "metadata": self.metadata,
        }


@dataclass
class AgentResponse:
    """
    Standard response format for agent interactions.

    Contains the agent's response text, any tool execution results,
    and metadata about the processing.
    """

    response_text: str
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "response_text": self.response_text,
            "tool_results": self.tool_results,
            "metadata": self.metadata,
        }


@dataclass
class ProfileGetRequest:
    """Request to retrieve a customer profile."""

    customer_id: str
    user_context: UserContext
    fields: Optional[list[str]] = None  # Specific fields to retrieve, None = all

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "customer_id": self.customer_id,
            "user_context": self.user_context.raw_claims,
            "fields": self.fields,
        }


@dataclass
class ProfileUpdateRequest:
    """Request to update a customer profile."""

    customer_id: str
    user_context: UserContext
    updates: dict[str, Any]  # Field name -> new value
    partial: bool = True  # If True, only update specified fields

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "customer_id": self.customer_id,
            "user_context": self.user_context.raw_claims,
            "updates": self.updates,
            "partial": self.partial,
        }


@dataclass
class ProfileResponse:
    """Response containing profile data."""

    success: bool
    profile: Optional[CustomerProfile] = None
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "profile": self.profile.to_dict() if self.profile else None,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }


@dataclass
class AccountListRequest:
    """Request to list accounts for a customer."""

    customer_id: str
    user_context: UserContext
    account_types: Optional[list[str]] = None  # Filter by account types
    include_balances: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "customer_id": self.customer_id,
            "user_context": self.user_context.raw_claims,
            "account_types": self.account_types,
            "include_balances": self.include_balances,
        }


@dataclass
class TransactionListRequest:
    """Request to list transactions for an account."""

    account_id: str
    customer_id: str
    user_context: UserContext
    start_date: Optional[str] = None  # ISO format
    end_date: Optional[str] = None  # ISO format
    limit: int = 50
    offset: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "account_id": self.account_id,
            "customer_id": self.customer_id,
            "user_context": self.user_context.raw_claims,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "limit": self.limit,
            "offset": self.offset,
        }


@dataclass
class CardListRequest:
    """Request to list cards for a customer."""

    customer_id: str
    user_context: UserContext
    include_inactive: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "customer_id": self.customer_id,
            "user_context": self.user_context.raw_claims,
            "include_inactive": self.include_inactive,
        }


@dataclass
class GenericDataResponse:
    """Generic response for data retrieval operations."""

    success: bool
    data: Any = None
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "data": self.data,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }
