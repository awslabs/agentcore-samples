# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Chat interface component for AgentCore interaction.
"""

import streamlit as st
import streamlit.components.v1 as components

from agentcore_client import AgentCoreClient
from session_manager import SessionManager, Message
from agent_trace import generate_mock_trace


def _scroll_to_bottom():
    """Inject JavaScript to scroll the chat to the bottom."""
    components.html(
        """
        <script>
        const main = window.parent.document.querySelector('[data-testid="stAppViewBlockContainer"]')
            || window.parent.document.querySelector('.main');
        if (main) {
            main.scrollTo({ top: main.scrollHeight, behavior: 'smooth' });
        }
        // Also scroll the entire window as fallback
        window.parent.scrollTo({ top: window.parent.document.body.scrollHeight, behavior: 'smooth' });
        </script>
        """,
        height=0,
    )


def _auto_scroll_script():
    """Return a JavaScript snippet that auto-scrolls. For use inside streaming updates."""
    return """
    <script>
    const el = window.parent.document.querySelector('[data-testid="stAppViewBlockContainer"]')
        || window.parent.document.querySelector('.main');
    if (el) { el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }); }
    window.parent.scrollTo({ top: window.parent.document.body.scrollHeight, behavior: 'smooth' });
    </script>
    """


def render_chat_interface(agentcore_client: AgentCoreClient, session_manager: SessionManager):
    """
    Render the main chat interface.

    Args:
        agentcore_client: AgentCoreClient instance
        session_manager: SessionManager instance
    """
    st.markdown(
        """
        <style>
        .chat-container {
            padding: 20px 0;
        }
        .user-message {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px;
            border-radius: 15px;
            margin: 10px 0;
            max-width: 80%;
            margin-left: auto;
        }
        .assistant-message {
            background: #f7fafc;
            color: #2d3748;
            padding: 15px;
            border-radius: 15px;
            margin: 10px 0;
            max-width: 80%;
            border: 1px solid #e2e8f0;
        }
        .message-timestamp {
            font-size: 0.75rem;
            color: #a0aec0;
            margin-top: 5px;
        }
        </style>
    """,
        unsafe_allow_html=True,
    )

    # Chat header
    st.markdown("### Chat with Financial Assistant")

    # Display conversation history
    messages = session_manager.get_messages()

    if not messages:
        st.info("Welcome! I'm your AI financial assistant. Ask me anything about your profile or accounts.")
    else:
        # Display message history
        for message in messages:
            render_message(message)
        # Auto-scroll to latest message
        _scroll_to_bottom()

    # Chat input
    st.markdown("---")

    # Create input form
    with st.form(key="chat_form", clear_on_submit=True):
        col1, col2 = st.columns([6, 1])

        with col1:
            user_input = st.text_input(
                "Your message",
                placeholder="Type your message here...",
                label_visibility="collapsed",
            )

        with col2:
            submit_button = st.form_submit_button("Send", use_container_width=True)

        # Action buttons
        col1, col2 = st.columns(2)
        with col1:
            if st.form_submit_button("Clear history"):
                session_manager.clear_messages()
                st.rerun()
        with col2:
            if st.form_submit_button("New session"):
                session_manager.clear_messages()
                session_manager.reset_session_id()
                st.success("Started new session")
                st.rerun()

    # Handle message submission
    if submit_button and user_input:
        send_message(user_input, agentcore_client, session_manager, enable_trace=True)


def render_message(message: Message):
    """
    Render a single message.

    Args:
        message: Message object to render
    """
    if message.role == "user":
        st.markdown(
            f"""
            <div class="user-message">
                <strong>You</strong><br>
                {message.content}
                <div class="message-timestamp">{message.formatted_timestamp}</div>
            </div>
        """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""
            <div class="assistant-message">
                <strong>Assistant</strong><br>
                {message.content}
                <div class="message-timestamp">{message.formatted_timestamp}</div>
            </div>
        """,
            unsafe_allow_html=True,
        )


def send_message(
    message: str,
    agentcore_client: AgentCoreClient,
    session_manager: SessionManager,
    enable_trace: bool = False,
):
    """
    Send message to AgentCore and display response.

    Args:
        message: User message to send
        agentcore_client: AgentCoreClient instance
        session_manager: SessionManager instance
        enable_trace: Whether to enable trace output
    """
    # Add user message to history
    session_manager.add_message("user", message)

    # Get access token
    access_token = session_manager.get_access_token()
    if not access_token:
        st.error("No access token available. Please sign in again.")
        return

    # Get session ID
    session_id = session_manager.get_session_id()

    # Get user info for trace generation
    user_info = session_manager.get_user_info() or {}
    user_id = user_info.get("sub", "unknown")
    user_email = user_info.get("email", "unknown@example.com")
    # For demo, use CUST-001 as the customer ID
    customer_id = user_info.get("https://agentcore.example.com/customer_id", "CUST-001")

    # Display user message
    render_message(session_manager.get_last_message())

    # Create placeholder for assistant response
    with st.spinner("Thinking..."):
        response_placeholder = st.empty()
        response_text = ""
        trace = None

        try:
            # Stream response from AgentCore
            for chunk in agentcore_client.invoke_with_streaming(
                message=message,
                session_id=session_id,
                access_token=access_token,
                enable_trace=enable_trace,
            ):
                response_text += chunk
                # Update response in real-time with auto-scroll
                response_placeholder.markdown(
                    f"""
                    <div class="assistant-message">
                        <strong>Assistant</strong><br>
                        {response_text}
                    </div>
                    {_auto_scroll_script()}
                """,
                    unsafe_allow_html=True,
                )

            # Generate mock trace for demonstration
            trace = generate_mock_trace(
                query=message,
                session_id=session_id,
                user_id=user_id,
                customer_id=customer_id,
                user_email=user_email,
            )

            # Add complete response to history
            if response_text:
                session_manager.add_message("assistant", response_text)
            else:
                error_msg = "No response received from agent."
                session_manager.add_message("assistant", error_msg)
                st.error(error_msg)

        except PermissionError as e:
            error_msg = f"Authorization failed: {str(e)}"
            st.error(error_msg)
            session_manager.add_message("assistant", f"Error: {error_msg}")

        except ValueError as e:
            error_msg = f"Invalid request: {str(e)}"
            st.error(error_msg)
            session_manager.add_message("assistant", f"Error: {error_msg}")

        except RuntimeError as e:
            error_msg = f"Agent invocation failed: {str(e)}"
            st.error(error_msg)
            session_manager.add_message("assistant", f"Error: {error_msg}")

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            st.error(error_msg)
            session_manager.add_message("assistant", f"Error: {error_msg}")

        # Store the trace in session state
        if trace:
            if "agent_traces" not in st.session_state:
                st.session_state["agent_traces"] = []
            st.session_state["agent_traces"].append(trace)
            # Keep only last 10 traces
            st.session_state["agent_traces"] = st.session_state["agent_traces"][-10:]
            # Store the latest trace for quick access
            st.session_state["latest_trace"] = trace

    # Rerun to update the display
    st.rerun()


def render_session_info(session_manager: SessionManager):
    """
    Render session information for debugging.

    Args:
        session_manager: SessionManager instance
    """
    with st.expander("Session Information"):
        session_info = session_manager.get_session_info()

        col1, col2 = st.columns(2)

        with col1:
            st.metric("Session ID", session_info["session_id"][:8] + "...")
            st.metric("Messages", session_info["message_count"])
            st.metric("Authenticated", "Yes" if session_info["authenticated"] else "No")

        with col2:
            if session_info["time_until_expiry"]:
                minutes = int(session_info["time_until_expiry"] / 60)
                st.metric("Token expires in", f"{minutes} min")
            st.metric("User", session_info["user_name"])
            if session_info["user_email"]:
                st.text(f"Email: {session_info['user_email']}")
