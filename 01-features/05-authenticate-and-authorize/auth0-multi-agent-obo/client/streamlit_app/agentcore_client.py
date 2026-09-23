# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
AgentCore Runtime client for invoking agents with JWT authentication.

This client uses direct HTTP requests to invoke AgentCore agents with OAuth/JWT authorization.
The customJWTAuthorizer configured on the agent validates tokens against Auth0.

Authentication Flow:
1. User authenticates with Auth0 (3-legged OAuth)
2. Streamlit receives JWT (access_token)
3. Client invokes agent via HTTP with JWT in Authorization header
4. AC Runtime validates JWT against Auth0 OIDC discovery endpoint
5. Validated claims injected into agent context
6. Agent processes request with verified user identity

NOTE: We use requests library instead of boto3 because the agent is configured
with customJWTAuthorizer (OAuth), and boto3 automatically signs requests with SigV4.
"""

import os
import json
import logging
import base64
from typing import Dict, Any, Iterator, Generator
from urllib.parse import quote

import requests


def _record_api_call(
    method: str,
    url: str,
    request_headers: dict,
    request_body: dict = None,
    response_status: int = None,
    response_headers: dict = None,
    response_body: any = None,
    duration_ms: float = None,
    error: str = None,
    call_type: str = "api",
):
    """Record an API call to the session log for educational purposes."""
    try:
        from components.api_call_log import record_api_call

        record_api_call(
            method=method,
            url=url,
            request_headers=request_headers,
            request_body=request_body,
            response_status=response_status,
            response_headers=response_headers,
            response_body=response_body,
            duration_ms=duration_ms,
            error=error,
            call_type=call_type,
        )
    except ImportError:
        pass  # API call log not available


def decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verification (for debugging)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        # Add padding if needed
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        return {"error": str(e)}


# Handle imports for both standalone and package usage
try:
    from shared.config.settings import settings
except ImportError:
    settings = None

# Configure logging
logger = logging.getLogger(__name__)


class AgentCoreClient:
    """
    Client for AgentCore Runtime with JWT authentication.

    Uses direct HTTP requests with OAuth/JWT for authentication.
    JWT is validated by AC Runtime's customJWTAuthorizer against Auth0.

    Example:
        client = AgentCoreClient()
        response = client.send_message(
            message="Show me my profile",
            session_id="session-123",
            access_token="eyJhbG..."  # Auth0 JWT
        )
    """

    def __init__(self):
        """
        Initialize AgentCore client.

        Loads configuration from settings or environment variables.
        """
        # Load configuration
        if settings:
            self.region = settings.agentcore.region
            self.coordinator_agent_id = settings.agentcore.coordinator_agent_id
        else:
            self.region = os.getenv("AWS_REGION", "us-east-1")
            self.coordinator_agent_id = os.getenv("COORDINATOR_AGENT_ID", "")

        # AWS Account ID for ARN construction
        self.account_id = os.getenv("EXPECTED_AWS_ACCOUNT", "")

        # Request timeout (seconds)
        self.timeout = int(os.getenv("AGENTCORE_TIMEOUT", "60"))

        # Build the base endpoint URL
        self.base_url = f"https://bedrock-agentcore.{self.region}.amazonaws.com"

        # Create a session for connection pooling
        self._session = requests.Session()

        logger.info(
            f"AgentCoreClient initialized - "
            f"Region: {self.region}, "
            f"Agent: {self.coordinator_agent_id or 'Not configured'}, "
            f"Endpoint: {self.base_url}"
        )

    def _build_agent_runtime_arn(self, agent_id: str) -> str:
        """
        Build the AgentCore Runtime ARN from agent ID.

        Args:
            agent_id: Agent ID (e.g., coordinator_agent-Ju2XHq2A6M)

        Returns:
            Full ARN string
        """
        # AgentCore uses "runtime" (not "agent-runtime") in the ARN
        return f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:runtime/{agent_id}"

    def send_message(
        self,
        message: str,
        session_id: str,
        access_token: str,
    ) -> Dict[str, Any]:
        """
        Send a message to the coordinator agent.

        Uses direct HTTP POST with JWT in Authorization header (OAuth).
        The JWT is validated by AC Runtime's customJWTAuthorizer against
        the configured Auth0 OIDC discovery endpoint.

        Args:
            message: User message to send to the agent
            session_id: Session ID for conversation continuity
            access_token: Auth0 JWT access token (validated by AC Runtime)

        Returns:
            Response dictionary containing:
            - response: Agent's text response
            - sessionId: Session ID for follow-up messages
            - metadata: Additional response metadata

        Raises:
            AuthenticationError: If JWT validation fails
            AuthorizationError: If user lacks required permissions
            RuntimeError: For other errors
        """
        if not self.coordinator_agent_id:
            raise ValueError("COORDINATOR_AGENT_ID not configured. Set this after deploying the coordinator agent.")

        logger.debug(f"Sending message to agent {self.coordinator_agent_id}")
        logger.debug(f"Session ID: {session_id}")

        # Debug: decode and log JWT claims (for troubleshooting)
        jwt_claims = decode_jwt_payload(access_token)
        logger.debug(f"JWT claims: {json.dumps(jwt_claims, indent=2)}")
        logger.info(f"JWT azp (client_id): {jwt_claims.get('azp', 'NOT FOUND')}")
        logger.info(f"JWT client_id claim: {jwt_claims.get('client_id', 'NOT FOUND')}")

        try:
            # Build the agent runtime ARN
            agent_arn = self._build_agent_runtime_arn(self.coordinator_agent_id)

            # Build the invocation URL
            # Format: POST /runtimes/{agentRuntimeArn}/invocations?qualifier=DEFAULT
            # The ARN must be URL-encoded since it contains special characters
            # The qualifier=DEFAULT is required for OAuth authorization
            encoded_arn = quote(agent_arn, safe="")
            url = f"{self.base_url}/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"

            # Prepare the request payload
            # With --request-header-allowlist "Authorization" configured on agents,
            # the agent can access JWT directly from context.request_headers.
            # We include access_token as fallback for backward compatibility.
            payload = {
                "prompt": message,
                "sessionId": session_id,
                "access_token": access_token,  # Fallback for backward compatibility
            }

            # Set headers with JWT in Authorization header (OAuth)
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
            }

            # Log the invocation attempt
            logger.info(f"Invoking agent via HTTP: {url}")
            logger.debug(f"Headers: {list(headers.keys())}")

            # Track request timing
            import time as _time

            start_time = _time.time()

            # Make the HTTP POST request
            response = self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )

            # Calculate duration
            duration_ms = (_time.time() - start_time) * 1000

            # Log response details
            logger.debug(f"Response status: {response.status_code}")
            logger.debug(f"Response headers: {dict(response.headers)}")

            # Record API call for educational purposes
            _record_api_call(
                method="POST",
                url=url,
                request_headers={
                    k: v if k.lower() != "authorization" else "Bearer [TOKEN]" for k, v in headers.items()
                },
                request_body={k: v if k != "access_token" else "[MASKED]" for k, v in payload.items()},
                response_status=response.status_code,
                response_headers=dict(response.headers),
                response_body=response.json() if response.status_code < 400 else response.text[:500],
                duration_ms=duration_ms,
                call_type="api",
            )

            # Handle HTTP errors
            if response.status_code == 401:
                error_body = response.text
                logger.error(f"JWT validation failed - 401 Unauthorized: {error_body}")
                logger.error(f"WWW-Authenticate header: {response.headers.get('WWW-Authenticate', 'N/A')}")
                raise AuthenticationError("Authentication failed. Your session may have expired. Please log in again.")

            if response.status_code == 403:
                error_body = response.text
                logger.error(f"Authorization denied - 403 Forbidden: {error_body}")

                # Check if it's an auth method mismatch
                if "authorization method mismatch" in error_body.lower():
                    logger.error("Agent may still be configured for SigV4 instead of OAuth")
                    raise RuntimeError(
                        "Authorization method mismatch. The agent may need to be reconfigured for OAuth authentication."
                    )

                raise AuthorizationError("You don't have permission to perform this action.")

            if response.status_code == 429:
                logger.warning("Rate limited - 429 Too Many Requests")
                raise RateLimitError("Too many requests. Please wait a moment and try again.")

            if response.status_code >= 400:
                error_body = response.text
                logger.error(f"HTTP error {response.status_code}: {error_body}")
                raise RuntimeError(f"Agent invocation failed: {error_body}")

            # Parse successful response
            logger.debug(f"Received successful response for session {session_id}")
            return self._parse_http_response(response, session_id)

        except AuthenticationError:
            raise
        except AuthorizationError:
            raise
        except RateLimitError:
            raise
        except requests.exceptions.Timeout:
            logger.error(f"Request timed out after {self.timeout}s")
            raise RuntimeError(
                f"Request timed out after {self.timeout} seconds. The agent may be taking too long to respond."
            )
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error: {e}")
            raise RuntimeError("Failed to connect to AgentCore. Please check your network connection.")
        except Exception as e:
            full_error = str(e)

            # Log full error details for debugging
            logger.error("=== AGENT INVOCATION ERROR ===")
            logger.error(f"Agent ARN: {agent_arn}")
            logger.error(f"Session ID: {session_id}")
            logger.error(f"Full error: {full_error}")
            logger.error(f"Error type: {type(e).__name__}")

            raise RuntimeError(f"Failed to invoke agent: {e}")

    def _parse_response(self, response: Any, session_id: str) -> Dict[str, Any]:
        """
        Parse AC Runtime response into standard format.

        Args:
            response: Raw response from AC Runtime
            session_id: Session ID for the request

        Returns:
            Standardized response dictionary
        """
        try:
            # Read the streaming body
            if "body" in response:
                body = response["body"].read()
                if isinstance(body, bytes):
                    body = body.decode("utf-8")

                # Try to parse as JSON
                try:
                    data = json.loads(body)
                    return {
                        "response": data.get("output", data.get("response", str(data))),
                        "sessionId": session_id,
                        "metadata": data.get("metadata", {}),
                    }
                except json.JSONDecodeError:
                    return {
                        "response": body,
                        "sessionId": session_id,
                        "metadata": {},
                    }

            # Handle dict response
            if isinstance(response, dict):
                return {
                    "response": response.get("output", response.get("response", str(response))),
                    "sessionId": session_id,
                    "metadata": response.get("metadata", {}),
                }

            return {
                "response": str(response),
                "sessionId": session_id,
                "metadata": {},
            }

        except Exception as e:
            logger.error(f"Error parsing response: {e}")
            return {
                "response": str(response),
                "sessionId": session_id,
                "metadata": {"parse_error": str(e)},
            }

    def _parse_http_response(self, response: requests.Response, session_id: str) -> Dict[str, Any]:
        """
        Parse HTTP response from AgentCore into standard format.

        Args:
            response: requests.Response object
            session_id: Session ID for the request

        Returns:
            Standardized response dictionary
        """
        try:
            # Try to parse as JSON
            try:
                data = response.json()
                logger.debug(f"Response JSON: {data}")

                # Handle various response formats
                if isinstance(data, dict):
                    # Check for common response fields
                    text_response = (
                        data.get("output")
                        or data.get("response")
                        or data.get("completion")
                        or data.get("text")
                        or data.get("message")
                        or str(data)
                    )
                    return {
                        "response": text_response,
                        "sessionId": session_id,
                        "metadata": data.get("metadata", {}),
                    }
                else:
                    return {
                        "response": str(data),
                        "sessionId": session_id,
                        "metadata": {},
                    }

            except json.JSONDecodeError:
                # Return raw text if not JSON
                return {
                    "response": response.text,
                    "sessionId": session_id,
                    "metadata": {},
                }

        except Exception as e:
            logger.error(f"Error parsing HTTP response: {e}")
            return {
                "response": response.text if response.text else "No response received",
                "sessionId": session_id,
                "metadata": {"parse_error": str(e)},
            }

    def send_message_streaming(
        self,
        message: str,
        session_id: str,
        access_token: str,
    ) -> Generator[str, None, None]:
        """
        Send a message and stream the response.

        Args:
            message: User message to send to the agent
            session_id: Session ID for conversation continuity
            access_token: Auth0 JWT access token

        Yields:
            Text chunks as they arrive from the agent
        """
        # For now, fall back to non-streaming and yield the full response
        # Streaming support can be added when the API supports it
        try:
            result = self.send_message(message, session_id, access_token)
            yield result.get("response", "")
        except Exception as e:
            raise RuntimeError(f"Streaming failed: {e}")

    def validate_token(self, access_token: str) -> bool:
        """
        Validate that an access token is present.

        Args:
            access_token: Auth0 JWT access token to validate

        Returns:
            True if token is present, False otherwise

        Note:
            Full token validation happens on actual invocation.
            The AC Runtime's customJWTAuthorizer validates the token.
        """
        if not self.coordinator_agent_id:
            logger.warning("Cannot validate token - agent not configured")
            return True  # Assume valid if we can't check

        # Token validation happens on actual invocation
        # For now, just check if we have a token
        return bool(access_token)

    def health_check(self) -> Dict[str, Any]:
        """
        Check AC Runtime health status.

        Returns:
            Health status dictionary
        """
        return {
            "status": "ok",
            "region": self.region,
            "agent_configured": bool(self.coordinator_agent_id),
            "endpoint": self.base_url,
            "auth_method": "OAuth/JWT",
        }

    # =========================================================================
    # Aliases for backward compatibility
    # =========================================================================

    def invoke_coordinator_agent(self, message: str, session_id: str, access_token: str, **kwargs) -> Iterator[Dict]:
        """Backward-compatible alias for send_message()."""
        result = self.send_message(message, session_id, access_token)
        yield {"chunk": {"bytes": result.get("response", "").encode()}}

    def get_full_response(self, message: str, session_id: str, access_token: str, **kwargs) -> str:
        """Backward-compatible alias that returns full response text."""
        result = self.send_message(message, session_id, access_token)
        return result.get("response", "")

    def invoke_with_streaming(self, message: str, session_id: str, access_token: str, **kwargs) -> Iterator[str]:
        """Backward-compatible streaming alias."""
        yield from self.send_message_streaming(message, session_id, access_token)


# =============================================================================
# Custom Exceptions
# =============================================================================


class AuthenticationError(Exception):
    """Raised when JWT authentication fails."""

    pass


class AuthorizationError(Exception):
    """Raised when user lacks required permissions."""

    pass


class RateLimitError(Exception):
    """Raised when rate limit is exceeded."""

    pass
