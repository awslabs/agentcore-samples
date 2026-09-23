# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Login component for Auth0 authentication.
"""

import webbrowser

import streamlit as st

from auth0_handler import Auth0Handler
from oauth2_callback import OAuth2CallbackServer
from session_manager import SessionManager
from shared.config.settings import settings
from pkce_store import store_pkce_state


def render_login_page(auth_handler: Auth0Handler, session_manager: SessionManager):
    """
    Render the login page with Auth0 authentication.

    Args:
        auth_handler: Auth0Handler instance
        session_manager: SessionManager instance
    """
    # Page styling
    st.markdown(
        """
        <style>
        .login-container {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 50px 20px;
        }
        .login-header {
            text-align: center;
            margin-bottom: 40px;
        }
        .login-title {
            font-size: 3rem;
            font-weight: 700;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
        }
        .login-subtitle {
            font-size: 1.2rem;
            color: #666;
            margin-bottom: 20px;
        }
        .feature-list {
            text-align: left;
            margin: 30px 0;
            padding: 0 20px;
        }
        .feature-item {
            display: flex;
            align-items: center;
            margin: 15px 0;
            font-size: 1.1rem;
        }
        .feature-icon {
            color: #667eea;
            margin-right: 10px;
            font-size: 1.5rem;
        }
        </style>
    """,
        unsafe_allow_html=True,
    )

    # Main login container
    st.markdown('<div class="login-container">', unsafe_allow_html=True)

    # Header
    st.markdown(
        """
        <div class="login-header">
            <h1 class="login-title">AgentCore Financial Assistant</h1>
            <p class="login-subtitle">Your intelligent banking companion powered by AI</p>
        </div>
    """,
        unsafe_allow_html=True,
    )

    # Features
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("### Account Management")
        st.markdown("""
        - View account balances
        - Check transaction history
        - Monitor spending patterns
        """)

    with col2:
        st.markdown("### Profile Services")
        st.markdown("""
        - Update personal information
        - Manage contact details
        - View customer profile
        """)

    with col3:
        st.markdown("### Card Operations")
        st.markdown("""
        - Check card details
        - View card limits
        - Manage card settings
        """)

    st.markdown("---")

    # Login button and status
    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        st.markdown("### Sign in to get started")

        if st.button("Sign in with Auth0", type="primary", use_container_width=True):
            with st.spinner("Starting authentication..."):
                try:
                    # Start callback server if not already running
                    callback_server = session_manager.get_callback_server()
                    if callback_server is None:
                        callback_server = OAuth2CallbackServer(
                            host=settings.app.oauth_callback_host,
                            port=settings.app.oauth_callback_port,
                        )
                        callback_server.start()
                        session_manager.store_callback_server(callback_server)

                    # Reset callback state
                    callback_server.reset()

                    # Generate authorization URL with PKCE
                    auth_url, state, code_verifier = auth_handler.generate_auth_url()

                    # Store PKCE parameters (in file-based store for cross-session access)
                    store_pkce_state(state, code_verifier)
                    session_manager.store_pkce_state(code_verifier, state)

                    # Open browser for authentication
                    st.info("Opening browser for authentication. Please sign in with Auth0.")
                    webbrowser.open(auth_url)

                    # Wait for callback
                    st.info("Waiting for authentication callback...")
                    callback_received = callback_server.wait_for_callback(timeout=300)

                    if callback_received:
                        # Get callback data
                        callback_data = callback_server.get_callback_data()

                        if callback_data["error"]:
                            st.error(f"Authentication failed: {callback_data['error']}")
                            st.error(f"Description: {callback_data['error_description']}")
                        else:
                            # The browser will be redirected to Streamlit with the auth code
                            # Token exchange will happen via URL parameters in app.py
                            st.info("Authentication received. Waiting for redirect...")
                    else:
                        st.error("Authentication timeout. Please try again.")

                except Exception as e:
                    st.error(f"Authentication error: {str(e)}")

        st.markdown("---")

        # Information
        with st.expander("About this application"):
            st.markdown("""
            This application demonstrates:
            - **3-legged OAuth 2.0** with Auth0 and PKCE
            - **JWT-based authentication** with AgentCore Runtime
            - **Secure identity propagation** through the agent hierarchy
            - **Multi-agent orchestration** for financial services

            The application uses Auth0 for identity management and passes
            JWT tokens to AgentCore, where they are validated and used to
            enforce fine-grained authorization on agent tools.
            """)

        with st.expander("Sample interactions"):
            st.markdown("""
            Try these example queries after signing in:

            **Profile Management:**
            - "Show me my customer profile"
            - "What's my current address?"
            - "Update my email to john.doe@example.com"
            - "Change my phone number to +61 400 123 456"

            **Account Information:**
            - "What accounts do I have?"
            - "Show my account balances"
            - "What's my savings account balance?"

            **Transaction History:**
            - "Show my recent transactions"
            - "What did I spend on groceries last month?"
            - "Show transactions over $100"

            **Card Services:**
            - "What cards do I have?"
            - "Show my credit card details"
            - "What's my card limit?"
            """)

    st.markdown("</div>", unsafe_allow_html=True)


def render_token_expired_warning(session_manager: SessionManager):
    """
    Render warning when token is expired.

    Args:
        session_manager: SessionManager instance
    """
    st.warning("Your session has expired. Please sign in again.")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if st.button("Sign in again", type="primary", use_container_width=True):
            session_manager.logout()
            st.rerun()
