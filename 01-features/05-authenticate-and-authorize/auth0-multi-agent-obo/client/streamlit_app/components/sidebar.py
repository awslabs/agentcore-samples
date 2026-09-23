# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Sidebar component with user information and controls.
"""

import streamlit as st

from auth0_handler import Auth0Handler
from session_manager import SessionManager


def render_sidebar(auth_handler: Auth0Handler, session_manager: SessionManager):
    """
    Render sidebar with user info and logout.

    Args:
        auth_handler: Auth0Handler instance
        session_manager: SessionManager instance
    """
    with st.sidebar:
        # Application title
        st.markdown("# AgentCore Identity")
        st.markdown("### Financial Assistant")
        st.markdown("---")

        # User profile section
        user_info = session_manager.get_user_info()
        tokens = session_manager.get_tokens()

        if user_info:
            st.markdown("### User Profile")

            # User avatar (if available)
            picture = user_info.get("picture")
            if picture:
                st.image(picture, width=80)

            # User name
            name = session_manager.get_user_name()
            st.markdown(f"**{name}**")

            # User email
            email = session_manager.get_user_email()
            if email:
                st.markdown(f"Email: {email}")

            st.markdown("---")

        # Token status
        if tokens:
            st.markdown("### Token Status")

            # Token expiry
            time_until_expiry = tokens.time_until_expiry()
            minutes = int(time_until_expiry / 60)
            seconds = int(time_until_expiry % 60)

            if time_until_expiry > 0:
                st.success(f"Active: {minutes}m {seconds}s remaining")
            else:
                st.error("Expired")

            # Token scopes
            with st.expander("Token Details"):
                st.markdown(f"**Type:** {tokens.token_type}")
                st.markdown(f"**Scopes:** {tokens.scope}")

                # Show custom claims from ID token
                if tokens.id_token:
                    try:
                        claims = auth_handler.decode_id_token(tokens.id_token)
                        st.markdown("**Claims:**")

                        # Filter custom claims
                        from shared.config.settings import settings

                        namespace = settings.auth0.claims_namespace

                        custom_claims = {
                            k.replace(namespace, ""): v for k, v in claims.items() if k.startswith(namespace)
                        }

                        if custom_claims:
                            for key, value in custom_claims.items():
                                st.text(f"{key}: {value}")

                    except Exception as e:
                        st.error(f"Failed to decode token: {e}")

            st.markdown("---")

        # Session info
        st.markdown("### Session")
        session_id = session_manager.get_session_id()
        st.text(f"ID: {session_id[:8]}...")

        message_count = len(session_manager.get_messages())
        st.text(f"Messages: {message_count}")

        # Session actions
        col1, col2 = st.columns(2)
        with col1:
            if st.button("New Session", use_container_width=True):
                session_manager.clear_messages()
                session_manager.reset_session_id()
                st.success("Session reset")
                st.rerun()

        with col2:
            if st.button("Clear Chat", use_container_width=True):
                session_manager.clear_messages()
                st.success("Chat cleared")
                st.rerun()

        st.markdown("---")

        # Logout button
        if st.button("Logout", type="primary", use_container_width=True):
            handle_logout(auth_handler, session_manager)

        # Debug info
        with st.expander("Debug Info"):
            session_info = session_manager.get_session_info()
            st.json(session_info)

        # Application info
        st.markdown("---")
        st.markdown("#### About")
        st.markdown("""
        This application demonstrates:
        - Auth0 OAuth 2.0 with PKCE
        - JWT authentication
        - AgentCore Runtime integration
        - Multi-agent orchestration
        """)

        # Configuration
        with st.expander("Configuration"):
            from shared.config.settings import settings

            st.markdown("**Auth0:**")
            st.text(f"Domain: {settings.auth0.domain}")
            st.text(f"Audience: {settings.auth0.audience}")

            st.markdown("**AgentCore:**")
            st.text(f"Region: {settings.agentcore.region}")
            if settings.agentcore.coordinator_agent_id:
                st.text(f"Agent: {settings.agentcore.coordinator_agent_id[:8]}...")

        # Version info
        st.markdown("---")
        st.markdown("*Version 0.1.0*")


def handle_logout(auth_handler: Auth0Handler, session_manager: SessionManager):
    """
    Handle user logout.

    Args:
        auth_handler: Auth0Handler instance
        session_manager: SessionManager instance
    """
    # Stop callback server if running
    callback_server = session_manager.get_callback_server()
    if callback_server:
        try:
            callback_server.stop()
        except Exception:
            pass

    # Clear session
    session_manager.logout()

    # Show logout confirmation
    st.success("Logged out successfully")
    st.info("Redirecting to Auth0 logout...")

    # Generate Auth0 logout URL
    logout_url = auth_handler.logout()

    # Note: In a real browser app, you would redirect to logout_url
    # For Streamlit, we show the URL
    st.markdown(f"[Complete logout on Auth0]({logout_url})")

    # Rerun to show login page
    st.rerun()


def render_token_refresh_warning(session_manager: SessionManager):
    """
    Show warning when token is about to expire.

    Args:
        session_manager: SessionManager instance
    """
    tokens = session_manager.get_tokens()
    if not tokens:
        return

    time_until_expiry = tokens.time_until_expiry()

    # Show warning if less than 5 minutes remaining
    if 0 < time_until_expiry < 300:
        minutes = int(time_until_expiry / 60)
        st.sidebar.warning(f"Token expires in {minutes} minutes")

        if tokens.refresh_token:
            if st.sidebar.button("Refresh Token"):
                refresh_token_handler(session_manager)
        else:
            st.sidebar.info("Please sign in again when token expires")


def refresh_token_handler(session_manager: SessionManager):
    """
    Handle token refresh.

    Args:
        session_manager: SessionManager instance
    """
    tokens = session_manager.get_tokens()
    if not tokens or not tokens.refresh_token:
        st.error("No refresh token available")
        return

    try:
        from auth0_handler import Auth0Handler

        auth_handler = Auth0Handler()

        # Refresh tokens
        new_tokens = auth_handler.refresh_tokens(tokens.refresh_token)

        # Update session
        session_manager.store_tokens(new_tokens)

        st.success("Token refreshed successfully")
        st.rerun()

    except Exception as e:
        st.error(f"Failed to refresh token: {str(e)}")
        st.info("Please sign in again")
