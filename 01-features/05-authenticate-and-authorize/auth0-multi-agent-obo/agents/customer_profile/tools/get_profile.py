# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Tool to retrieve customer profile information.
"""

import logging
from typing import Dict, Any
from strands import tool
from auth_validator import (
    validate_forwarded_claims,
    authorize_profile_access,
    ClaimsValidationError,
    AuthorizationError,
)
from profile_service import profile_service

logger = logging.getLogger(__name__)


@tool
def get_profile_tool(customer_id: str, claims: Dict[str, Any]) -> Dict[str, Any]:
    """
    Retrieve customer profile information.

    This tool validates the JWT claims (from the exchanged token with
    attenuated profile:* scopes) and ensures the requesting user is
    authorized to view the profile data.

    Args:
        customer_id: The customer ID to retrieve profile for
        claims: JWT claims from the exchanged token (or original JWT)

    Returns:
        Dict[str, Any]: Customer profile data including personal details,
                        contact information, and preferences

    Raises:
        ClaimsValidationError: If JWT claims are invalid
        AuthorizationError: If user is not authorized to access this profile
        ValueError: If profile not found
    """
    try:
        # Validate forwarded claims
        validated_claims = validate_forwarded_claims(claims)

        # Check authorization
        authorize_profile_access(validated_claims, customer_id)

        # Retrieve profile
        profile = profile_service.get_profile(customer_id)

        if not profile:
            logger.error(f"Profile not found for customer_id={customer_id}")
            return {
                "success": False,
                "error": f"Profile not found for customer {customer_id}",
            }

        logger.info(f"Profile retrieved successfully for customer_id={customer_id}")

        return {"success": True, "profile": profile}

    except ClaimsValidationError as e:
        logger.error(f"Claims validation failed: {e}")
        return {"success": False, "error": f"Authentication error: {str(e)}"}

    except AuthorizationError as e:
        logger.warning(f"Authorization failed: {e}")
        return {"success": False, "error": f"Authorization error: {str(e)}"}

    except Exception as e:
        logger.error(f"Unexpected error retrieving profile: {e}", exc_info=True)
        return {"success": False, "error": f"Failed to retrieve profile: {str(e)}"}
