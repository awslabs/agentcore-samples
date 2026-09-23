# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Tool to update customer phone numbers.
"""

import logging
from typing import Dict, Any, Optional
from strands import tool
from auth_validator import (
    validate_forwarded_claims,
    authorize_profile_update,
    get_audit_context,
    ClaimsValidationError,
    AuthorizationError,
)
from profile_service import profile_service

logger = logging.getLogger(__name__)


@tool
def update_phone_tool(
    customer_id: str,
    phone_type: str,
    claims: Dict[str, Any],
    phone_number: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update customer phone number.

    This tool validates the JWT claims (from the exchanged token with
    attenuated profile:* scopes) and ensures the requesting user is
    authorized to update phone numbers. Supports both primary and
    secondary phone numbers. Pass None to remove a phone number.

    Args:
        customer_id: The customer ID to update
        phone_type: Type of phone number - must be "primary" or "secondary"
        claims: JWT claims from the exchanged token (or original JWT)
        phone_number: New phone number in international format (e.g., +61412345678)
                     or None to remove the phone number

    Returns:
        Dict[str, Any]: Result with success status and updated profile data

    Raises:
        ClaimsValidationError: If JWT claims are invalid
        AuthorizationError: If user is not authorized to update this profile
        ValueError: If phone type or number format is invalid
    """
    try:
        # Validate forwarded claims
        validated_claims = validate_forwarded_claims(claims)

        # Check authorization
        authorize_profile_update(validated_claims, customer_id)

        # Get audit context
        audit_ctx = get_audit_context(validated_claims)

        # Update phone number
        updated_profile = profile_service.update_phone(
            customer_id=customer_id,
            phone_type=phone_type,
            phone_number=phone_number,
            updated_by=audit_ctx["customer_id"],
        )

        action = "removed" if phone_number is None else "updated"
        logger.info(
            f"{phone_type.capitalize()} phone {action} for customer_id={customer_id} by {audit_ctx['customer_id']}"
        )

        return {
            "success": True,
            "message": f"{phone_type.capitalize()} phone number {action} successfully",
            "profile": updated_profile,
        }

    except ClaimsValidationError as e:
        logger.error(f"Claims validation failed: {e}")
        return {"success": False, "error": f"Authentication error: {str(e)}"}

    except AuthorizationError as e:
        logger.warning(f"Authorization failed: {e}")
        return {"success": False, "error": f"Authorization error: {str(e)}"}

    except ValueError as e:
        logger.error(f"Invalid phone update: {e}")
        return {"success": False, "error": f"Invalid phone number: {str(e)}"}

    except Exception as e:
        logger.error(f"Unexpected error updating phone: {e}", exc_info=True)
        return {"success": False, "error": f"Failed to update phone number: {str(e)}"}
