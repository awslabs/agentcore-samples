# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Token forwarding utilities for agent-to-agent communication."""

import json
import base64
from typing import Optional, Any
from datetime import datetime

from ..models.user_context import UserContext


class TokenForwarder:
    """
    Manages user context forwarding between agents.

    This class handles serialization and validation of user context
    when one agent needs to call another agent on behalf of a user.
    It ensures that user identity and permissions are properly maintained
    throughout the agent chain.
    """

    def __init__(self, require_valid_tokens: bool = True):
        """
        Initialize the token forwarder.

        Args:
            require_valid_tokens: Whether to enforce token expiry validation
        """
        self.require_valid_tokens = require_valid_tokens

    def serialize_user_context(self, user_context: UserContext) -> str:
        """
        Serialize a UserContext for forwarding to another agent.

        Args:
            user_context: The UserContext to serialize

        Returns:
            Base64-encoded JSON string containing the context
        """
        context_dict = {
            "user_id": user_context.user_id,
            "customer_id": user_context.customer_id,
            "email": user_context.email,
            "name": user_context.name,
            "account_types": user_context.account_types,
            "scopes": user_context.scopes,
            "kyc_status": user_context.kyc_status,
            "token_expiry": user_context.token_expiry.isoformat(),
            "raw_claims": user_context.raw_claims,
        }

        # Serialize to JSON and encode
        json_str = json.dumps(context_dict)
        encoded = base64.b64encode(json_str.encode("utf-8")).decode("utf-8")

        return encoded

    def deserialize_user_context(self, serialized_context: str) -> UserContext:
        """
        Deserialize a UserContext from a forwarded string.

        Args:
            serialized_context: Base64-encoded JSON string

        Returns:
            UserContext instance

        Raises:
            ValueError: If deserialization fails or validation fails
        """
        try:
            # Decode from base64
            json_str = base64.b64decode(serialized_context.encode("utf-8")).decode("utf-8")
            context_dict = json.loads(json_str)

            # Parse token expiry
            token_expiry = datetime.fromisoformat(context_dict["token_expiry"])

            # Create UserContext
            user_context = UserContext(
                user_id=context_dict["user_id"],
                customer_id=context_dict["customer_id"],
                email=context_dict["email"],
                name=context_dict["name"],
                account_types=context_dict["account_types"],
                scopes=context_dict["scopes"],
                kyc_status=context_dict["kyc_status"],
                token_expiry=token_expiry,
                raw_claims=context_dict["raw_claims"],
            )

            # Validate if required
            if self.require_valid_tokens and user_context.is_token_expired():
                raise ValueError("Forwarded token has expired")

            return user_context

        except (KeyError, json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Failed to deserialize user context: {e}")

    def create_forwarding_headers(self, user_context: UserContext) -> dict[str, str]:
        """
        Create HTTP headers for forwarding user context.

        Args:
            user_context: The UserContext to forward

        Returns:
            Dictionary of headers to include in agent-to-agent requests
        """
        serialized = self.serialize_user_context(user_context)

        return {
            "X-User-Context": serialized,
            "X-User-Id": user_context.user_id,
            "X-Customer-Id": user_context.customer_id,
            "X-User-Scopes": " ".join(user_context.scopes),
        }

    def extract_from_headers(self, headers: dict[str, str]) -> Optional[UserContext]:
        """
        Extract UserContext from HTTP headers.

        Args:
            headers: HTTP headers dictionary

        Returns:
            UserContext if found and valid, None otherwise
        """
        serialized = headers.get("X-User-Context")
        if not serialized:
            return None

        try:
            return self.deserialize_user_context(serialized)
        except ValueError:
            return None

    def validate_forwarded_context(
        self,
        user_context: UserContext,
        required_scopes: Optional[list[str]] = None,
        required_account_types: Optional[list[str]] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Validate a forwarded user context meets requirements.

        Args:
            user_context: The UserContext to validate
            required_scopes: List of scopes that must be present
            required_account_types: List of account types that must be present

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check token expiry
        if self.require_valid_tokens and user_context.is_token_expired():
            return False, "Token has expired"

        # Check required scopes
        if required_scopes:
            missing_scopes = [s for s in required_scopes if not user_context.has_scope(s)]
            if missing_scopes:
                return False, f"Missing required scopes: {', '.join(missing_scopes)}"

        # Check required account types
        if required_account_types:
            missing_types = [t for t in required_account_types if not user_context.has_account_type(t)]
            if missing_types:
                return (
                    False,
                    f"Missing required account types: {', '.join(missing_types)}",
                )

        return True, None

    def create_agent_request_context(
        self,
        user_context: UserContext,
        source_agent: str,
        target_agent: str,
        operation: str,
    ) -> dict[str, Any]:
        """
        Create a complete context for agent-to-agent requests.

        This includes user context, routing information, and audit metadata.

        Args:
            user_context: The UserContext to forward
            source_agent: Name of the calling agent
            target_agent: Name of the target agent
            operation: Description of the operation being performed

        Returns:
            Dictionary containing complete request context
        """
        return {
            "user_context": {
                "user_id": user_context.user_id,
                "customer_id": user_context.customer_id,
                "email": user_context.email,
                "name": user_context.name,
                "account_types": user_context.account_types,
                "scopes": user_context.scopes,
                "kyc_status": user_context.kyc_status,
                "token_expiry": user_context.token_expiry.isoformat(),
            },
            "routing": {
                "source_agent": source_agent,
                "target_agent": target_agent,
                "operation": operation,
                "timestamp": datetime.utcnow().isoformat(),
            },
            "audit": {
                "user_id": user_context.user_id,
                "customer_id": user_context.customer_id,
                "source_agent": source_agent,
                "target_agent": target_agent,
                "operation": operation,
                "timestamp": datetime.utcnow().isoformat(),
            },
        }

    def verify_agent_chain_integrity(
        self,
        user_context: UserContext,
        expected_user_id: str,
        expected_customer_id: str,
    ) -> bool:
        """
        Verify that the forwarded context matches expected identity.

        This helps prevent context confusion or injection attacks where
        one user's context might be forwarded inappropriately.

        Args:
            user_context: The UserContext to verify
            expected_user_id: Expected user_id
            expected_customer_id: Expected customer_id

        Returns:
            True if context matches expectations
        """
        return user_context.user_id == expected_user_id and user_context.customer_id == expected_customer_id
