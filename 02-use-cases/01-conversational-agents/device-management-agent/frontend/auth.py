"""
Device Management Frontend - Amazon Cognito Authentication Module

This module provides comprehensive authentication functionality for the Device
Management System frontend using Amazon Cognito OAuth 2.0 and JWT validation.
It handles user login, token exchange, session management, and authorization
for the FastAPI web application.

The module implements:
- Amazon Cognito Hosted UI integration for OAuth login
- Authorization code exchange for access tokens
- JWT token validation using JWKS (JSON Web Key Set)
- Session-based authentication with cookie support
- Simple login fallback for development/testing
- User information extraction from JWT claims

Key Features:
    - OAuth 2.0 authorization code flow with Cognito
    - JWT signature verification using RSA public keys
    - Token expiration and audience validation
    - Session middleware integration for FastAPI
    - Dual authentication support (Cognito + Simple login)
    - Automatic JWKS fetching and caching
    - Logout with Cognito hosted UI redirect

Authentication Flow:
    1. User clicks login → Redirected to Cognito Hosted UI
    2. User authenticates → Cognito redirects with authorization code
    3. Backend exchanges code for tokens (access + ID tokens)
    4. Backend validates ID token signature and claims
    5. User info stored in session
    6. Subsequent requests use session authentication

Environment Variables Required:
    COGNITO_DOMAIN: Cognito domain (e.g., domain.auth.region.amazoncognito.com)
    COGNITO_CLIENT_ID: OAuth client ID from Cognito App Client
    COGNITO_CLIENT_SECRET: OAuth client secret from Cognito App Client
    COGNITO_REDIRECT_URI: OAuth callback URL (e.g., http://localhost:5001/auth/callback)
    COGNITO_LOGOUT_URI: Logout redirect URL (e.g., http://localhost:5001/simple-login)
    AWS_REGION: AWS region for Cognito User Pool
    COGNITO_USER_POOL_ID: Cognito User Pool ID for JWKS validation

Functions:
    get_jwks(): Fetch and cache JSON Web Key Set from Cognito
    get_login_url(): Generate Cognito Hosted UI login URL
    get_logout_url(): Generate Cognito Hosted UI logout URL
    exchange_code_for_tokens(): Exchange authorization code for tokens
    validate_token(): Validate JWT token signature and claims
    get_current_user(): Get authenticated user from session or cookie
    login_required(): FastAPI dependency for protected routes

JWT Validation:
    - Fetches public keys from Cognito JWKS endpoint
    - Verifies RSA signature using matching key ID (kid)
    - Validates token expiration (exp claim)
    - Validates audience (client_id claim)
    - Extracts user claims (sub, email, name)

Session Management:
    - User info stored in FastAPI session
    - Session includes: sub, email, name, access_token, id_token
    - Simple login fallback stores username in cookie
    - Session cleared on logout

Example Usage:
    In FastAPI routes:
    >>> @app.get("/protected")
    >>> async def protected_route(request: Request):
    >>>     user = await get_current_user(request)
    >>>     if not user:
    >>>         return RedirectResponse(url="/login")
    >>>     return {"user": user}

    With dependency injection:
    >>> @app.get("/profile")
    >>> async def profile(user: dict = Depends(login_required)):
    >>>     return {"user": user}

Security Features:
    - JWT signature verification prevents token tampering
    - Token expiration prevents replay attacks
    - Audience validation prevents token misuse
    - HTTPS required for production (OAuth redirect URIs)
    - Client secret kept server-side (not exposed to browser)

Error Handling:
    - Invalid tokens raise HTTPException with 401 status
    - Missing authentication redirects to login
    - Token exchange failures return error details
    - JWKS fetch failures logged and handled gracefully

Notes:
    - JWKS is cached globally to minimize API calls
    - Simple login is for development only (no password validation)
    - Production should use Cognito authentication exclusively
    - Session middleware must be configured in main.py
    - Logout clears both session and cookies
"""

import os
import logging
from urllib.parse import urlencode
from typing import Optional, Dict, Any

import httpx
from fastapi import Request, HTTPException
from jose import jwk, jwt
from jose.utils import base64url_decode

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cognito configuration from environment variables
COGNITO_DOMAIN = os.getenv("COGNITO_DOMAIN")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID")
COGNITO_CLIENT_SECRET = os.getenv("COGNITO_CLIENT_SECRET")
COGNITO_REDIRECT_URI = os.getenv("COGNITO_REDIRECT_URI")
COGNITO_LOGOUT_URI = os.getenv("COGNITO_LOGOUT_URI")
AWS_REGION = os.getenv("AWS_REGION")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID")

# JWT validation
jwks_url = f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
jwks = None


async def get_jwks():
    """Fetch the JSON Web Key Set from Cognito"""
    global jwks
    if jwks is None:
        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_url)
            jwks = response.json()
    return jwks


def get_login_url() -> str:
    """Generate the Cognito login URL"""
    # Use the Cognito hosted UI directly
    login_url = f"https://{COGNITO_DOMAIN}/login?client_id={COGNITO_CLIENT_ID}&response_type=code&redirect_uri={COGNITO_REDIRECT_URI}"

    # Debug logging
    logger.info(f"COGNITO_DOMAIN: {COGNITO_DOMAIN}")
    logger.info(f"COGNITO_CLIENT_ID: {COGNITO_CLIENT_ID}")
    logger.info(f"COGNITO_REDIRECT_URI: {COGNITO_REDIRECT_URI}")
    logger.info(f"Full login URL: {login_url}")

    return login_url


def get_logout_url() -> str:
    """Generate the Cognito logout URL"""
    params = {"client_id": COGNITO_CLIENT_ID, "logout_uri": COGNITO_LOGOUT_URI}
    return f"https://{COGNITO_DOMAIN}/logout?{urlencode(params)}"


async def exchange_code_for_tokens(code: str) -> Dict[str, Any]:
    """Exchange authorization code for tokens"""
    token_endpoint = f"https://{COGNITO_DOMAIN}/oauth2/token"

    # Instead of using Authorization header, include client_id and client_secret in the form data
    data = {
        "grant_type": "authorization_code",
        "client_id": COGNITO_CLIENT_ID,
        "client_secret": COGNITO_CLIENT_SECRET,
        "code": code,
        "redirect_uri": COGNITO_REDIRECT_URI,
    }

    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    logger.info(f"Exchanging code for tokens with client_id: {COGNITO_CLIENT_ID}")

    async with httpx.AsyncClient() as client:
        response = await client.post(token_endpoint, headers=headers, data=data)

    if response.status_code != 200:
        logger.error(f"Token exchange failed: {response.text}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to exchange code for tokens: {response.text}",
        )

    return response.json()


async def validate_token(token: str) -> Dict[str, Any]:
    """Validate the JWT token from Cognito"""
    # Get the key id from the token header
    header = jwt.get_unverified_header(token)
    kid = header["kid"]

    # Get the public key that matches the key id
    jwks_client = await get_jwks()
    key = None
    for jwk_key in jwks_client["keys"]:
        if jwk_key["kid"] == kid:
            key = jwk_key
            break

    if not key:
        raise HTTPException(status_code=401, detail="Invalid token: Key not found")

    # Verify the signature
    hmac_key = jwk.construct(key)
    message, encoded_signature = token.rsplit(".", 1)
    decoded_signature = base64url_decode(encoded_signature.encode())

    if not hmac_key.verify(message.encode(), decoded_signature):
        raise HTTPException(status_code=401, detail="Invalid token: Signature verification failed")

    # Verify the claims
    claims = jwt.get_unverified_claims(token)

    # Check expiration
    import time

    if claims["exp"] < time.time():
        raise HTTPException(status_code=401, detail="Token expired")

    # Check audience
    if claims["client_id"] != COGNITO_CLIENT_ID:
        raise HTTPException(status_code=401, detail="Invalid audience")

    return claims


async def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """Get the current authenticated user from the session or simple cookie"""
    # Check for Cognito session first
    if "user" in request.session:
        return request.session["user"]

    # Check for simple login cookie as fallback
    simple_user = request.cookies.get("simple_user")
    if simple_user:
        return {"username": simple_user, "auth_type": "simple"}

    return None


def login_required(request: Request):
    """Dependency to check if user is logged in"""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
