# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Authentication utilities for AgentCore Identity."""

from .claims_extractor import (
    extract_standard_claims,
    extract_custom_claims,
    get_claim_value,
    extract_scopes,
    extract_permissions,
    build_user_context,
    validate_required_claims,
    extract_user_metadata,
    format_claim_for_logging,
    DEFAULT_CLAIMS_NAMESPACE,
)
from .token_forwarder import TokenForwarder
from .token_exchange import (
    TokenExchangeService,
    TokenExchangeRequest,
    TokenExchangeResponse,
    ScopePolicy,
    TokenExchangeError,
    InvalidRequestError,
    InvalidTokenError,
    InsufficientScopeError,
    GRANT_TYPE_TOKEN_EXCHANGE,
    TOKEN_TYPE_JWT,
    TOKEN_TYPE_ACCESS_TOKEN,
    DEFAULT_EXCHANGE_ISSUER,
)

__all__ = [
    # Claims extraction
    "extract_standard_claims",
    "extract_custom_claims",
    "get_claim_value",
    "extract_scopes",
    "extract_permissions",
    "build_user_context",
    "validate_required_claims",
    "extract_user_metadata",
    "format_claim_for_logging",
    "DEFAULT_CLAIMS_NAMESPACE",
    # Token forwarding
    "TokenForwarder",
    # RFC 8693 Token Exchange
    "TokenExchangeService",
    "TokenExchangeRequest",
    "TokenExchangeResponse",
    "ScopePolicy",
    "TokenExchangeError",
    "InvalidRequestError",
    "InvalidTokenError",
    "InsufficientScopeError",
    "GRANT_TYPE_TOKEN_EXCHANGE",
    "TOKEN_TYPE_JWT",
    "TOKEN_TYPE_ACCESS_TOKEN",
    "DEFAULT_EXCHANGE_ISSUER",
]
