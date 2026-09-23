#!/usr/bin/env python3.11
"""
Local Backend Server for Testing Visa Card Onboarding UI

This server runs locally and provides the same API endpoints that would
normally be provided by AWS Lambda + API Gateway in production.

Usage:
    python3.11 local_backend_server.py

Then your React UI at http://localhost:3000 can call:
    - http://localhost:5001/api/visa/secure-token
    - http://localhost:5001/api/visa/onboard-card
    - etc.
"""

from flask import Flask, jsonify, request

# from flask_cors import CORS  # Not needed - API Gateway handles CORS
import traceback
import uuid
import hashlib
import json

# Import only what we need at startup
from visa.secure_token import get_secure_token_direct


# Lazy import wrapper functions to avoid loading secrets at startup
def lazy_import_flow():
    """Lazy import flow module to avoid loading secrets at startup"""
    from visa import flow

    return flow


app = Flask(__name__)

# CORS configuration - DISABLED (API Gateway handles CORS)
# Flask-CORS causes duplicate headers when used with API Gateway
# ALLOWED_ORIGINS = [
#     'https://vcas.local.com:9000',
#     'https://vcas.local.com:9005',
#     'https://localhost:3000',
#     'https://localhost:5173',  # Vite default port
# ]
# CORS(app, origins=ALLOWED_ORIGINS)

# Visa credentials - lazy load
API_KEY = None
CLIENT_APP_ID = "VICTestAccountTR"


def get_request_json():
    """
    Safely get JSON from request body.

    When running through WsgiToAsgi in Lambda, Flask's request.json
    fails and request.get_data() returns empty. The solution is to read
    directly from wsgi.input stream.
    """
    # Try Flask's normal request.json first (with exception handling)
    try:
        if request.json is not None:
            return request.json
    except Exception:
        pass  # Fall through to wsgi.input method

    # Read directly from wsgi.input (works in Lambda with WsgiToAsgi)
    try:
        if "wsgi.input" in request.environ:
            wsgi_input = request.environ["wsgi.input"]
            wsgi_input.seek(0)
            body_bytes = wsgi_input.read()
            if body_bytes:
                body_str = body_bytes.decode("utf-8")
                return json.loads(body_str)

        # Fallback: try get_data()
        raw_data = request.get_data(cache=True, as_text=True)
        if raw_data:
            return json.loads(raw_data)

        return {}

    except json.JSONDecodeError as e:
        print(f"❌ JSON decode error: {e}")
        raise
    except Exception as e:
        print(f"❌ Error getting request data: {e}")
        traceback.print_exc()
        raise


def get_api_key():
    """Lazy load API key when needed"""
    global API_KEY
    if API_KEY is None:
        from visa.helpers import get_secret

        try:
            API_KEY = get_secret("visa/api-key", "us-east-1")
            print("✅ Loaded Visa API key from AWS Secrets Manager")
        except Exception as e:
            print(f"⚠️  Warning: Could not load API key from Secrets Manager: {e}")
            API_KEY = None
    return API_KEY


@app.route("/", methods=["GET"])
def home():
    """Health check endpoint"""
    return jsonify(
        {
            "status": "running",
            "message": "Visa Local Backend Server",
            "endpoints": [
                "GET /api/visa/secure-token",
                "POST /api/visa/onboard-card",
                "POST /api/visa/device-attestation",
                "POST /api/visa/device-binding",
                "POST /api/visa/step-up",
                "POST /api/visa/validate-otp",
                "POST /api/visa/complete-passkey",
                "POST /api/visa/vic/enroll-card",
                "POST /api/visa/vic/initiate-purchase",
                "POST /api/visa/vic/payment-credentials",
            ],
        }
    )


@app.route("/api/visa/secure-token", methods=["GET"])
def get_secure_token_endpoint():
    """
    Get secureToken from Visa OAuth API (no iframe!)

    Returns:
        {
            "success": true,
            "secureToken": "ezAwMX06...",
            "requestID": "uuid-here"
        }
    """
    try:
        print("\n=== GET /api/visa/secure-token ===")

        api_key = get_api_key()
        if not api_key:
            raise Exception("API_KEY not configured")

        result = get_secure_token_direct(api_key, CLIENT_APP_ID)

        print("✅ SecureToken obtained successfully")

        return jsonify(
            {
                "success": True,
                "secureToken": result["secureToken"],
                "requestID": result["requestID"],
                "proof_verifier": result.get("proof_verifier"),
                "device_fingerprint": result.get("device_fingerprint"),
            }
        )

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/visa/onboard-card", methods=["POST"])
def onboard_card_endpoint():
    """
    Enroll card and provision token

    Request body:
        {
            "email": "user@example.com",
            "cardNumber": "4622943123044159",
            "cvv": "598",
            "expirationMonth": "12",
            "expirationYear": "2026",
            "secureToken": "ezAwMX06..."  (optional - will generate if not provided)
        }

    Returns:
        {
            "success": true,
            "vPanEnrollmentID": "...",
            "vProvisionedTokenId": "..."
        }
    """
    try:
        print("\n=== POST /api/visa/onboard-card ===")

        # Lazy import flow module
        flow = lazy_import_flow()

        data = get_request_json()
        email = data.get("email")
        card_number = data.get("cardNumber")
        cvv = data.get("cvv")
        exp_month = data.get("expirationMonth")
        exp_year = data.get("expirationYear")
        secure_token = data.get("secureToken")  # Get secure token from iframe session
        browser_data = data.get("browserData")  # Get browser data from iframe session

        print(f"Email: {email}")
        print(f"Card: {card_number[:4]}...{card_number[-4:]}")

        # Use the secure token from iframe CREATE_AUTH_SESSION
        if not secure_token:
            raise Exception("Secure token not provided from iframe session")

        print("✅ Using SecureToken from iframe")

        # Log browser data for debugging
        if browser_data:
            print(f"✅ Using BrowserData from iframe: {list(browser_data.keys())}")
        else:
            print("⚠️  Warning: No browserData provided from iframe")

        # Step 2: Prepare card data and enroll
        pan_data = {
            "accountNumber": card_number,
            "cvv2": cvv,
            "expirationDate": {"month": exp_month, "year": exp_year},
        }

        # Generate x_request_id for VPP session continuity
        x_request_id = str(uuid.uuid4())

        # Step 1: Enroll the card
        enrollment_result = flow.enroll_pan(email, pan_data, CLIENT_APP_ID, x_request_id=x_request_id)
        vpan_enrollment_id = enrollment_result["vPanEnrollmentID"]

        # Step 2: Provision the token
        provision_result = flow.provision_token(
            vpan_enrollment_id,
            email,
            CLIENT_APP_ID,
            browser_data=browser_data,
            x_request_id=x_request_id,
        )
        print(f"📦 Provision result keys: {provision_result.keys()}")
        print(f"📦 Full provision result: {provision_result}")
        v_provisioned_token_id = provision_result["vProvisionedTokenID"]
        print(f"📦 Extracted vProvisionedTokenId: {v_provisioned_token_id}")

        result = {
            "vPanEnrollmentID": vpan_enrollment_id,
            "vProvisionedTokenId": v_provisioned_token_id,
            "lastFourDigits": pan_data["accountNumber"][-4:],
            "xRequestId": x_request_id,
        }

        # Generate client_reference_id for this transaction (will be reused across all API calls)
        client_reference_id = str(uuid.uuid4())

        print(f"✅ Card enrolled and provisioned: {result.get('vPanEnrollmentID')}")
        print(f"✅ Token ID: {result.get('vProvisionedTokenId')}")
        print(f"✅ Client Reference ID: {client_reference_id}")

        return jsonify(
            {
                "success": True,
                "vPanEnrollmentID": result.get("vPanEnrollmentID"),
                "vProvisionedTokenId": result.get("vProvisionedTokenId"),
                "lastFourDigits": result.get("lastFourDigits"),
                "secureToken": secure_token,  # Return secure token for device attestation
                "xRequestId": result.get("xRequestId"),  # Return x_request_id for VPP session continuity
                "clientReferenceId": client_reference_id,  # Return client_reference_id for transaction tracking
            }
        )

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/visa/device-attestation", methods=["POST"])
def device_attestation_endpoint():
    """
    Get WebAuthn device attestation options for passkey creation

    This handles TWO different steps in the VPP flow:
    - Step 4: AUTHENTICATE (reasonCode=PAYMENT, before device binding)
    - Step 9: REGISTER (reasonCode=DEVICE_BINDING, after OTP validation)

    Request body:
        {
            "email": "user@example.com",
            "vProvisionedTokenId": "...",
            "secureToken": "...",
            "browserData": {...},
            "step": "AUTHENTICATE" or "REGISTER",
            "panData": {...},  // Only for AUTHENTICATE step
            "xRequestId": "...",  // VPP session x-request-id from onboard-card
            "clientReferenceId": "..."  // Transaction client_reference_id from onboard-card
        }

    Returns:
        {
            "success": true,
            "action": "REGISTER",  // For AUTHENTICATE step
            "authenticationContext": {...},  // For REGISTER step
            "fullResponse": {...}
        }
    """
    try:
        print("\n=== POST /api/visa/device-attestation ===")

        # Import flow functions
        flow = lazy_import_flow()

        data = get_request_json()
        email = data.get("email")
        v_provisioned_token_id = data.get("vProvisionedTokenId")
        secure_token = data.get("secureToken")
        browser_data = data.get("browserData")
        step = data.get("step", "AUTHENTICATE")  # Default to AUTHENTICATE for backward compatibility
        # Note: panData is no longer needed - we use email for encAuthenticationData
        x_request_id = data.get("xRequestId")  # VPP session x-request-id
        client_reference_id = data.get("clientReferenceId")  # Transaction client_reference_id (reuse from onboard-card)

        print(f"Email: {email}")
        print(f"TokenId: {v_provisioned_token_id}")
        print(f"SecureToken: {'present' if secure_token else 'None'}")
        print(f"Step: {step}")

        # Validate required fields
        if not client_reference_id:
            raise ValueError("clientReferenceId is required - must be passed from onboard-card response")
        if not x_request_id:
            raise ValueError("xRequestId is required - must be passed from onboard-card response")

        if step == "AUTHENTICATE":
            # Step 4: Device Attestation Authenticate (reasonCode=PAYMENT, type=AUTHENTICATE)
            # This checks if device binding is needed
            print("🔵 Calling device_attestation_authenticate (Step 4)")

            # Get transaction amount from request (default to 567.89)
            transaction_amount = data.get("transactionAmount", "567.89")

            result = flow.device_attestation_authenticate(
                email,  # FIXED: Pass email instead of pan_data
                secure_token,
                v_provisioned_token_id,
                browser_data,
                CLIENT_APP_ID,
                client_reference_id,
                x_request_id,
                transaction_amount,
            )

            print("✅ Device attestation authenticate completed")
            # Extract action from nested authenticationContext
            action = result.get("authenticationContext", {}).get("action")
            print(f"Action: {action}")

            return jsonify(
                {
                    "success": True,
                    "action": action,  # Should be "REGISTER" indicating device binding needed
                    "fullResponse": result,
                }
            )

        elif step == "REGISTER":
            # Step 9: Device Attestation Register (reasonCode=DEVICE_BINDING, type=REGISTER)
            # This returns the payload for iframe passkey creation
            print("🔵 Calling device_attestation_register (Step 9)")

            result = flow.device_attestation_register(
                v_provisioned_token_id,
                email,
                secure_token,
                browser_data,
                CLIENT_APP_ID,
                client_reference_id,
                x_request_id,
            )

            print("✅ Device attestation register completed")
            print(f"Has authenticationContext: {'authenticationContext' in result}")

            return jsonify(
                {
                    "success": True,
                    "authenticationContext": result.get("authenticationContext"),
                    "fullResponse": result,
                }
            )

        else:
            raise ValueError(f"Invalid step: {step}. Must be 'AUTHENTICATE' or 'REGISTER'")

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/visa/complete-passkey", methods=["POST"])
def complete_passkey_endpoint():
    """
    Complete passkey registration with Visa

    Request body:
        {
            "vProvisionedTokenId": "...",
            "fidoBlob": "base64-encoded-fido-response"
        }

    Returns:
        {
            "success": true
        }
    """
    try:
        print("\n=== POST /api/visa/complete-passkey ===")

        # Import flow functions
        _flow = lazy_import_flow()

        data = get_request_json()
        v_provisioned_token_id = data.get("vProvisionedTokenId")
        fido_blob = data.get("fidoBlob")

        print(f"TokenId: {v_provisioned_token_id}")
        print(f"FidoBlob: {'present' if fido_blob else 'None'} (length: {len(fido_blob) if fido_blob else 0})")

        if not fido_blob or (isinstance(fido_blob, str) and fido_blob.strip() == ""):
            raise ValueError("fidoBlob is empty")

        # fidoBlob is a URL-encoded string from Visa iframe
        # Parse it to extract parameters
        import urllib.parse

        params = urllib.parse.parse_qs(fido_blob)
        code = params.get("c", [""])[0]
        hint = params.get("h", [""])[0]

        result = {
            "success": True,
            "code": code,
            "fidoBlob": fido_blob,
            "vProvisionedTokenId": v_provisioned_token_id,
            "hint": hint,
        }

        print("✅ Passkey registration completed")

        return jsonify({"success": True, "result": result})

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/visa/device-binding", methods=["POST"])
def device_binding_endpoint():
    """
    Step 5: Get Device Binding (VPP)

    Request body:
        {
            "vProvisionedTokenId": "...",
            "secureToken": "...",
            "email": "user@example.com",
            "browserData": {...},
            "xRequestId": "...",  // VPP session x-request-id from onboard-card
            "clientReferenceId": "..."  // Transaction client_reference_id from onboard-card
        }

    Returns:
        {
            "success": true,
            "stepUpRequest": [{...}],
            "status": "CHALLENGE"
        }
    """
    try:
        print("\n=== POST /api/visa/device-binding ===")

        # Import flow functions
        flow = lazy_import_flow()

        data = get_request_json()
        v_provisioned_token_id = data.get("vProvisionedTokenId")
        secure_token = data.get("secureToken")
        email = data.get("email")
        browser_data = data.get("browserData")
        x_request_id = data.get("xRequestId")  # VPP session x-request-id
        client_reference_id = data.get("clientReferenceId")  # Transaction client_reference_id (reuse from onboard-card)

        print(f"TokenId: {v_provisioned_token_id}")
        print(f"Email: {email}")
        print(f"SecureToken: {'present' if secure_token else 'None'}")

        # Validate required fields
        if not client_reference_id:
            raise ValueError("clientReferenceId is required - must be passed from onboard-card response")
        if not x_request_id:
            raise ValueError("xRequestId is required - must be passed from onboard-card response")

        # Call device_binding from flow.py
        result = flow.device_binding(
            secure_token,
            email,
            v_provisioned_token_id,
            browser_data,
            CLIENT_APP_ID,
            client_reference_id,
            x_request_id,
        )

        print("✅ Device binding completed")
        print(f"Status: {result.get('status')}")
        print(f"Step-up options: {len(result.get('stepUpRequest', []))}")

        return jsonify(
            {
                "success": True,
                "stepUpRequest": result.get("stepUpRequest", []),
                "status": result.get("status"),
                "fullResponse": result,
            }
        )

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/visa/step-up", methods=["POST"])
def step_up_endpoint():
    """
    Step 6: Select Step-up Option (VPP)

    Request body:
        {
            "vProvisionedTokenId": "...",
            "identifier": "...",
            "method": "OTPSMS" or "OTPEMAIL",
            "xRequestId": "...",  // VPP session x-request-id from onboard-card
            "clientReferenceId": "..."  // Transaction client_reference_id from onboard-card
        }

    Returns:
        {
            "success": true
        }
    """
    try:
        print("\n=== POST /api/visa/step-up ===")

        # Import flow functions
        flow = lazy_import_flow()

        data = get_request_json()
        v_provisioned_token_id = data.get("vProvisionedTokenId")
        identifier = data.get("identifier")
        method = data.get("method")
        x_request_id = data.get("xRequestId")  # VPP session x-request-id
        client_reference_id = data.get("clientReferenceId")  # Transaction client_reference_id (reuse from onboard-card)

        print(f"TokenId: {v_provisioned_token_id}")
        print(f"Method: {method}")

        # Validate required fields
        if not client_reference_id:
            raise ValueError("clientReferenceId is required - must be passed from onboard-card response")
        if not x_request_id:
            raise ValueError("xRequestId is required - must be passed from onboard-card response")

        # Call step_up from flow.py
        result = flow.step_up(
            v_provisioned_token_id,
            identifier,
            CLIENT_APP_ID,
            client_reference_id,
            x_request_id,
        )

        print("✅ Step-up option selected")

        return jsonify({"success": True, "result": result})

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/visa/validate-otp", methods=["POST"])
def validate_otp_endpoint():
    """
    Step 8: Validate OTP (VPP)

    Request body:
        {
            "vProvisionedTokenId": "...",
            "otpValue": "123456",
            "xRequestId": "...",  // VPP session x-request-id from onboard-card
            "clientReferenceId": "..."  // Transaction client_reference_id from onboard-card
        }

    Returns:
        {
            "success": true,
            "status": "VALIDATED"
        }
    """
    try:
        print("\n=== POST /api/visa/validate-otp ===")

        # Import flow functions
        flow = lazy_import_flow()

        data = get_request_json()
        v_provisioned_token_id = data.get("vProvisionedTokenId")
        otp_value = data.get("otpValue")
        x_request_id = data.get("xRequestId")  # VPP session x-request-id
        client_reference_id = data.get("clientReferenceId")  # Transaction client_reference_id (reuse from onboard-card)

        print(f"TokenId: {v_provisioned_token_id}")
        print(f"OTP: {'*' * len(otp_value) if otp_value else 'None'}")

        # Validate required fields
        if not client_reference_id:
            raise ValueError("clientReferenceId is required - must be passed from onboard-card response")
        if not x_request_id:
            raise ValueError("xRequestId is required - must be passed from onboard-card response")

        # Call validate_otp from flow.py
        result = flow.validate_otp(
            v_provisioned_token_id,
            otp_value,
            CLIENT_APP_ID,
            client_reference_id,
            x_request_id,
        )

        print("✅ OTP validated")
        print(f"Status: {result.get('status')}")

        return jsonify({"success": True, "status": result.get("status"), "result": result})

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/visa/vic/enroll-card", methods=["POST"])
def vic_enroll_card_endpoint():
    """
    Step 14: VIC Enroll Card

    Request body:
        {
            "email": "user@example.com",
            "vProvisionedTokenId": "..."
        }

    Returns:
        {
            "success": true,
            "clientReferenceId": "...",
            "consumerId": "...",
            "clientDeviceId": "...",
            "status": "..."
        }
    """
    try:
        print("\n=== POST /api/visa/vic/enroll-card ===")

        # Import flow functions
        flow = lazy_import_flow()

        data = get_request_json()
        email = data.get("email")
        v_provisioned_token_id = data.get("vProvisionedTokenId")

        print(f"Email: {email}")
        print(f"TokenId: {v_provisioned_token_id}")

        # Generate IDs for VIC enrollment (matching working version)
        client_reference_id = str(uuid.uuid4())
        client_device_id = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()[:32]
        consumer_id = str(uuid.uuid4())

        print(f"Generated client_reference_id: {client_reference_id}")
        print(f"Generated client_device_id: {client_device_id}")
        print(f"Generated consumer_id: {consumer_id}")

        # Call VIC enroll card (will lazy-load secrets)
        result = flow.vic_enroll_card(
            email,
            v_provisioned_token_id,
            CLIENT_APP_ID,
            client_reference_id,
            client_device_id,
            consumer_id,
        )

        print(f"✅ VIC card enrolled: {result.get('clientReferenceId')}")

        # Return all IDs needed for subsequent purchase flow
        return jsonify(
            {
                "success": True,
                "clientReferenceId": result.get("clientReferenceId"),
                "consumerId": consumer_id,
                "clientDeviceId": client_device_id,
                "status": result.get("status"),
                "raw": result.get("raw"),
            }
        )

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/visa/vic/initiate-purchase", methods=["POST"])
def vic_initiate_purchase_endpoint():
    """
    Step 15: VIC Initiate Purchase Instructions

    Request body:
        {
            "vProvisionedTokenId": "...",
            "consumerId": "...",
            "clientReferenceId": "...",
            "clientDeviceId": "...",
            "consumerRequest": "Buy apples",
            "authIdentifier": "...",
            "dfpSessionId": "...",
            "fidoBlob": "...",
            "transactionAmount": "444.44"
        }

    Returns:
        {
            "success": true,
            "instructionId": "...",
            "clientReferenceId": "...",
            "status": "..."
        }
    """
    try:
        print("\n=== POST /api/visa/vic/initiate-purchase ===")

        # Import flow functions
        flow = lazy_import_flow()

        data = get_request_json()
        v_provisioned_token_id = data.get("vProvisionedTokenId")
        consumer_id = data.get("consumerId")
        client_reference_id = data.get("clientReferenceId")
        client_device_id = data.get("clientDeviceId")
        consumer_request_raw = data.get("consumerRequest", "Purchase from cart")
        auth_identifier = data.get("authIdentifier", "")
        dfp_session_id = data.get("dfpSessionId", "")
        iframe_auth_fido_blob = data.get("fidoBlob", "")
        transaction_amount = data.get("transactionAmount", "444.44")

        # Truncate consumer_request to avoid Visa API 400 error (max 150 chars)
        MAX_CONSUMER_REQUEST_LENGTH = 150
        if len(consumer_request_raw) > MAX_CONSUMER_REQUEST_LENGTH:
            consumer_request = consumer_request_raw[: MAX_CONSUMER_REQUEST_LENGTH - 3] + "..."
            print(f"⚠️  Consumer request truncated from {len(consumer_request_raw)} to {len(consumer_request)} chars")
        else:
            consumer_request = consumer_request_raw

        print(f"TokenId: {v_provisioned_token_id}")
        print(f"ConsumerId: {consumer_id}")
        print(f"ClientReferenceId: {client_reference_id}")
        print(f"ClientDeviceId: {client_device_id}")
        print(f"ConsumerRequest: {consumer_request}")
        print(f"TransactionAmount: ${transaction_amount}")

        # Generate mandate_id for this purchase
        mandate_id = str(uuid.uuid4())
        print(f"Generated mandate_id: {mandate_id}")

        # Call VIC initiate purchase with all 11 required parameters
        result = flow.vic_initiate_purchase_instructions(
            v_provisioned_token_id,  # 1. provisioned_token_id
            consumer_id,  # 2. consumer_id (from enrollment)
            CLIENT_APP_ID,  # 3. client_app_id
            mandate_id,  # 4. mandate_id (generated)
            consumer_request,  # 5. consumer_request (from request)
            client_reference_id,  # 6. client_reference_id (from enrollment)
            client_device_id,  # 7. client_device_id (from enrollment)
            auth_identifier,  # 8. auth_identifier (from request)
            dfp_session_id,  # 9. dfp_session_id (from request)
            iframe_auth_fido_blob,  # 10. iframe_auth_fido_blob (from request)
            transaction_amount,  # 11. transaction_amount (from request)
        )

        print(f"✅ Purchase instructions initiated: {result.get('instructionId')}")

        return jsonify(
            {
                "success": True,
                "instructionId": result.get("instructionId"),
                "clientReferenceId": result.get("clientReferenceId"),
                "status": result.get("status"),
                "raw": result.get("raw"),
            }
        )

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/visa/vic/payment-credentials", methods=["POST"])
def vic_payment_credentials_endpoint():
    """
    Step 16: VIC Get Payment Credentials (Cryptogram)

    Request body:
        {
            "instructionId": "...",
            "vProvisionedTokenId": "...",
            "clientReferenceId": "...",
            "merchantUrl": "https://www.rei.com",
            "merchantName": "REI",
            "transactionAmount": "444.44"
        }

    Returns:
        {
            "success": true,
            "signedPayload": "...",
            "instructionId": "...",
            "status": "..."
        }
    """
    try:
        print("\n=== POST /api/visa/vic/payment-credentials ===")

        # Import flow functions
        flow = lazy_import_flow()

        data = get_request_json()
        instruction_id = data.get("instructionId")
        v_provisioned_token_id = data.get("vProvisionedTokenId")
        client_reference_id = data.get("clientReferenceId", str(uuid.uuid4()))
        merchant_url = data.get("merchantUrl", "https://www.rei.com")
        merchant_name = data.get("merchantName", "REI")
        transaction_amount = data.get("transactionAmount", "444.44")

        print(f"InstructionId: {instruction_id}")
        print(f"TokenId: {v_provisioned_token_id}")
        print(f"ClientReferenceId: {client_reference_id}")
        print(f"Merchant: {merchant_name} ({merchant_url})")
        print(f"Amount: ${transaction_amount}")

        # Call VIC get payment credentials with all required parameters
        result = flow.vic_get_payment_credentials(
            instruction_id,
            v_provisioned_token_id,
            CLIENT_APP_ID,
            client_reference_id,
            merchant_url,
            merchant_name,
            transaction_amount,
        )

        print("✅ Payment credentials retrieved")

        return jsonify(
            {
                "success": True,
                "signedPayload": result.get("signedPayload"),
                "instructionId": result.get("instructionId"),
                "status": result.get("status"),
                "raw": result.get("raw"),
            }
        )

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Visa Local Backend Server")
    parser.add_argument("--http", action="store_true", help="Run in HTTP mode (no SSL)")
    args = parser.parse_args()

    use_ssl = not args.http
    protocol = "https" if use_ssl else "http"

    print("=" * 60)
    print(f"🚀 Starting Visa Local Backend Server ({protocol.upper()})")
    print("=" * 60)
    print()
    print(f"Server running at: {protocol}://localhost:5001")
    print()
    print("Available endpoints:")
    print(f"  GET  {protocol}://localhost:5001/api/visa/secure-token")
    print(f"  POST {protocol}://localhost:5001/api/visa/onboard-card")
    print(f"  POST {protocol}://localhost:5001/api/visa/device-attestation")
    print(f"  POST {protocol}://localhost:5001/api/visa/complete-passkey")
    print()
    if use_ssl:
        print("⚠️  You may see SSL warnings - this is normal for self-signed certificates")
        print("   Click 'Advanced' -> 'Proceed to localhost' in your browser")
        print()
        print("💡 TIP: Run with --http flag to use HTTP instead (no SSL warnings)")
    else:
        print("⚠️  Running in HTTP mode (no SSL) - for local development only")
    print()
    print("=" * 60)
    print()

    if use_ssl:
        app.run(host="localhost", port=5001, ssl_context="adhoc")
    else:
        app.run(host="localhost", port=5001)
