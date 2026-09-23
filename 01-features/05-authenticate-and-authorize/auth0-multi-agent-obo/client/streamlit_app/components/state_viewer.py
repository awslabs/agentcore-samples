# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Application State Viewer Component

Educational component that provides visibility into the application's
authentication state, PKCE flow, and session management.
"""

import streamlit as st
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass
import time


@dataclass
class StateTransition:
    """Record of a state transition."""

    timestamp: float
    from_state: str
    to_state: str
    trigger: str
    details: Optional[Dict[str, Any]] = None

    @property
    def formatted_timestamp(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")


def get_state_history() -> List[StateTransition]:
    """Get state transition history from session state."""
    if "state_history" not in st.session_state:
        st.session_state["state_history"] = []
    return st.session_state["state_history"]


def record_state_transition(from_state: str, to_state: str, trigger: str, details: Optional[Dict] = None):
    """Record a state transition."""
    history = get_state_history()
    history.append(
        StateTransition(
            timestamp=time.time(),
            from_state=from_state,
            to_state=to_state,
            trigger=trigger,
            details=details,
        )
    )
    # Keep only last 50 transitions
    if len(history) > 50:
        st.session_state["state_history"] = history[-50:]


def render_state_viewer(session_manager):
    """
    Render the application state viewer.

    Args:
        session_manager: SessionManager instance
    """
    st.markdown("## Application State")
    st.markdown("""
    This view shows the internal state of the application, helping you understand
    how authentication state, sessions, and tokens are managed throughout the
    application lifecycle.
    """)

    # Tabs for different views
    tab1, tab2, tab3, tab4 = st.tabs(["Auth State Machine", "PKCE Flow", "Session State", "State History"])

    with tab1:
        render_auth_state_machine(session_manager)

    with tab2:
        render_pkce_flow()

    with tab3:
        render_session_state(session_manager)

    with tab4:
        render_state_history()


def render_auth_state_machine(session_manager):
    """Render the authentication state machine visualization."""
    st.markdown("### Authentication State Machine")

    # Determine current state
    tokens = session_manager.get_tokens()
    user_info = session_manager.get_user_info()

    if tokens and not session_manager.is_token_expired():
        current_state = "AUTHENTICATED"
        state_color = "#4ecdc4"
    elif tokens and session_manager.is_token_expired():
        current_state = "TOKEN_EXPIRED"
        state_color = "#ff6b6b"
    elif "code" in st.query_params:
        current_state = "CALLBACK_RECEIVED"
        state_color = "#ffd93d"
    else:
        current_state = "UNAUTHENTICATED"
        state_color = "#95a5a6"

    # Visual state diagram
    st.markdown("""
    ```
                                    +------------------+
                                    |  UNAUTHENTICATED |
                                    +--------+---------+
                                             |
                                             | Login clicked
                                             v
                                    +------------------+
                                    |  PKCE_INITIATED  |
                                    +--------+---------+
                                             |
                                             | Redirect to Auth0
                                             v
                                    +------------------+
                                    | AUTH0_REDIRECT   |
                                    +--------+---------+
                                             |
                                             | User authenticates
                                             v
                                    +------------------+
                                    | CALLBACK_RECEIVED|
                                    +--------+---------+
                                             |
                                             | Exchange code for tokens
                                             v
                                    +------------------+
                                    |  AUTHENTICATED   |<----+
                                    +--------+---------+     |
                                             |               |
                            Token expires    |    Token      |
                                             v    refresh    |
                                    +------------------+     |
                                    |  TOKEN_EXPIRED   |-----+
                                    +------------------+
                                             |
                                             | No refresh / Logout
                                             v
                                    +------------------+
                                    |  UNAUTHENTICATED |
                                    +------------------+
    ```
    """)

    # Current state indicator
    st.markdown("---")
    st.markdown("### Current State")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            f"""
        <div style="
            background: {state_color};
            color: white;
            padding: 20px;
            border-radius: 10px;
            text-align: center;
            font-size: 24px;
            font-weight: bold;
        ">
            {current_state}
        </div>
        """,
            unsafe_allow_html=True,
        )

    with col2:
        # State details
        st.markdown("**State Details:**")
        st.markdown(f"- **Has Tokens:** {'Yes' if tokens else 'No'}")
        st.markdown(f"- **Has User Info:** {'Yes' if user_info else 'No'}")
        if tokens:
            remaining = tokens.time_until_expiry()
            st.markdown(f"- **Token Valid:** {'Yes' if remaining > 0 else 'No'}")
            if remaining > 0:
                st.markdown(f"- **Expires In:** {int(remaining / 60)}m {int(remaining % 60)}s")

    # State transition actions
    st.markdown("---")
    st.markdown("### Available Transitions")

    if current_state == "UNAUTHENTICATED":
        st.info("Action: Click 'Login' to initiate PKCE flow")
    elif current_state == "AUTHENTICATED":
        st.info("Actions: 'Logout' to end session, or wait for token expiry")
        if tokens and tokens.refresh_token:
            st.info("Token can be refreshed when it expires")
    elif current_state == "TOKEN_EXPIRED":
        if tokens and tokens.refresh_token:
            st.warning("Token expired - click 'Refresh Token' to continue")
        else:
            st.warning("Token expired - please login again")
    elif current_state == "CALLBACK_RECEIVED":
        st.info("Processing OAuth callback - exchanging code for tokens")


def render_pkce_flow():
    """Render the PKCE (Proof Key for Code Exchange) flow visualization."""
    st.markdown("### PKCE Flow (Proof Key for Code Exchange)")

    st.markdown("""
    PKCE is a security extension to OAuth 2.0 that protects against authorization
    code interception attacks. It's required for public clients (like SPAs) that
    cannot securely store a client secret.
    """)

    # PKCE flow diagram
    st.markdown("""
    ```
    Step 1: Generate PKCE Values (Client-side)
    +------------------------------------------+
    |  code_verifier = random(43-128 chars)    |
    |  code_challenge = SHA256(code_verifier)  |
    |  state = random(unique per request)      |
    +------------------------------------------+
                        |
                        v
    Step 2: Authorization Request
    +------------------------------------------+
    |  GET /authorize?                         |
    |    response_type=code                    |
    |    client_id={client_id}                 |
    |    redirect_uri={callback}               |
    |    scope=openid profile email            |
    |    state={state}                         |
    |    code_challenge={code_challenge}       |
    |    code_challenge_method=S256            |
    +------------------------------------------+
                        |
                        v
    Step 3: User Authenticates with Auth0
    +------------------------------------------+
    |  User enters credentials                 |
    |  Auth0 validates user                    |
    |  Auth0 generates authorization code      |
    +------------------------------------------+
                        |
                        v
    Step 4: Callback with Authorization Code
    +------------------------------------------+
    |  GET /callback?                          |
    |    code={authorization_code}             |
    |    state={state}                         |
    +------------------------------------------+
                        |
                        v
    Step 5: Token Exchange
    +------------------------------------------+
    |  POST /oauth/token                       |
    |    grant_type=authorization_code         |
    |    code={authorization_code}             |
    |    redirect_uri={callback}               |
    |    client_id={client_id}                 |
    |    code_verifier={code_verifier}  <---   |
    +------------------------------------------+
                        |                 |
                        |     PKCE Proof  |
                        v                 |
    Step 6: Tokens Issued                 |
    +------------------------------------------+
    |  {                                       |
    |    "access_token": "eyJ...",             |
    |    "id_token": "eyJ...",                 |
    |    "refresh_token": "...",               |
    |    "token_type": "Bearer",               |
    |    "expires_in": 86400                   |
    |  }                                       |
    +------------------------------------------+
    ```
    """)

    # Current PKCE state
    st.markdown("---")
    st.markdown("### Current PKCE State")

    # Check for PKCE state in session
    pkce_initiated = st.session_state.get("pkce_initiated", False)
    _pkce_state = st.session_state.get("pkce_state")
    code_in_url = "code" in st.query_params

    col1, col2, col3 = st.columns(3)

    with col1:
        if pkce_initiated:
            st.success("PKCE Initiated")
        else:
            st.info("PKCE Not Started")

    with col2:
        if code_in_url:
            st.success("Auth Code Received")
        else:
            st.info("Awaiting Auth Code")

    with col3:
        # Check for tokens
        if "tokens" in st.session_state and st.session_state["tokens"]:
            st.success("Tokens Obtained")
        else:
            st.info("No Tokens Yet")

    # Security explanation
    st.markdown("---")
    st.markdown("### Why PKCE?")

    st.markdown("""
    | Without PKCE | With PKCE |
    |--------------|-----------|
    | Authorization code can be intercepted | Code is useless without code_verifier |
    | Attacker can exchange code for tokens | Only the client that initiated flow can complete it |
    | Client secret required (not safe for SPAs) | No client secret needed |
    | Vulnerable to MITM attacks | Protected against interception |
    """)


def render_session_state(session_manager):
    """Render current session state details."""
    st.markdown("### Session State")

    st.markdown("""
    The application maintains state in Streamlit's session state, which persists
    across reruns but is isolated per browser tab.
    """)

    # Session info
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### Session Identifiers")
        session_id = session_manager.get_session_id()
        st.markdown(f"**Session ID:** `{session_id[:8]}...{session_id[-4:]}`")
        st.markdown(f"**Full ID:** `{session_id}`")

        messages = session_manager.get_messages()
        st.markdown(f"**Messages in History:** {len(messages)}")

    with col2:
        st.markdown("#### Authentication State")
        st.markdown(f"**Authenticated:** {'Yes' if session_manager.is_authenticated() else 'No'}")

        tokens = session_manager.get_tokens()
        if tokens:
            st.markdown(f"**Token Type:** {tokens.token_type}")
            st.markdown(f"**Has Refresh Token:** {'Yes' if tokens.refresh_token else 'No'}")
            st.markdown(f"**Expires At:** {datetime.fromtimestamp(tokens.expires_at).strftime('%H:%M:%S')}")

    # Full session state dump
    st.markdown("---")
    st.markdown("#### Raw Session State")

    with st.expander("View All Session State Keys"):
        session_keys = list(st.session_state.keys())
        st.markdown(f"**Total Keys:** {len(session_keys)}")

        for key in sorted(session_keys):
            value = st.session_state[key]
            value_type = type(value).__name__

            # Mask sensitive values
            if "token" in key.lower() or "secret" in key.lower():
                display_value = "[MASKED]"
            elif isinstance(value, (dict, list)):
                display_value = f"{value_type} with {len(value)} items"
            elif isinstance(value, str) and len(value) > 50:
                display_value = f"{value[:50]}..."
            else:
                display_value = str(value)

            st.markdown(f"- `{key}` ({value_type}): {display_value}")

    # Token details
    if tokens:
        st.markdown("---")
        st.markdown("#### Token Storage")

        token_state = {
            "token_type": tokens.token_type,
            "scope": tokens.scope,
            "expires_at": tokens.expires_at,
            "expires_at_formatted": datetime.fromtimestamp(tokens.expires_at).isoformat(),
            "time_until_expiry_seconds": tokens.time_until_expiry(),
            "has_access_token": bool(tokens.access_token),
            "has_id_token": bool(tokens.id_token),
            "has_refresh_token": bool(tokens.refresh_token),
            "access_token_length": len(tokens.access_token) if tokens.access_token else 0,
            "id_token_length": len(tokens.id_token) if tokens.id_token else 0,
        }

        st.json(token_state)


def render_state_history():
    """Render state transition history."""
    st.markdown("### State Transition History")

    history = get_state_history()

    if not history:
        st.info("No state transitions recorded yet. Interact with the application to see state changes.")

        # Show example transitions
        with st.expander("Example State Transitions"):
            st.markdown("""
            When you interact with the application, state transitions are recorded:

            | Time | From | To | Trigger |
            |------|------|-----|---------|
            | 10:00:01 | UNAUTHENTICATED | PKCE_INITIATED | Login clicked |
            | 10:00:05 | PKCE_INITIATED | CALLBACK_RECEIVED | Auth0 redirect |
            | 10:00:06 | CALLBACK_RECEIVED | AUTHENTICATED | Token exchange |
            | 10:30:06 | AUTHENTICATED | TOKEN_EXPIRED | Token timeout |
            | 10:30:07 | TOKEN_EXPIRED | AUTHENTICATED | Token refresh |
            """)
        return

    # Display transitions
    st.markdown(f"**Total Transitions:** {len(history)}")

    for transition in reversed(history[-20:]):  # Show last 20
        col1, col2, col3, col4 = st.columns([1, 2, 2, 2])

        with col1:
            st.caption(transition.formatted_timestamp)

        with col2:
            st.markdown(f"`{transition.from_state}`")

        with col3:
            st.markdown(f"-> `{transition.to_state}`")

        with col4:
            st.caption(transition.trigger)

        if transition.details:
            with st.expander("Details"):
                st.json(transition.details)


def render_oauth_config_details():
    """Render OAuth configuration details for educational purposes."""
    st.markdown("### OAuth Configuration")

    try:
        from shared.config.settings import settings

        st.markdown("#### Auth0 Configuration")

        config_display = {
            "Domain": settings.auth0.domain,
            "Client ID": settings.auth0.client_id[:8] + "..." if settings.auth0.client_id else "Not set",
            "Audience": settings.auth0.audience,
            "Callback URL": settings.auth0.callback_url,
            "Claims Namespace": settings.auth0.claims_namespace,
        }

        for key, value in config_display.items():
            st.markdown(f"- **{key}:** `{value}`")

        st.markdown("#### AgentCore Configuration")

        agentcore_display = {
            "Region": settings.agentcore.region,
            "Coordinator Agent": settings.agentcore.coordinator_agent_id[:12] + "..."
            if settings.agentcore.coordinator_agent_id
            else "Not set",
        }

        for key, value in agentcore_display.items():
            st.markdown(f"- **{key}:** `{value}`")

        # Security note
        st.markdown("---")
        st.info("""
        **Security Note:** Client secrets are never exposed in the browser.
        This application uses PKCE, which doesn't require a client secret
        for the authorization code exchange.
        """)

    except ImportError:
        st.warning("Settings not available")


def render_token_refresh_flow():
    """Render token refresh flow explanation."""
    st.markdown("### Token Refresh Flow")

    st.markdown("""
    When an access token expires, the application can use the refresh token
    to obtain new tokens without requiring the user to log in again.
    """)

    st.markdown("""
    ```
    +------------------+
    |  Access Token    |
    |  (expires)       |
    +--------+---------+
             |
             | Token expired
             v
    +------------------+
    |  Check Refresh   |
    |  Token Available |
    +--------+---------+
             |
             | Has refresh token
             v
    +------------------+
    |  POST /token     |
    |  grant_type=     |
    |  refresh_token   |
    +--------+---------+
             |
             | Auth0 validates
             v
    +------------------+
    |  New Tokens      |
    |  - access_token  |
    |  - id_token      |
    |  - (new refresh) |
    +------------------+
    ```
    """)

    st.markdown("""
    **Key Points:**
    1. Refresh tokens have longer lifetimes than access tokens
    2. Refresh token rotation: each use may return a new refresh token
    3. If refresh fails, user must re-authenticate
    4. Refresh tokens should be stored securely (not in localStorage)
    """)
