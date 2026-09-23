# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
HTTP client for agent-to-agent communication with JWT-based authentication.

This client enables coordinator agents to invoke action agents via HTTP.
The coordinator performs RFC 8693 token exchange to mint attenuated tokens
per sub-agent before routing. The original JWT is passed in the Authorization
header for platform-level auth, while the exchanged token (with reduced scopes)
is passed in the payload for application-level scope enforcement.

Flow:
1. Client authenticates with IdP (Auth0) and receives JWT (13 scopes)
2. Client calls Coordinator Agent via HTTP with JWT
3. Coordinator validates JWT, extracts user context, scope-gates tools
4. Coordinator exchanges token (RFC 8693) with per-agent scope attenuation
5. Action Agents validate exchanged token and process requests with verified identity

This approach ensures:
- End-to-end authentication chain
- Least-privilege access per agent (scope attenuation)
- Audit trail through JWT claims and RFC 8693 `act` delegation chain
- No credential escalation (exchanged tokens are strictly scope-attenuated)
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# OpenTelemetry trace context propagation for distributed tracing
try:
    from opentelemetry.propagate import inject as otel_inject

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

logger = logging.getLogger(__name__)


class AgentInvocationError(Exception):
    """Base exception for agent invocation errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        agent_id: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.agent_id = agent_id


class AuthenticationError(AgentInvocationError):
    """Raised when JWT authentication fails (401)."""

    pass


class AuthorizationError(AgentInvocationError):
    """Raised when authorization is denied (403)."""

    pass


class AgentNotFoundError(AgentInvocationError):
    """Raised when the target agent is not found (404)."""

    pass


class AgentTimeoutError(AgentInvocationError):
    """Raised when agent invocation times out."""

    pass


@dataclass
class AgentEndpoint:
    """Configuration for an agent endpoint."""

    agent_id: str
    agent_name: str
    # Optional override URL - if not set, uses standard AC Runtime URL
    endpoint_url: Optional[str] = None


class AgentHttpClient:
    """
    HTTP client for invoking AgentCore agents with JWT-based authentication.

    This client is used for agent-to-agent communication where the calling
    agent (e.g., Coordinator) invokes another agent (e.g., Profile) with
    the original JWT for platform auth and an exchanged token (RFC 8693)
    for application-level scope enforcement.

    Example:
        client = AgentHttpClient()

        # Forward JWT to profile agent
        response = client.invoke_agent(
            agent_id="profile_agent-abc123",
            input_text="Get customer profile",
            jwt_token="eyJhbG...",  # Original JWT from client
            session_id="session-123"
        )
    """

    def __init__(
        self,
        region: Optional[str] = None,
        account_id: Optional[str] = None,
        timeout: int = 60,
        max_retries: int = 3,
        retry_backoff_factor: float = 0.5,
    ):
        """
        Initialize the agent HTTP client.

        Args:
            region: AWS region (defaults to AWS_REGION env var)
            account_id: AWS account ID (defaults to EXPECTED_AWS_ACCOUNT env var)
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
            retry_backoff_factor: Backoff factor for retries
        """
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self.account_id = account_id or os.getenv("EXPECTED_AWS_ACCOUNT", "")
        self.timeout = timeout

        # Build the base endpoint URL for AgentCore Runtime
        self.base_url = f"https://bedrock-agentcore.{self.region}.amazonaws.com"

        # Create session with retry configuration
        self._session = requests.Session()

        # Configure retries for transient errors
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=retry_backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST", "GET"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

        logger.info(f"AgentHttpClient initialized - Region: {self.region}, Base URL: {self.base_url}")

    def _build_agent_runtime_arn(self, agent_id: str) -> str:
        """
        Build the AgentCore Runtime ARN from agent ID.

        Args:
            agent_id: Agent ID (e.g., profile_agent-abc123)

        Returns:
            Full ARN string
        """
        return f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:runtime/{agent_id}"

    def _build_invocation_url(self, agent_id: str, endpoint_url: Optional[str] = None) -> str:
        """
        Build the invocation URL for an agent.

        Args:
            agent_id: Agent ID
            endpoint_url: Optional override URL

        Returns:
            Full invocation URL
        """
        if endpoint_url:
            return endpoint_url

        agent_arn = self._build_agent_runtime_arn(agent_id)
        encoded_arn = quote(agent_arn, safe="")
        return f"{self.base_url}/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"

    def invoke_agent(
        self,
        agent_id: str,
        input_text: str,
        jwt_token: str,
        session_id: str,
        additional_context: Optional[Dict[str, Any]] = None,
        endpoint_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Invoke an agent via HTTP with JWT-based authentication.

        The original JWT is passed in the Authorization header for platform-level
        auth. The additional_context dict can carry an exchanged token (RFC 8693)
        for application-level scope enforcement.

        Args:
            agent_id: Target agent ID
            input_text: Input text/prompt for the agent
            jwt_token: JWT token to forward (from original request)
            session_id: Session ID for conversation continuity
            additional_context: Optional additional context to include
            endpoint_url: Optional override URL for the agent

        Returns:
            Response dictionary with:
            - response: Agent's text response
            - session_id: Session ID
            - metadata: Additional response metadata

        Raises:
            AuthenticationError: If JWT validation fails (401)
            AuthorizationError: If authorization is denied (403)
            AgentNotFoundError: If agent is not found (404)
            AgentTimeoutError: If request times out
            AgentInvocationError: For other errors
        """
        url = self._build_invocation_url(agent_id, endpoint_url)

        # Prepare request payload
        payload = {
            "prompt": input_text,
            "sessionId": session_id,
        }

        # Add additional context if provided
        if additional_context:
            payload["context"] = additional_context

        # Set headers with JWT for platform-level authentication
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
            # Add header to indicate this is an agent-to-agent call
            "X-AgentCore-Source": "agent-invocation",
        }

        # Inject OpenTelemetry trace context (traceparent, tracestate) into headers
        # This enables distributed trace correlation across agent boundaries
        # in CloudWatch → Traces → Trajectory
        if OTEL_AVAILABLE:
            try:
                otel_inject(headers)
                logger.debug(f"Injected OTEL trace context: traceparent={headers.get('traceparent', 'N/A')}")
            except Exception as e:
                logger.debug(f"Could not inject OTEL trace context: {e}")

        logger.info(f"Invoking agent {agent_id} via HTTP")
        logger.debug(f"URL: {url}")
        logger.debug(f"Session ID: {session_id}")

        try:
            response = self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )

            logger.debug(f"Response status: {response.status_code}")

            # Handle HTTP errors
            if response.status_code == 401:
                error_msg = self._extract_error_message(response)
                logger.error(f"JWT validation failed for agent {agent_id}: {error_msg}")
                raise AuthenticationError(
                    f"Authentication failed: {error_msg}",
                    status_code=401,
                    agent_id=agent_id,
                )

            if response.status_code == 403:
                error_msg = self._extract_error_message(response)
                logger.error(f"Authorization denied for agent {agent_id}: {error_msg}")
                raise AuthorizationError(
                    f"Authorization denied: {error_msg}",
                    status_code=403,
                    agent_id=agent_id,
                )

            if response.status_code == 404:
                logger.error(f"Agent not found: {agent_id}")
                raise AgentNotFoundError(
                    f"Agent not found: {agent_id}",
                    status_code=404,
                    agent_id=agent_id,
                )

            if response.status_code >= 400:
                error_msg = self._extract_error_message(response)
                logger.error(f"Agent invocation failed ({response.status_code}): {error_msg}")
                raise AgentInvocationError(
                    f"Agent invocation failed: {error_msg}",
                    status_code=response.status_code,
                    agent_id=agent_id,
                )

            # Parse successful response
            return self._parse_response(response, session_id)

        except requests.exceptions.Timeout:
            logger.error(f"Request to agent {agent_id} timed out after {self.timeout}s")
            raise AgentTimeoutError(
                f"Request timed out after {self.timeout} seconds",
                agent_id=agent_id,
            )
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error to agent {agent_id}: {e}")
            raise AgentInvocationError(
                f"Failed to connect to agent: {e}",
                agent_id=agent_id,
            )
        except (
            AuthenticationError,
            AuthorizationError,
            AgentNotFoundError,
            AgentTimeoutError,
        ):
            raise
        except Exception as e:
            logger.exception(f"Unexpected error invoking agent {agent_id}")
            raise AgentInvocationError(
                f"Unexpected error: {e}",
                agent_id=agent_id,
            )

    def _extract_error_message(self, response: requests.Response) -> str:
        """Extract error message from response."""
        try:
            data = response.json()
            return data.get("message") or data.get("error") or response.text
        except json.JSONDecodeError:
            return response.text or f"HTTP {response.status_code}"

    def _parse_response(
        self,
        response: requests.Response,
        session_id: str,
    ) -> Dict[str, Any]:
        """
        Parse HTTP response into standard format.

        Args:
            response: requests.Response object
            session_id: Session ID

        Returns:
            Standardized response dictionary
        """
        try:
            data = response.json()
            logger.debug(f"Response data: {data}")

            if isinstance(data, dict):
                # Handle various response formats
                text_response = (
                    data.get("output")
                    or data.get("response")
                    or data.get("completion")
                    or data.get("text")
                    or data.get("message")
                    or json.dumps(data)
                )
                return {
                    "response": text_response,
                    "session_id": session_id,
                    "metadata": data.get("metadata", {}),
                    "raw_response": data,
                }
            else:
                return {
                    "response": str(data),
                    "session_id": session_id,
                    "metadata": {},
                    "raw_response": data,
                }

        except json.JSONDecodeError:
            return {
                "response": response.text,
                "session_id": session_id,
                "metadata": {},
                "raw_response": response.text,
            }

    def invoke_with_user_context(
        self,
        agent_id: str,
        input_text: str,
        jwt_token: str,
        session_id: str,
        user_context: Dict[str, Any],
        endpoint_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Invoke an agent with JWT authentication and explicit user context.

        This method is useful when the calling agent has enriched the user
        context with additional information (e.g., exchanged token claims).

        Args:
            agent_id: Target agent ID
            input_text: Input text/prompt for the agent
            jwt_token: JWT token to forward
            session_id: Session ID
            user_context: User context dictionary to include
            endpoint_url: Optional override URL

        Returns:
            Response dictionary
        """
        return self.invoke_agent(
            agent_id=agent_id,
            input_text=input_text,
            jwt_token=jwt_token,
            session_id=session_id,
            additional_context={"user_context": user_context},
            endpoint_url=endpoint_url,
        )

    def health_check(self, agent_id: str, endpoint_url: Optional[str] = None) -> Dict[str, Any]:
        """
        Check if an agent endpoint is reachable.

        Args:
            agent_id: Agent ID to check
            endpoint_url: Optional override URL

        Returns:
            Health status dictionary
        """
        # For health checks, we'd typically hit a /health endpoint
        # but AgentCore Runtime may not expose this directly
        return {
            "agent_id": agent_id,
            "status": "unknown",
            "message": "Health check endpoint not available for AgentCore Runtime",
        }

    def close(self):
        """Close the HTTP session."""
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
