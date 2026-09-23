# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Main entry point for the Coordinator Agent using bedrock_agentcore SDK.

This module implements the AgentCore Runtime entry point using the @app.entrypoint
decorator pattern with the correct bedrock_agentcore SDK.
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agent import create_agent
from auth_context import validate_user_authorization
from shared.auth.token_exchange import TokenExchangeService
from subagent_router import SubAgentRouter


# Configure structured JSON logging
class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record):
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


# Setup logging
log_level = os.getenv("LOG_LEVEL", "INFO")
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(JSONFormatter())

logging.basicConfig(
    level=getattr(logging, log_level.upper(), logging.INFO),
    handlers=[handler],
)
logger = logging.getLogger(__name__)

# Log startup
logger.info(
    json.dumps(
        {
            "event": "agent_module_loading",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "log_level": log_level,
            "python_version": sys.version,
        }
    )
)

# Initialize the BedrockAgentCoreApp
app = BedrockAgentCoreApp()

# Initialize subagent router
router = SubAgentRouter()

# Initialize RFC 8693 token exchange service for scope attenuation
token_exchange_service = TokenExchangeService()

logger.info(
    json.dumps(
        {
            "event": "router_initialized",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "token_exchange": "enabled",
        }
    )
)


@app.entrypoint
async def invoke(payload, context=None):
    """
    Main entry point for coordinator agent invocations.

    Args:
        payload: Dictionary containing the request payload with 'prompt' key
        context: Optional context object with session_id and other metadata

    Returns:
        Dictionary with the agent's response
    """
    import time

    start_time = time.time()

    # Generate trace ID
    trace_id = str(uuid.uuid4())
    session_id = getattr(context, "session_id", None) or str(uuid.uuid4())

    logger.info(
        json.dumps(
            {
                "event": "handler_invoked",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trace_id": trace_id,
                "session_id": session_id,
                "has_payload": payload is not None,
            }
        )
    )

    try:
        # Get user input from payload
        user_input = ""
        if payload:
            user_input = payload.get("prompt", "") or payload.get("input_text", "") or ""

        # Extract user context from payload (JWT claims may be in payload or context)
        user_context = extract_user_context_from_payload(payload, context)

        # Safety net: ensure access_token is set from payload if extract_user_context_from_payload missed it
        # Note: extract_user_context_from_payload now prioritizes payload.access_token over internal tokens
        if not user_context.get("access_token") and payload:
            payload_token = payload.get("access_token", "")
            if payload_token and isinstance(payload_token, str) and payload_token.strip():
                user_context["access_token"] = payload_token
                logger.info("access_token set from payload (safety net)")

        logger.info(
            json.dumps(
                {
                    "event": "user_context_extracted",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "user_id": user_context.get("user_id", "unknown"),
                    "customer_id": user_context.get("customer_id", "unknown"),
                    "input_preview": user_input[:100] if user_input else "",
                }
            )
        )

        # Validate user authorization
        if not validate_user_authorization(user_context):
            logger.warning(
                json.dumps(
                    {
                        "event": "authorization_failed",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "trace_id": trace_id,
                        "session_id": session_id,
                    }
                )
            )
            return {
                "status": "error",
                "error": "AUTHORIZATION_FAILED",
                "message": "You don't have the required permissions to access this service.",
                "trace_id": trace_id,
            }

        # Create the coordinator agent
        agent = create_agent(
            session_id=session_id,
            user_context=user_context,
            router=router,
            token_exchange_service=token_exchange_service,
        )

        # Process the request through the agent
        logger.info(
            json.dumps(
                {
                    "event": "agent_processing_started",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trace_id": trace_id,
                    "session_id": session_id,
                }
            )
        )

        response = await agent.process(user_input, user_context)

        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000

        logger.info(
            json.dumps(
                {
                    "event": "request_completed",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "duration_ms": round(duration_ms, 2),
                }
            )
        )

        return {
            "status": "success",
            "response": response.get("output", "Unable to process your request."),
            "session_id": session_id,
            "trace_id": trace_id,
            "duration_ms": round(duration_ms, 2),
        }

    except ValueError as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.error(
            json.dumps(
                {
                    "event": "validation_error",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "error": str(e),
                }
            )
        )

        return {
            "status": "error",
            "error": "VALIDATION_ERROR",
            "message": str(e),
            "trace_id": trace_id,
        }

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.exception(
            json.dumps(
                {
                    "event": "unexpected_error",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "error_type": type(e).__name__,
                    "error": str(e),
                }
            )
        )

        return {
            "status": "error",
            "error": "INTERNAL_ERROR",
            "message": "An unexpected error occurred. Please try again later.",
            "trace_id": trace_id,
        }


def decode_jwt_claims(token: str) -> Dict[str, Any]:
    """Decode JWT payload without verification (already validated by AgentCore)."""
    import base64

    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        # Add padding if needed
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        logger.warning(f"Failed to decode JWT: {e}")
        return {}


def extract_user_context_from_payload(payload: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Extract user context from JWT token.

    With --request-header-allowlist "Authorization" configured on deployment,
    the Authorization header is now accessible via context.request_headers.
    AgentCore Runtime's customJWTAuthorizer validates the JWT, so we just decode claims.

    Token Priority:
    1. Authorization header (primary - now available with allowlist)
    2. payload.access_token (fallback for backward compatibility)
    """
    CLAIMS_NAMESPACE = "https://agentcore.example.com/"

    user_context = {
        "user_id": "unknown",
        "email": "unknown",
        "email_verified": False,
        "customer_id": "unknown",
        "permissions": [],
        "access_token": "",
    }

    token = ""
    token_source = "none"

    # PRIORITY 1: Get JWT from Authorization header (now available with allowlist)
    if context and hasattr(context, "request_headers"):
        headers = context.request_headers or {}
        auth_header = headers.get("Authorization") or headers.get("authorization") or ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            token_source = "Authorization header"

    # PRIORITY 2: Fallback to payload (backward compatibility)
    if not token and payload:
        payload_token = payload.get("access_token", "")
        if payload_token and isinstance(payload_token, str) and payload_token.strip():
            token = payload_token
            token_source = "payload.access_token"

    logger.info(
        json.dumps(
            {
                "event": "token_extraction",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "token_source": token_source,
                "has_token": bool(token),
            }
        )
    )

    if token:
        user_context["access_token"] = token
        claims = decode_jwt_claims(token)

        if claims:
            logger.info(
                json.dumps(
                    {
                        "event": "jwt_claims_decoded",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "claims_keys": list(claims.keys()),
                        "sub": claims.get("sub", "N/A")[:20] if claims.get("sub") else "N/A",
                    }
                )
            )

            # Extract standard claims
            user_context["user_id"] = claims.get("sub", user_context["user_id"])
            user_context["email"] = claims.get("email", user_context["email"])
            user_context["email_verified"] = claims.get("email_verified", False)

            # Extract RBAC permissions (role-based, per-user) as primary source
            permissions = claims.get("permissions", [])
            if permissions:
                user_context["permissions"] = permissions if isinstance(permissions, list) else [permissions]
            else:
                # Fallback to scope claim for non-Auth0 IdPs that don't have RBAC
                scope = claims.get("scope", "")
                if scope:
                    user_context["permissions"] = scope.split() if isinstance(scope, str) else scope

            logger.info(
                f"Authorization source: {'permissions claim (RBAC)' if permissions else 'scope claim (fallback)'}, values={user_context['permissions']}"
            )

            # Extract custom namespace claims from Auth0
            for key, value in claims.items():
                if key.startswith(CLAIMS_NAMESPACE):
                    claim_name = key.replace(CLAIMS_NAMESPACE, "")
                    if claim_name == "customer_id":
                        user_context["customer_id"] = value
                    elif claim_name == "roles" and isinstance(value, list):
                        user_context["permissions"].extend(value)

            # Fallback: use user_id as customer_id if not in custom claims
            if user_context["customer_id"] == "unknown" and user_context["user_id"] != "unknown":
                user_context["customer_id"] = user_context["user_id"]

    logger.info(
        json.dumps(
            {
                "event": "extract_context_result",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user_id": user_context["user_id"],
                "customer_id": user_context["customer_id"],
                "permissions_count": len(user_context["permissions"]),
            }
        )
    )

    return user_context


# Log module load complete
logger.info(
    json.dumps(
        {
            "event": "agent_module_loaded",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "3.0.0",
            "sdk": "bedrock_agentcore",
        }
    )
)


if __name__ == "__main__":
    app.run()
