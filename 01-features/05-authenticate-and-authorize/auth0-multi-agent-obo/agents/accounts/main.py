# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Main entry point for the Accounts Agent using bedrock_agentcore SDK.

This module implements the AgentCore Runtime entry point using the @app.entrypoint
decorator pattern with the correct bedrock_agentcore SDK.
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agent import AccountsAgent
from auth_validator import validate_forwarded_claims


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

logger.info(
    json.dumps(
        {
            "event": "app_initialized",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
)


def _decode_jwt_claims(token: str) -> dict:
    """Decode JWT payload without verification (already validated by AgentCore)."""
    import base64

    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        decoded = base64.urlsafe_b64decode(payload_b64)
        return json.loads(decoded)
    except Exception as e:
        logger.warning(f"Failed to decode JWT: {e}")
        return {}


def _extract_claims(payload, context) -> dict:
    """Extract JWT claims from exchanged token, Authorization header, or payload.

    Priority order:
    1. RFC 8693 exchanged token in payload context (attenuated scopes from coordinator)
    2. Authorization header (original Auth0 JWT forwarded by AgentCore Runtime)
    3. Claims in payload (backward compatibility)
    4. access_token in payload
    """
    # Priority 1: RFC 8693 exchanged token from coordinator (scope-attenuated)
    if payload and isinstance(payload.get("context"), dict):
        exchanged_token = payload["context"].get("exchanged_token")
        if exchanged_token:
            claims = _decode_jwt_claims(exchanged_token)
            if claims:
                logger.info(
                    json.dumps(
                        {
                            "event": "claims_from_exchanged_token",
                            "sub": claims.get("sub", "N/A")[:20],
                            "exchange_id": claims.get("exchange_id", "N/A"),
                            "scope": claims.get("scope", ""),
                            "act": claims.get("act"),
                        }
                    )
                )
                return claims

    # Priority 2: Authorization header (forwarded by coordinator via AgentCore)
    if context and hasattr(context, "request_headers"):
        headers = context.request_headers or {}
        auth_header = headers.get("Authorization") or headers.get("authorization") or ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            claims = _decode_jwt_claims(token)
            if claims:
                logger.info(f"Claims extracted from Authorization header (sub={claims.get('sub', 'N/A')[:20]})")
                return claims

    # Priority 3: Claims in payload (backward compatibility)
    if payload and payload.get("claims"):
        logger.info("Claims extracted from payload")
        return payload.get("claims", {})

    # Priority 4: access_token in payload
    if payload and payload.get("access_token"):
        token = payload["access_token"]
        claims = _decode_jwt_claims(token)
        if claims:
            logger.info(f"Claims extracted from payload.access_token (sub={claims.get('sub', 'N/A')[:20]})")
            return claims

    logger.warning("No JWT claims found in request")
    return {}


@app.entrypoint
async def invoke(payload, context=None):
    """
    Main entry point for accounts agent invocations.

    Args:
        payload: Dictionary containing the request payload with 'query' and 'claims' keys
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
        # Extract query from payload
        query = payload.get("prompt", "") or payload.get("query", "") if payload else ""

        # Extract JWT claims - try Authorization header first, then payload
        claims = _extract_claims(payload, context)

        logger.info(
            json.dumps(
                {
                    "event": "processing_request",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "user_id": claims.get("sub", "unknown"),
                    "query_preview": query[:100] if query else "",
                }
            )
        )

        # Validate forwarded claims
        validation_result = validate_forwarded_claims(claims)
        if not validation_result["valid"]:
            logger.warning(
                json.dumps(
                    {
                        "event": "authorization_failed",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "trace_id": trace_id,
                        "session_id": session_id,
                        "error": validation_result["error"],
                    }
                )
            )
            return {
                "status": "error",
                "error": "AUTHORIZATION_FAILED",
                "message": validation_result["error"],
                "trace_id": trace_id,
            }

        # Extract user context
        user_id = claims.get("sub")
        customer_id = claims.get("https://agentcore.example.com/customer_id") or claims.get("customer_id")

        logger.info(
            json.dumps(
                {
                    "event": "user_authorized",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "user_id": user_id,
                    "customer_id": customer_id,
                }
            )
        )

        # Create and run the accounts agent
        accounts_agent = AccountsAgent(user_id=user_id, customer_id=customer_id)
        response = accounts_agent.process_query(query)

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
            "response": response,
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


# Log module load complete
logger.info(
    json.dumps(
        {
            "event": "agent_module_loaded",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "2.0.0",
            "sdk": "bedrock_agentcore",
        }
    )
)


if __name__ == "__main__":
    app.run()
