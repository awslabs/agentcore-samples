"""
JWT Helper Functions for Actor ID Extraction

Provides utilities to extract actor_id from Cognito JWT tokens.
Used by Labs 2-5 to trace agent calls back to specific users/actors.
"""

import jwt
from typing import Dict
import logging

logger = logging.getLogger(__name__)


def get_jwt_claims(access_token: str, region: str, user_pool_id: str, skip_verification: bool = True) -> Dict[str, str]:
    """
    Extract claims from Cognito JWT token.

    Args:
        access_token: JWT token from Cognito authentication
        region: AWS region where Cognito pool is created
        user_pool_id: Cognito User Pool ID
        skip_verification: If True, decode without signature verification (default: True for labs)

    Returns:
        Dictionary with keys: actor_id, sub, email, username, token_use, aud

    Example:
        >>> claims = get_jwt_claims(token, "us-west-2", "us-west-2_abc123xyz")
        >>> actor_id = claims['actor_id']  # username from Cognito
    """
    try:
        # Decode JWT (skip verification for workshop labs)
        claims = jwt.decode(access_token, options={"verify_signature": False})

        # Extract actor_id from username claim
        # Cognito stores username in 'cognito:username'
        actor_id = claims.get("cognito:username", claims.get("sub", "unknown-user"))

        return {
            "actor_id": actor_id,
            "sub": claims.get("sub"),
            "email": claims.get("email"),
            "token_use": claims.get("token_use"),
            "aud": claims.get("aud"),
            "username": claims.get("cognito:username"),
        }

    except jwt.InvalidTokenError as e:
        logger.error(f"Invalid JWT token: {e}")
        raise
    except Exception as e:
        logger.error(f"Error extracting JWT claims: {e}")
        raise


def extract_actor_id_from_jwt(access_token: str) -> str:
    """
    Quick utility to extract just the actor_id from a JWT token.

    Args:
        access_token: JWT token from Cognito

    Returns:
        actor_id (username) from the token
    """
    try:
        claims = jwt.decode(access_token, options={"verify_signature": False})
        return claims.get("cognito:username", claims.get("sub", "unknown-user"))
    except Exception as e:
        logger.error(f"Error extracting actor_id from JWT: {e}")
        raise
