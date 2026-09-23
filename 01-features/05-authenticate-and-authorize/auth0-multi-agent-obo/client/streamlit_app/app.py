# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Main Streamlit application for AgentCore Identity sample.
Demonstrates 3-legged OAuth with Auth0 and AgentCore Runtime integration.
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# Setup logging first
from logging_config import setup_logging

LOG_FILE = setup_logging()
import logging

logger = logging.getLogger(__name__)

import streamlit as st

from auth0_handler import Auth0Handler
from agentcore_client import AgentCoreClient
from session_manager import SessionManager
from components.login import render_login_page, render_token_expired_warning
from components.chat import render_chat_interface
from components.sidebar import render_sidebar, render_token_refresh_warning
from components.jwt_viewer import render_jwt_viewer, render_token_timeline
from components.api_call_log import (
    render_api_call_log,
    render_call_sequence_diagram,
    render_call_statistics,
)
from components.state_viewer import render_state_viewer, render_oauth_config_details
from shared.config.settings import settings


# Page configuration
st.set_page_config(
    page_title="AgentCore Financial Assistant",
    page_icon="$",
    layout="wide",
    initial_sidebar_state="expanded",
)


def handle_oauth_callback(auth_handler: Auth0Handler, session_manager: SessionManager) -> bool:
    """
    Handle OAuth callback if auth code is in URL params.

    Returns:
        True if callback was handled successfully, False otherwise
    """
    from pkce_store import get_pkce_state, remove_pkce_state

    # Check for auth code in URL query params
    query_params = st.query_params
    code = query_params.get("code")
    state = query_params.get("state")

    if not code or not state:
        return False

    # Get the PKCE code_verifier from the file-based store
    code_verifier = get_pkce_state(state)
    if not code_verifier:
        st.error("Authentication session expired. Please try again.")
        st.query_params.clear()
        return False

    try:
        # Exchange code for tokens
        tokens = auth_handler.exchange_code_for_tokens(code=code, code_verifier=code_verifier)

        # Store tokens
        session_manager.store_tokens(tokens)

        # Get user info
        user_info = auth_handler.get_user_info(tokens["access_token"])
        session_manager.store_user_info(user_info)

        # Clean up
        remove_pkce_state(state)
        st.query_params.clear()

        st.success("Authentication successful!")
        return True

    except Exception as e:
        st.error(f"Failed to complete authentication: {str(e)}")
        st.query_params.clear()
        return False


def main():
    """Main application entry point."""
    # Validate configuration
    config_errors = settings.validate()
    if config_errors:
        st.error("Configuration errors detected:")
        for error in config_errors:
            st.error(f"- {error}")
        st.stop()

    # Initialize handlers
    auth_handler = Auth0Handler()
    agentcore_client = AgentCoreClient()
    session_manager = SessionManager()

    # Handle OAuth callback if auth code is in URL
    if "code" in st.query_params:
        # Check if already authenticated (callback server may have already exchanged the code)
        if session_manager.is_authenticated():
            # Already authenticated, just clear the URL params
            st.query_params.clear()
            st.rerun()
        elif handle_oauth_callback(auth_handler, session_manager):
            st.rerun()
        else:
            # Show login page on failure
            render_login_page(auth_handler, session_manager)
            return

    # Check if user is authenticated
    if session_manager.is_authenticated():
        # Render authenticated UI
        render_authenticated_app(auth_handler, agentcore_client, session_manager)
    else:
        # Check if token expired
        if session_manager.get_tokens() is not None and session_manager.is_token_expired():
            render_token_expired_warning(session_manager)
        else:
            # Render login page
            render_login_page(auth_handler, session_manager)


def render_authenticated_app(
    auth_handler: Auth0Handler,
    agentcore_client: AgentCoreClient,
    session_manager: SessionManager,
):
    """
    Render the authenticated application interface.

    Args:
        auth_handler: Auth0Handler instance
        agentcore_client: AgentCoreClient instance
        session_manager: SessionManager instance
    """
    # Render sidebar
    render_sidebar(auth_handler, session_manager)

    # Show token refresh warning if needed
    render_token_refresh_warning(session_manager)

    # Main content area
    st.title("AgentCore Financial Assistant")

    # Welcome message
    user_name = session_manager.get_user_name()
    st.markdown(f"### Welcome back, {user_name}!")

    # Educational banner
    with st.expander("About This Sample Application", expanded=False):
        st.markdown("""
        This is an **educational sample application** demonstrating secure authentication
        and authorization patterns for AI agents using Auth0 3LO. Use the tabs below to explore:

        - **Chat**: Interact with the AI financial assistant
        - **JWT Tokens**: Deep dive into JWT structure and claims
        - **API Calls**: See the sequence of API calls and their details
        - **App State**: Understand the authentication state machine
        """)

    # Tabs for different views
    tab1, tab2, tab3, tab4 = st.tabs(["Chat", "JWT Tokens", "API Calls", "App State"])

    with tab1:
        # Render chat interface
        render_chat_interface(agentcore_client, session_manager)

    with tab2:
        # JWT Token Deep Dive tab
        tokens = session_manager.get_tokens()
        if tokens:
            render_jwt_viewer(
                access_token=tokens.access_token,
                id_token=tokens.id_token,
                claims_namespace=settings.auth0.claims_namespace,
            )

            st.markdown("---")
            st.markdown("### Token Lifecycle")
            render_token_timeline(
                {
                    "issued_at": tokens.expires_at - 86400,  # Assuming 24h lifetime
                    "expires_at": tokens.expires_at,
                }
            )
        else:
            st.warning("No tokens available. Please log in to see JWT details.")

    with tab3:
        # API Call Log tab
        render_api_call_log()

        st.markdown("---")
        render_call_sequence_diagram()

        st.markdown("---")
        render_call_statistics()

    with tab4:
        # Application State tab
        render_state_viewer(session_manager)

        st.markdown("---")
        render_oauth_config_details()


if __name__ == "__main__":
    main()
