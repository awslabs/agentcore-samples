#!/usr/bin/env python3.11
"""
Direct SecureToken API - Bypass Visa's iframe completely!

This calls the discovered API endpoint directly to get the secureToken
without loading any iframe.
"""

import requests
import json
import hashlib
import base64
import uuid
import secrets
from urllib.parse import urlencode
import logging

logger = logging.getLogger(__name__)


def generate_proof_challenge():
    """Generate PKCE challenge for OAuth2"""
    # Generate verifier (43-128 characters)
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8").rstrip("=")

    # Generate challenge (SHA256 of verifier)
    challenge_bytes = hashlib.sha256(
        verifier.encode("utf-8")
    ).digest()  # CodeQL[py/weak-cryptographic-algorithm] SHA256 is mandated by Visa API specification
    challenge = base64.urlsafe_b64encode(challenge_bytes).decode("utf-8").rstrip("=")

    return verifier, challenge


def generate_device_fingerprint():
    """Generate a unique device fingerprint ID"""
    return base64.b64encode(uuid.uuid4().bytes).decode("utf-8").rstrip("=")


def create_jwt_assertion(device_id: str):
    """
    Create the JWT assertion for Visa OAuth

    This is an anonymous credential JWT that Visa expects
    """
    import time

    header = {"alg": "none", "typ": "vnd.visa.anonymous_credential+JWT"}

    payload = {
        "iss_knd": "DEVICE_ID",
        "iss": device_id,
        "aud": ["https://www.visa.com"],
        "iat": int(time.time()),
        "exp": int(time.time()) + 30,  # 30 seconds expiry
        "jti": str(uuid.uuid4()),
    }

    # Create unsigned JWT (Visa accepts this)
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

    return f"{header_b64}.{payload_b64}."


def get_secure_token_direct(api_key: str, client_app_id: str):
    """
    Get secureToken by calling Visa's OAuth API directly

    This completely bypasses the iframe!

    Args:
        api_key: Your Visa API key
        client_app_id: Your Visa client app ID

    Returns:
        dict: Contains secureToken, requestID, and other session data
    """

    logger.info("=" * 70)
    logger.info("DIRECT SECURETOKEN API CALL - NO IFRAME")
    logger.info("=" * 70)

    # Step 1: Generate required parameters
    logger.info("Generating OAuth parameters...")
    verifier, challenge = generate_proof_challenge()
    device_fingerprint = generate_device_fingerprint()
    device_id = str(uuid.uuid4())

    # Step 2: Create JWT assertion
    logger.info("Creating JWT assertion...")
    assertion = create_jwt_assertion(device_id)

    # Step 3: Build request parameters
    logger.info("Building request parameters...")

    params = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "response_type": "urn:ext:oauth:response-type:server_state",
        "assertion": assertion,
        "proof_challenge": challenge,
        "proof_challenge_method": "S256",
        "software_statement_claimset.software_id": "VTSW",
        "software_statement_claimset.tenancy_context.product_code": "VTS",
        "software_statement_claimset.uebas.0.ueba_source": "VDI",
        "software_statement_claimset.uebas.0.ueba_ref": device_fingerprint,
        "software_statement_claimset.software_version": "24.09",
        "software_statement_claimset.oauth2_version": "1.0",
        "software_statement_claimset.oidc_version": "0.0",
        "software_statement_claimset.top_origin": "https://blahblah.com",
        "software_statement_claimset.declared_origin": "https://blahblah.com",
        "software_statement_claimset.integrator_origin": "https://sbx.vts.auth.visa.com",
    }

    # Step 4: Make the API call
    logger.info("Calling Visa OAuth API...")

    url = "https://sandbox.auth.visa.com/apn/vts-web/oauth2/token"

    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Origin": "https://sbx.vts.auth.visa.com",
        "Referer": "https://sbx.vts.auth.visa.com/",
        "x-correlation-id": str(uuid.uuid4()),
    }

    try:
        response = requests.post(url, headers=headers, data=urlencode(params), timeout=10)

        logger.info(f"Response received: {response.status_code}")

        if response.status_code in [200, 201]:
            response_data = response.json()

            # Extract the secureToken
            if "issued_tokens" in response_data and len(response_data["issued_tokens"]) > 0:
                secure_token = response_data["issued_tokens"][0]["token"]

                logger.info("=" * 70)
                logger.info("SUCCESS! SecureToken obtained WITHOUT iframe!")
                logger.info("=" * 70)

                result = {
                    "secureToken": secure_token,
                    "requestID": device_id,
                    "proof_verifier": verifier,
                    "device_fingerprint": device_fingerprint,
                    "full_response": response_data,
                }

                return result
            else:
                logger.error(f"Unexpected response format: {response_data}")
                return None
        else:
            logger.error(f"API call failed! Status: {response.status_code}")
            logger.error(f"Response: {response.text[:500]}")
            return None

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return None
