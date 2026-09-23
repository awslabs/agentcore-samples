# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
API Call Log Component

Educational component that provides visibility into the sequence of API calls,
including request/response details, headers, and timing information.
"""

import streamlit as st
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass, field, asdict
import time


@dataclass
class APICallRecord:
    """Record of a single API call."""

    id: str
    timestamp: float
    method: str
    url: str
    request_headers: Dict[str, str]
    request_body: Optional[Dict[str, Any]]
    response_status: Optional[int] = None
    response_headers: Optional[Dict[str, str]] = None
    response_body: Optional[Any] = None
    duration_ms: Optional[float] = None
    error: Optional[str] = None
    call_type: str = "api"  # api, auth, token_refresh, etc.

    @property
    def formatted_timestamp(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S.%f")[:-3]

    @property
    def is_success(self) -> bool:
        return self.response_status is not None and 200 <= self.response_status < 300

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class APICallLog:
    """Collection of API call records with management methods."""

    calls: List[APICallRecord] = field(default_factory=list)
    max_calls: int = 50

    def add_call(self, call: APICallRecord):
        """Add a call to the log, maintaining max size."""
        self.calls.append(call)
        if len(self.calls) > self.max_calls:
            self.calls = self.calls[-self.max_calls :]

    def get_recent(self, count: int = 10) -> List[APICallRecord]:
        """Get most recent calls."""
        return list(reversed(self.calls[-count:]))

    def clear(self):
        """Clear all calls."""
        self.calls = []

    def get_by_type(self, call_type: str) -> List[APICallRecord]:
        """Get calls by type."""
        return [c for c in self.calls if c.call_type == call_type]


def get_api_call_log() -> APICallLog:
    """Get or create the API call log from session state."""
    if "api_call_log" not in st.session_state:
        st.session_state["api_call_log"] = APICallLog()
    return st.session_state["api_call_log"]


def record_api_call(
    method: str,
    url: str,
    request_headers: Dict[str, str],
    request_body: Optional[Dict[str, Any]] = None,
    response_status: Optional[int] = None,
    response_headers: Optional[Dict[str, str]] = None,
    response_body: Optional[Any] = None,
    duration_ms: Optional[float] = None,
    error: Optional[str] = None,
    call_type: str = "api",
) -> APICallRecord:
    """
    Record an API call to the session log.

    Args:
        method: HTTP method (GET, POST, etc.)
        url: Full URL of the request
        request_headers: Headers sent with request
        request_body: Request payload
        response_status: HTTP status code
        response_headers: Response headers
        response_body: Response payload
        duration_ms: Request duration in milliseconds
        error: Error message if request failed
        call_type: Type of call (api, auth, token_refresh)

    Returns:
        The created APICallRecord
    """
    call = APICallRecord(
        id=f"call_{int(time.time() * 1000)}",
        timestamp=time.time(),
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

    log = get_api_call_log()
    log.add_call(call)

    return call


def render_api_call_log():
    """Render the main API call log view."""
    st.markdown("## API Call Sequence")
    st.markdown("""
    This view shows the sequence of API calls made during your session,
    including authentication flows, agent invocations, and token operations.
    Understanding this flow is key to learning how the system works.
    """)

    log = get_api_call_log()

    # Controls
    col1, col2, col3 = st.columns([2, 2, 1])

    with col1:
        filter_type = st.selectbox(
            "Filter by type:",
            ["All", "Agent Calls", "Auth Calls", "Token Operations"],
            key="api_log_filter",
        )

    with col2:
        count = st.slider("Show last N calls:", 5, 50, 10, key="api_log_count")

    with col3:
        if st.button("Clear Log", key="clear_api_log"):
            log.clear()
            st.rerun()

    st.markdown("---")

    # Get filtered calls
    if filter_type == "Agent Calls":
        calls = [c for c in log.get_recent(count) if c.call_type == "api"]
    elif filter_type == "Auth Calls":
        calls = [c for c in log.get_recent(count) if c.call_type in ("auth", "oauth")]
    elif filter_type == "Token Operations":
        calls = [c for c in log.get_recent(count) if c.call_type in ("token_refresh", "token_exchange")]
    else:
        calls = log.get_recent(count)

    if not calls:
        st.info("No API calls recorded yet. Interact with the application to see calls appear here.")

        # Show example of what a call looks like
        with st.expander("Example: What an API call looks like"):
            render_example_call()
        return

    # Render call timeline
    st.markdown("### Call Timeline")

    for call in calls:
        render_api_call_card(call)


def render_api_call_card(call: APICallRecord):
    """Render a single API call as an expandable card."""
    # Determine status color and icon
    if call.error:
        _status_color = "red"
        status_icon = "[ERR]"
    elif call.is_success:
        _status_color = "green"
        status_icon = "[OK]"
    else:
        _status_color = "orange"
        status_icon = f"[{call.response_status or '?'}]"

    # Call type badge
    type_badges = {
        "api": ("API", "#4ecdc4"),
        "auth": ("AUTH", "#ff6b6b"),
        "oauth": ("OAUTH", "#ff6b6b"),
        "token_refresh": ("REFRESH", "#ffd93d"),
        "token_exchange": ("EXCHANGE", "#e17055"),
    }

    badge_text, badge_color = type_badges.get(call.call_type, ("OTHER", "#95a5a6"))

    # Create expandable section
    with st.expander(
        f"{status_icon} {call.formatted_timestamp} | {call.method} {_truncate_url(call.url)} | {call.duration_ms or 0:.0f}ms",
        expanded=bool(call.error),
    ):
        # Header row
        col1, col2, col3 = st.columns([1, 2, 1])

        with col1:
            st.markdown(
                f"""
            <span style="background: {badge_color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px;">
                {badge_text}
            </span>
            """,
                unsafe_allow_html=True,
            )

        with col2:
            st.markdown(f"**{call.method}** `{call.url}`")

        with col3:
            if call.duration_ms:
                st.metric("Duration", f"{call.duration_ms:.0f}ms")

        # Tabs for details
        tab1, tab2, tab3 = st.tabs(["Request", "Response", "Headers"])

        with tab1:
            render_request_details(call)

        with tab2:
            render_response_details(call)

        with tab3:
            render_headers_details(call)


def render_request_details(call: APICallRecord):
    """Render request details."""
    st.markdown("#### Request")

    # Method and URL
    st.markdown(f"**URL:** `{call.url}`")
    st.markdown(f"**Method:** `{call.method}`")

    # Headers summary (hiding sensitive values)
    st.markdown("**Key Headers:**")
    if call.request_headers:
        for key, value in call.request_headers.items():
            if key.lower() == "authorization":
                # Show token type but mask the actual token
                if value.startswith("Bearer "):
                    st.markdown(f"- `{key}`: Bearer [JWT TOKEN - {len(value) - 7} chars]")
                else:
                    st.markdown(f"- `{key}`: [MASKED]")
            elif key.lower() in ("x-api-key", "cookie"):
                st.markdown(f"- `{key}`: [MASKED]")
            else:
                st.markdown(f"- `{key}`: {value}")

    # Request body
    if call.request_body:
        st.markdown("**Request Body:**")
        # Mask sensitive fields
        masked_body = _mask_sensitive_fields(call.request_body)
        st.json(masked_body)


def render_response_details(call: APICallRecord):
    """Render response details."""
    st.markdown("#### Response")

    # Status
    if call.response_status:
        status_text = _get_status_text(call.response_status)
        if call.is_success:
            st.success(f"Status: {call.response_status} {status_text}")
        elif call.response_status >= 400:
            st.error(f"Status: {call.response_status} {status_text}")
        else:
            st.warning(f"Status: {call.response_status} {status_text}")

    # Error
    if call.error:
        st.error(f"Error: {call.error}")

    # Response body
    if call.response_body:
        st.markdown("**Response Body:**")
        if isinstance(call.response_body, dict):
            st.json(call.response_body)
        else:
            st.code(str(call.response_body)[:1000])


def render_headers_details(call: APICallRecord):
    """Render full headers view."""
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### Request Headers")
        if call.request_headers:
            for key, value in call.request_headers.items():
                # Mask sensitive headers
                if key.lower() in ("authorization", "x-api-key", "cookie"):
                    st.markdown(f"**{key}:** `[MASKED]`")
                else:
                    st.markdown(f"**{key}:** `{value}`")
        else:
            st.info("No headers recorded")

    with col2:
        st.markdown("#### Response Headers")
        if call.response_headers:
            for key, value in call.response_headers.items():
                st.markdown(f"**{key}:** `{value}`")
        else:
            st.info("No response headers recorded")


def render_example_call():
    """Render an example API call for educational purposes."""
    st.markdown("""
    When you send a message to the agent, the following happens:

    **1. Request is prepared:**
    ```
    POST https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{arn}/invocations
    ```

    **2. Headers are set:**
    ```json
    {
        "Authorization": "Bearer eyJhbG...[JWT Token]",
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "session-123"
    }
    ```

    **3. Request body is sent:**
    ```json
    {
        "prompt": "Show me my account balance",
        "sessionId": "session-123",
        "claims": { "sub": "auth0|123", "customer_id": "CUST-001" }
    }
    ```

    **4. AgentCore validates the JWT:**
    - Fetches JWKS from Auth0
    - Verifies signature
    - Checks expiration
    - Validates audience and issuer

    **5. Response is received:**
    ```json
    {
        "response": "Your checking account balance is $1,234.56",
        "sessionId": "session-123"
    }
    ```
    """)


def render_call_sequence_diagram():
    """Render a sequence diagram of the authentication and API flow."""
    st.markdown("### Authentication & API Flow")

    st.markdown("""
    ```
    Browser          Auth0           AgentCore Runtime       Agent
       |               |                    |                  |
       |--Login------->|                    |                  |
       |               |                    |                  |
       |<--Auth Code---|                    |                  |
       |               |                    |                  |
       |--Exchange---->|                    |                  |
       |   Code+PKCE   |                    |                  |
       |               |                    |                  |
       |<--JWT Tokens--|                    |                  |
       |               |                    |                  |
       |--API Request--+--Bearer Token----->|                  |
       |               |                    |                  |
       |               |<--Validate JWT-----|                  |
       |               |---JWKS------------>|                  |
       |               |                    |                  |
       |               |                    |--Forward-------->|
       |               |                    |  +Claims         |
       |               |                    |                  |
       |               |                    |<--Response-------|
       |               |                    |                  |
       |<--Response----+--------------------+                  |
       |               |                    |                  |
    ```
    """)

    st.markdown("""
    **Key Points:**
    1. **PKCE Flow**: Browser uses Proof Key for Code Exchange for secure token retrieval
    2. **JWT Validation**: AgentCore Runtime validates tokens against Auth0's JWKS endpoint
    3. **Claims Forwarding**: Validated claims are passed to the agent for authorization decisions
    4. **Stateless Auth**: Each request carries its own authentication - no server sessions needed
    """)


def render_call_statistics():
    """Render statistics about API calls."""
    log = get_api_call_log()

    if not log.calls:
        st.info("No statistics available yet.")
        return

    st.markdown("### Call Statistics")

    col1, col2, col3, col4 = st.columns(4)

    # Total calls
    with col1:
        st.metric("Total Calls", len(log.calls))

    # Success rate
    success_count = sum(1 for c in log.calls if c.is_success)
    success_rate = (success_count / len(log.calls)) * 100 if log.calls else 0
    with col2:
        st.metric("Success Rate", f"{success_rate:.1f}%")

    # Average duration
    durations = [c.duration_ms for c in log.calls if c.duration_ms]
    avg_duration = sum(durations) / len(durations) if durations else 0
    with col3:
        st.metric("Avg Duration", f"{avg_duration:.0f}ms")

    # Errors
    error_count = sum(1 for c in log.calls if c.error)
    with col4:
        st.metric("Errors", error_count)

    # Call types breakdown
    st.markdown("#### Calls by Type")
    type_counts = {}
    for call in log.calls:
        type_counts[call.call_type] = type_counts.get(call.call_type, 0) + 1

    for call_type, count in type_counts.items():
        st.markdown(f"- **{call_type}**: {count} calls")


# Helper functions


def _truncate_url(url: str, max_length: int = 60) -> str:
    """Truncate URL for display."""
    if len(url) <= max_length:
        return url
    return url[: max_length - 3] + "..."


def _mask_sensitive_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """Mask sensitive fields in a dictionary."""
    sensitive_keys = {
        "access_token",
        "refresh_token",
        "id_token",
        "password",
        "secret",
        "client_secret",
    }

    masked = {}
    for key, value in data.items():
        if key.lower() in sensitive_keys:
            if isinstance(value, str):
                masked[key] = f"[MASKED - {len(value)} chars]"
            else:
                masked[key] = "[MASKED]"
        elif isinstance(value, dict):
            masked[key] = _mask_sensitive_fields(value)
        else:
            masked[key] = value

    return masked


def _get_status_text(status_code: int) -> str:
    """Get human-readable status text."""
    status_texts = {
        200: "OK",
        201: "Created",
        204: "No Content",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        429: "Too Many Requests",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
    }
    return status_texts.get(status_code, "")
