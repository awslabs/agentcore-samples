# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Session manager for handling authentication state and conversation history.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime

import streamlit as st


@dataclass
class TokenInfo:
    """Container for OAuth token information."""

    access_token: str
    id_token: Optional[str]
    refresh_token: Optional[str]
    token_type: str
    expires_at: float
    scope: str

    def is_expired(self, buffer_seconds: int = 60) -> bool:
        """
        Check if access token is expired.

        Args:
            buffer_seconds: Refresh buffer time before actual expiration

        Returns:
            True if token is expired or will expire soon
        """
        return time.time() >= (self.expires_at - buffer_seconds)

    def time_until_expiry(self) -> float:
        """
        Get seconds until token expires.

        Returns:
            Seconds until expiration (negative if already expired)
        """
        return self.expires_at - time.time()


@dataclass
class Message:
    """Container for chat messages."""

    role: str  # 'user' or 'assistant'
    content: str
    timestamp: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @property
    def formatted_timestamp(self) -> str:
        """Get formatted timestamp string."""
        dt = datetime.fromtimestamp(self.timestamp)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


class SessionManager:
    """Manages Streamlit session state for authentication and conversation."""

    # Session state keys
    KEY_TOKENS = "auth_tokens"
    KEY_USER_INFO = "user_info"
    KEY_AUTHENTICATED = "authenticated"
    KEY_PKCE_VERIFIER = "pkce_verifier"
    KEY_PKCE_STATE = "pkce_state"
    KEY_SESSION_ID = "session_id"
    KEY_MESSAGES = "messages"
    KEY_CALLBACK_SERVER = "callback_server"

    def __init__(self):
        """Initialize session manager."""
        self._init_session_state()

    def _init_session_state(self):
        """Initialize all session state variables."""
        if self.KEY_AUTHENTICATED not in st.session_state:
            st.session_state[self.KEY_AUTHENTICATED] = False

        if self.KEY_TOKENS not in st.session_state:
            st.session_state[self.KEY_TOKENS] = None

        if self.KEY_USER_INFO not in st.session_state:
            st.session_state[self.KEY_USER_INFO] = None

        if self.KEY_PKCE_VERIFIER not in st.session_state:
            st.session_state[self.KEY_PKCE_VERIFIER] = None

        if self.KEY_PKCE_STATE not in st.session_state:
            st.session_state[self.KEY_PKCE_STATE] = None

        if self.KEY_SESSION_ID not in st.session_state:
            st.session_state[self.KEY_SESSION_ID] = self._generate_session_id()

        if self.KEY_MESSAGES not in st.session_state:
            st.session_state[self.KEY_MESSAGES] = []

        if self.KEY_CALLBACK_SERVER not in st.session_state:
            st.session_state[self.KEY_CALLBACK_SERVER] = None

    def _generate_session_id(self) -> str:
        """Generate unique session ID for AgentCore."""
        import uuid

        return str(uuid.uuid4())

    # Token management

    def store_tokens(self, token_data: Dict[str, Any]):
        """
        Store authentication tokens in session.

        Args:
            token_data: Token dictionary from Auth0
        """
        token_info = TokenInfo(
            access_token=token_data["access_token"],
            id_token=token_data.get("id_token"),
            refresh_token=token_data.get("refresh_token"),
            token_type=token_data.get("token_type", "Bearer"),
            expires_at=token_data["expires_at"],
            scope=token_data.get("scope", ""),
        )
        st.session_state[self.KEY_TOKENS] = token_info
        st.session_state[self.KEY_AUTHENTICATED] = True

    def get_tokens(self) -> Optional[TokenInfo]:
        """
        Get current tokens from session.

        Returns:
            TokenInfo object or None if not authenticated
        """
        return st.session_state.get(self.KEY_TOKENS)

    def get_access_token(self) -> Optional[str]:
        """
        Get current access token.

        Returns:
            Access token string or None
        """
        tokens = self.get_tokens()
        return tokens.access_token if tokens else None

    def is_token_expired(self) -> bool:
        """
        Check if current access token is expired.

        Returns:
            True if expired or no token exists
        """
        tokens = self.get_tokens()
        if not tokens:
            return True
        return tokens.is_expired()

    def clear_tokens(self):
        """Clear authentication tokens from session."""
        st.session_state[self.KEY_TOKENS] = None
        st.session_state[self.KEY_AUTHENTICATED] = False
        st.session_state[self.KEY_USER_INFO] = None

    # User info management

    def store_user_info(self, user_info: Dict[str, Any]):
        """
        Store user profile information.

        Args:
            user_info: User info dictionary from Auth0
        """
        st.session_state[self.KEY_USER_INFO] = user_info

    def get_user_info(self) -> Optional[Dict[str, Any]]:
        """
        Get user profile information.

        Returns:
            User info dictionary or None
        """
        return st.session_state.get(self.KEY_USER_INFO)

    def get_user_name(self) -> str:
        """
        Get user's display name.

        Returns:
            User name or 'User' if not available
        """
        user_info = self.get_user_info()
        if not user_info:
            return "User"

        return user_info.get("name") or user_info.get("nickname") or user_info.get("email") or "User"

    def get_user_email(self) -> Optional[str]:
        """
        Get user's email address.

        Returns:
            Email address or None
        """
        user_info = self.get_user_info()
        return user_info.get("email") if user_info else None

    # Authentication state

    def is_authenticated(self) -> bool:
        """
        Check if user is authenticated.

        Returns:
            True if authenticated with valid token
        """
        return st.session_state.get(self.KEY_AUTHENTICATED, False) and not self.is_token_expired()

    def set_authenticated(self, authenticated: bool):
        """
        Set authentication state.

        Args:
            authenticated: Authentication state
        """
        st.session_state[self.KEY_AUTHENTICATED] = authenticated

    # PKCE state management

    def store_pkce_state(self, verifier: str, state: str):
        """
        Store PKCE parameters during OAuth flow.

        Args:
            verifier: PKCE code verifier
            state: OAuth state parameter
        """
        st.session_state[self.KEY_PKCE_VERIFIER] = verifier
        st.session_state[self.KEY_PKCE_STATE] = state

    def get_pkce_verifier(self) -> Optional[str]:
        """Get PKCE code verifier."""
        return st.session_state.get(self.KEY_PKCE_VERIFIER)

    def get_pkce_state(self) -> Optional[str]:
        """Get OAuth state parameter."""
        return st.session_state.get(self.KEY_PKCE_STATE)

    def clear_pkce_state(self):
        """Clear PKCE parameters after OAuth flow."""
        st.session_state[self.KEY_PKCE_VERIFIER] = None
        st.session_state[self.KEY_PKCE_STATE] = None

    # Session ID management

    def get_session_id(self) -> str:
        """
        Get AgentCore session ID.

        Returns:
            Session ID string
        """
        return st.session_state.get(self.KEY_SESSION_ID, self._generate_session_id())

    def reset_session_id(self):
        """Generate new session ID."""
        st.session_state[self.KEY_SESSION_ID] = self._generate_session_id()

    # Message history management

    def add_message(self, role: str, content: str):
        """
        Add message to conversation history.

        Args:
            role: Message role ('user' or 'assistant')
            content: Message content
        """
        message = Message(role=role, content=content, timestamp=time.time())
        st.session_state[self.KEY_MESSAGES].append(message)

    def get_messages(self) -> List[Message]:
        """
        Get conversation history.

        Returns:
            List of Message objects
        """
        return st.session_state.get(self.KEY_MESSAGES, [])

    def clear_messages(self):
        """Clear conversation history."""
        st.session_state[self.KEY_MESSAGES] = []

    def get_last_message(self) -> Optional[Message]:
        """
        Get last message in conversation.

        Returns:
            Last Message object or None
        """
        messages = self.get_messages()
        return messages[-1] if messages else None

    # Callback server management

    def store_callback_server(self, server: Any):
        """
        Store callback server instance.

        Args:
            server: OAuth2CallbackServer instance
        """
        st.session_state[self.KEY_CALLBACK_SERVER] = server

    def get_callback_server(self) -> Optional[Any]:
        """Get callback server instance."""
        return st.session_state.get(self.KEY_CALLBACK_SERVER)

    # Full session management

    def logout(self):
        """Clear all session data."""
        self.clear_tokens()
        self.clear_messages()
        self.reset_session_id()
        self.clear_pkce_state()
        st.session_state[self.KEY_CALLBACK_SERVER] = None

    def get_session_info(self) -> Dict[str, Any]:
        """
        Get session information for debugging.

        Returns:
            Dictionary with session details
        """
        tokens = self.get_tokens()
        return {
            "authenticated": self.is_authenticated(),
            "has_tokens": tokens is not None,
            "token_expired": self.is_token_expired() if tokens else None,
            "time_until_expiry": tokens.time_until_expiry() if tokens else None,
            "user_name": self.get_user_name(),
            "user_email": self.get_user_email(),
            "session_id": self.get_session_id(),
            "message_count": len(self.get_messages()),
        }

    # State tracking for educational UI

    def get_auth_state(self) -> str:
        """
        Get current authentication state name for state machine visualization.

        Returns:
            State name string
        """
        tokens = self.get_tokens()

        if tokens and not self.is_token_expired():
            return "AUTHENTICATED"
        elif tokens and self.is_token_expired():
            return "TOKEN_EXPIRED"
        elif st.session_state.get(self.KEY_PKCE_STATE):
            return "PKCE_INITIATED"
        else:
            return "UNAUTHENTICATED"

    def record_state_transition(self, trigger: str, details: Optional[Dict] = None):
        """
        Record a state transition for educational tracking.

        Args:
            trigger: What caused the transition
            details: Optional additional details
        """
        try:
            from components.state_viewer import record_state_transition

            # Get previous state
            previous_state = st.session_state.get("_previous_auth_state", "UNKNOWN")
            current_state = self.get_auth_state()

            if previous_state != current_state:
                record_state_transition(
                    from_state=previous_state,
                    to_state=current_state,
                    trigger=trigger,
                    details=details,
                )

            st.session_state["_previous_auth_state"] = current_state
        except ImportError:
            pass  # State viewer not available

    def get_token_info_for_display(self) -> Optional[Dict[str, Any]]:
        """
        Get token information formatted for display.

        Returns:
            Dictionary with display-safe token information
        """
        tokens = self.get_tokens()
        if not tokens:
            return None

        return {
            "token_type": tokens.token_type,
            "scope": tokens.scope,
            "expires_at": tokens.expires_at,
            "time_until_expiry": tokens.time_until_expiry(),
            "is_expired": tokens.is_expired(),
            "has_refresh_token": tokens.refresh_token is not None,
            "access_token_preview": f"{tokens.access_token[:20]}..." if tokens.access_token else None,
            "id_token_preview": f"{tokens.id_token[:20]}..." if tokens.id_token else None,
        }
