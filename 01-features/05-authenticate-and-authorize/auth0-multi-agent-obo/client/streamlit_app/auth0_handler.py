# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Auth0 OAuth handler for managing authentication flow.
Implements PKCE (Proof Key for Code Exchange) for secure OAuth.
"""

import base64
import hashlib
import logging
import secrets
import time
from collections import deque
from threading import Lock
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode

import requests

from shared.config.settings import settings

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded for token exchange operations."""

    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after:.1f} seconds")


class RateLimiter:
    """Sliding window rate limiter for token exchange operations."""

    def __init__(self, max_calls: int = 10, window_seconds: int = 60):
        """
        Initialize rate limiter.

        Args:
            max_calls: Maximum number of calls allowed in the window
            window_seconds: Time window in seconds
        """
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._calls = deque()
        self._lock = Lock()

    def is_allowed(self) -> bool:
        """Check if a call is allowed and record it if so."""
        now = time.time()
        with self._lock:
            # Remove expired entries
            cutoff = now - self.window_seconds
            while self._calls and self._calls[0] < cutoff:
                self._calls.popleft()
            # Check if under limit
            if len(self._calls) < self.max_calls:
                self._calls.append(now)
                return True
            return False

    def get_retry_after(self) -> float:
        """Get seconds until the oldest call expires."""
        if not self._calls:
            return 0
        return max(0, self.window_seconds - (time.time() - self._calls[0]))


class Auth0Handler:
    """Handles Auth0 OAuth 2.0 authentication with PKCE."""

    def __init__(self):
        """Initialize Auth0 handler with configuration."""
        self.config = settings.auth0
        self.domain = self.config.domain
        self.client_id = self.config.client_id
        self.client_secret = self.config.client_secret
        self.audience = self.config.audience
        self.callback_url = self.config.callback_url
        self.scopes = self.config.scopes

        # Rate limiter for token exchange operations (10 calls per 60 seconds)
        self._token_exchange_limiter = RateLimiter(max_calls=10, window_seconds=60)

    def _generate_pkce_pair(self) -> Tuple[str, str]:
        """
        Generate PKCE code verifier and challenge.

        Returns:
            Tuple of (code_verifier, code_challenge)
        """
        # Generate random code verifier (43-128 characters)
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8")
        code_verifier = code_verifier.rstrip("=")

        # Create code challenge using SHA256
        code_challenge = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        code_challenge = base64.urlsafe_b64encode(code_challenge).decode("utf-8")
        code_challenge = code_challenge.rstrip("=")

        return code_verifier, code_challenge

    def generate_auth_url(self, state: Optional[str] = None) -> Tuple[str, str, str]:
        """
        Generate Auth0 authorization URL with PKCE.

        Args:
            state: Optional state parameter for CSRF protection

        Returns:
            Tuple of (auth_url, state, code_verifier)
        """
        # Generate PKCE pair
        code_verifier, code_challenge = self._generate_pkce_pair()

        # Generate state if not provided
        if state is None:
            state = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8")

        # Build authorization URL
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.callback_url,
            "scope": self.scopes,
            "audience": self.audience,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        auth_url = f"{self.config.authorization_url}?{urlencode(params)}"
        return auth_url, state, code_verifier

    def exchange_code_for_tokens(self, code: str, code_verifier: str) -> Dict[str, any]:
        """
        Exchange authorization code for access and ID tokens.

        Args:
            code: Authorization code from callback
            code_verifier: PKCE code verifier

        Returns:
            Dictionary containing tokens and expiration info

        Raises:
            requests.HTTPError: If token exchange fails
            RateLimitExceeded: If rate limit is exceeded
        """
        # Check rate limit before proceeding
        if not self._token_exchange_limiter.is_allowed():
            raise RateLimitExceeded(self._token_exchange_limiter.get_retry_after())

        # Regular Web Application (confidential client) - requires client_secret
        token_data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": self.callback_url,
        }

        # Auth0 token endpoint expects form-urlencoded data
        response = requests.post(
            self.config.token_url,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )

        response.raise_for_status()
        token_response = response.json()

        # Calculate expiration timestamp
        expires_in = token_response.get("expires_in", 3600)
        expires_at = time.time() + expires_in

        return {
            "access_token": token_response["access_token"],
            "id_token": token_response.get("id_token"),
            "refresh_token": token_response.get("refresh_token"),
            "token_type": token_response.get("token_type", "Bearer"),
            "expires_in": expires_in,
            "expires_at": expires_at,
            "scope": token_response.get("scope", self.scopes),
        }

    def refresh_tokens(self, refresh_token: str) -> Dict[str, any]:
        """
        Refresh access token using refresh token.

        Args:
            refresh_token: Valid refresh token

        Returns:
            Dictionary containing new tokens and expiration info

        Raises:
            requests.HTTPError: If token refresh fails
        """
        token_data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
        }

        # Auth0 token endpoint expects form-urlencoded data
        response = requests.post(
            self.config.token_url,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )

        response.raise_for_status()
        token_response = response.json()

        # Calculate expiration timestamp
        expires_in = token_response.get("expires_in", 3600)
        expires_at = time.time() + expires_in

        return {
            "access_token": token_response["access_token"],
            "id_token": token_response.get("id_token"),
            "refresh_token": token_response.get("refresh_token", refresh_token),
            "token_type": token_response.get("token_type", "Bearer"),
            "expires_in": expires_in,
            "expires_at": expires_at,
            "scope": token_response.get("scope", self.scopes),
        }

    def get_user_info(self, access_token: str) -> Dict[str, any]:
        """
        Fetch user information from Auth0 userinfo endpoint.

        Args:
            access_token: Valid access token

        Returns:
            Dictionary containing user profile information

        Raises:
            requests.HTTPError: If userinfo request fails
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
        }

        response = requests.get(self.config.userinfo_url, headers=headers, timeout=10)

        response.raise_for_status()
        return response.json()

    def logout(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        return_to: Optional[str] = None,
    ) -> str:
        """
        Revoke tokens and generate Auth0 logout URL.

        Implements RFC 7009 token revocation for refresh tokens before
        redirecting to the Auth0 logout endpoint.

        Args:
            access_token: Optional access token (not revoked, for future use)
            refresh_token: Optional refresh token to revoke
            return_to: URL to redirect to after logout

        Returns:
            Logout URL
        """
        # Revoke refresh token if provided (RFC 7009)
        if refresh_token:
            try:
                response = requests.post(
                    f"https://{self.domain}/oauth/revoke",
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "token": refresh_token,
                        "token_type_hint": "refresh_token",
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=5,
                )
                if response.status_code == 200:
                    logger.info("Refresh token revoked successfully")
                else:
                    logger.warning(f"Token revocation returned {response.status_code}")
            except requests.RequestException as e:
                # Log but don't fail - still generate logout URL
                logger.warning(f"Failed to revoke refresh token: {e}")

        # Generate logout URL
        if return_to is None:
            return_to = f"http://{settings.app.oauth_callback_host}:{settings.app.streamlit_port}"

        params = {
            "client_id": self.client_id,
            "returnTo": return_to,
        }

        logout_url = f"https://{self.domain}/v2/logout?{urlencode(params)}"
        return logout_url

    def decode_id_token(self, id_token: str) -> Dict[str, any]:
        """
        Decode ID token (JWT) without validation.
        For production, use proper JWT validation with jwcrypto or PyJWT.

        Args:
            id_token: JWT ID token

        Returns:
            Dictionary containing decoded claims

        Note:
            This is a basic decoder for demonstration. Production apps should
            validate the signature, issuer, audience, and expiration.
        """
        try:
            # Split JWT into parts
            parts = id_token.split(".")
            if len(parts) != 3:
                raise ValueError("Invalid JWT format")

            # Decode payload (add padding if needed)
            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += "=" * padding

            decoded = base64.urlsafe_b64decode(payload)

            import json

            return json.loads(decoded)
        except Exception as e:
            raise ValueError(f"Failed to decode ID token: {e}")
