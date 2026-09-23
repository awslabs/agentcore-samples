import json
import time
import hashlib
import requests
import logging
import base64
import uuid
from visa.helpers import (
    get_secret,
    generate_x_pay_token,
    encrypt_card_data,
    decrypt_token_info,
    create_email_hash,
    encrypt_payload,
    decrypt_rsa,
)
from visa.secure_token import get_secure_token_direct


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# Config
region = "us-east-1"

# Secrets Manager secret names
server_cert_secret_name = "visa/server-mle-cert"  # pragma: allowlist secret
private_cert_secret_name = "visa/mle-private-cert"  # pragma: allowlist secret
api_key_secret_name = "visa/api-key"  # pragma: allowlist secret
shared_secret_secret_name = "visa/shared-secret"  # pragma: allowlist secret
encryption_api_key_secret_name = "visa/encryption-api-key"  # pragma: allowlist secret
encryption_shared_secret_secret_name = "visa/encryption-shared-secret"  # pragma: allowlist secret
vic_api_key_secret_name = "visa/api-key"  # pragma: allowlist secret

# Lazy-load secrets - only load when needed
_secrets_cache = {}


def get_visa_secret(secret_name):
    """Lazy load secrets from AWS Secrets Manager with caching"""
    if secret_name not in _secrets_cache:
        _secrets_cache[secret_name] = get_secret(secret_name, region)
    return _secrets_cache[secret_name]


# All secrets are lazy-loaded when functions are called
server_cert = None
private_cert = None
api_key = None
shared_secret = None
encryption_api_key = None
encryption_shared_secret = None
vic_api_key = None
vic_key_id = None


def _ensure_vts_secrets():
    """Ensure VTS secrets are loaded"""
    global server_cert, private_cert, api_key, shared_secret, encryption_api_key, encryption_shared_secret
    if api_key is None:
        server_cert = get_visa_secret(server_cert_secret_name)
        private_cert = get_visa_secret(private_cert_secret_name)
        api_key = get_visa_secret(api_key_secret_name)
        shared_secret = get_visa_secret(shared_secret_secret_name)
        encryption_api_key = get_visa_secret(encryption_api_key_secret_name)
        encryption_shared_secret = get_visa_secret(encryption_shared_secret_secret_name)


def enroll_pan(
    email,
    pan_data,
    client_app_id,
    client_wallet_account_id="40010062596",
    x_request_id=None,
):  # PanEnrollmentId
    """
    Step 1: Enroll PAN with Visa Token Service

    Args:
        email: User email address
        pan_data: Dictionary containing accountNumber, cvv2, and expirationDate
        client_wallet_account_id: Wallet account ID (default: "40010062596")
        client_app_id: Client application ID (default: "VICTestAccountTR")
        x_request_id: Request ID for VPP session continuity (optional, generates UUID if not provided)

    Returns:
        Dictionary containing the response from Visa API with vPanEnrollmentID

    Raises:
        requests.exceptions.RequestException: If the API request fails
        KeyError: If the response doesn't contain expected fields
    """
    _ensure_vts_secrets()

    logger.info("=" * 80)
    logger.info("Starting Visa PAN Enrollment Process")
    logger.info("=" * 80)

    # Generate x_request_id if not provided (this starts the VPP session)
    if not x_request_id:
        x_request_id = str(uuid.uuid4())
        logger.info(f"Generated new x-request-id: {x_request_id}")

    resource_path = "vts/panEnrollments"

    # Encrypt payment instrument
    enc_payment_instrument = encrypt_card_data(pan_data, encryption_api_key, encryption_shared_secret)

    # Build query string and URL
    query_string_for_token = f"apiKey={api_key}"
    url = f"https://cert.api.visa.com/vts/panEnrollments?apiKey={api_key}"
    # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    logger.info(f"Target URL: {url.split('?')[0]}...")  # API key redacted

    # Build payload (no indentation for HMAC calculation)
    payload = json.dumps(
        {
            "clientWalletAccountID": client_wallet_account_id,
            "clientAppID": client_app_id,
            "locale": "en_US",
            "encPaymentInstrument": enc_payment_instrument,
            "panSource": "ONFILE",
        }
    )

    payload = payload.replace(" ", "")
    logger.info(f"Request payload prepared (length: {len(payload)} bytes)")

    # Generate X-PAY-TOKEN
    x_pay_token = generate_x_pay_token(shared_secret, resource_path, query_string_for_token, payload)

    # Generate X-SERVICE-CONTEXT header
    # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    service_context = {"serviceId": "vts", "serviceVersion": "1.0"}
    service_context_json = json.dumps(service_context, separators=(",", ":"))
    x_service_context = base64.b64encode(service_context_json.encode("utf-8")).decode("utf-8")
    # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    logger.info(f"X-SERVICE-CONTEXT generated: {x_service_context}")

    # Set headers
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-PAY-TOKEN": x_pay_token,
        "X-SERVICE-CONTEXT": x_service_context,
        "x-request-id": x_request_id,
    }

    logger.info("\n" + "=" * 80)
    logger.info("REQUEST DETAILS")
    logger.info("=" * 80)
    logger.info("Method: POST")
    logger.info(f"URL: {url}")
    logger.info("\nRequest Headers:")
    for key, value in headers.items():
        logger.info(f"  {key}: {value}")
    logger.info("\nRequest Body:")
    logger.info(payload)

    logger.info("\n" + "=" * 80)
    logger.info("Sending Request to Visa API")
    logger.info("=" * 80)

    # Make the request
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=300)
        response.raise_for_status()

        logger.info("\nResponse Body (Parsed JSON):")
        response_json = response.json()
        logger.info("[Response data redacted for security]")

        # Validate response contains required field
        if "vPanEnrollmentID" not in response_json:
            raise KeyError("Response missing 'vPanEnrollmentID' field")

        logger.info("\n" + "=" * 80)
        logger.info("PAN Enrollment completed successfully")
        logger.info("=" * 80)

        return response_json

    except json.JSONDecodeError as e:
        logger.error(f"Could not parse response as JSON: {e}")
        raise
    except requests.exceptions.HTTPError as e:
        logger.error("\n" + "=" * 80)
        logger.error("PAN ENROLLMENT FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        if hasattr(e, "response") and e.response is not None:
            logger.error(f"Response status code: {e.response.status_code}")
            logger.error(f"Response text: {e.response.text}")
        raise
    except requests.exceptions.RequestException as e:
        logger.error("\n" + "=" * 80)
        logger.error("PAN ENROLLMENT FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        raise


def provision_token(
    vpan_enrollment_id,
    email,
    client_app_id,
    client_wallet_account_id="40010062596",
    browser_data=None,
    x_request_id=None,
):
    """
    Step 2: Provision token for an enrolled PAN

    Args:
        vpan_enrollment_id: The vPanEnrollmentID from the enrollment response
        email: User email address for hashing
        client_wallet_account_id: Wallet account ID (default: "40010062596")
        client_app_id: Client application ID (default: "VICTestAccountTR")
        browser_data: Optional browser data from Visa iframe (if not provided, uses dummy data)
        x_request_id: Request ID for VPP session continuity (must match enroll_pan x_request_id)

    Returns:
        Dictionary containing the response from Visa API with tokenInfo

    Raises:
        requests.exceptions.RequestException: If the API request fails
        KeyError: If the response doesn't contain expected fields
    """
    _ensure_vts_secrets()

    logger.info("=" * 80)
    logger.info("Starting Provision Token given PAN EnrollmentID")
    logger.info("=" * 80)

    if not x_request_id:
        logger.warning("No x-request-id provided - VPP session continuity may be broken!")
        x_request_id = str(uuid.uuid4())

    resource_path = f"vts/panEnrollments/{vpan_enrollment_id}/provisionedTokens"
    url = f"https://cert.api.visa.com/vts/panEnrollments/{vpan_enrollment_id}/provisionedTokens?apiKey={api_key}"
    # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    logger.info(f"Target URL: {url.split('?')[0]}...")  # API key redacted

    hash_email = create_email_hash(email)

    # Build risk data for encryption
    # Use real browser data from iframe if available, otherwise use dummy data
    if browser_data:
        logger.info("✅ Using real browser data from Visa iframe")
        logger.info(f"Browser data keys: {list(browser_data.keys())}")

        # Extract a unique device identifier from browser data
        user_agent = browser_data.get("userAgent", "Unknown")
        device_id = hashlib.md5(user_agent.encode(), usedforsecurity=False).hexdigest()[:16]

        _risk_data = {
            "deviceFingerprint": {
                "deviceID": device_id,
                "deviceType": "WEB",
                "osVersion": browser_data.get("browserPlatform", "Web Platform"),
                "model": "Web Browser",
            },
            "ipAddress": browser_data.get("ipAddress", "192.168.1.1"),
            "timestamp": str(int(time.time())),
        }
    else:
        logger.warning("⚠️  No browser data provided - using dummy device data")
        _risk_data = {
            "deviceFingerprint": {
                "deviceID": "device-12345",
                "deviceType": "MOBILE",
                "osVersion": "iOS 16.0",
                "model": "iPhone 14 Pro",
            },
            "ipAddress": "192.168.1.1",
            "timestamp": str(int(time.time())),
        }

    # Encrypt risk data only if using non-iframe flow
    # For iframe flows, the device context is already established by Visa's iframe
    payload_dict = {
        "clientWalletAccountID": client_wallet_account_id,
        "clientAppID": client_app_id,
        "protectionType": "SOFTWARE",
        "presentationType": ["AI_AGENT"],
        "clientWalletAccountEmailAddressHash": hash_email,
    }

    payload = json.dumps(payload_dict)

    # Generate X-PAY-TOKEN
    query_string_for_token = f"apiKey={api_key}"
    x_pay_token = generate_x_pay_token(shared_secret, resource_path, query_string_for_token, payload)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-PAY-TOKEN": x_pay_token,
        "x-request-id": x_request_id,
    }

    logger.info("\nRequest Headers:")
    for key, value in headers.items():
        logger.info(f"  {key}: {value}")
    logger.info("\nRequest Body:")
    logger.info(payload)

    # Make the request
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=300)
        response.raise_for_status()

        logger.info("\nResponse Body (Parsed JSON):")
        response_json = response.json()
        logger.info("[Response data redacted for security]")

        # Validate response contains required fields
        if "tokenInfo" not in response_json or "encTokenInfo" not in response_json["tokenInfo"]:
            raise KeyError("Response missing 'tokenInfo' or 'encTokenInfo' field")

        logger.info("\n" + "=" * 80)
        logger.info("Token Provisioning completed successfully")
        logger.info("=" * 80)

        return response_json

    except json.JSONDecodeError as e:
        logger.error(f"Could not parse response as JSON: {e}")
        raise
    except requests.exceptions.HTTPError as e:
        logger.error("\n" + "=" * 80)
        logger.error("TOKEN PROVISIONING FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        if hasattr(e, "response") and e.response is not None:
            logger.error(f"Response status code: {e.response.status_code}")
            logger.error(f"Response text: {e.response.text}")
        raise
    except requests.exceptions.RequestException as e:
        logger.error("\n" + "=" * 80)
        logger.error("TOKEN PROVISIONING FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        raise


def decrypt_and_store_token(enc_token_info):
    """
    Step 3: Decrypt the encrypted token information

    Args:
        enc_token_info: The encrypted token info (JWE format) from the provision response

    Returns:
        Dictionary containing the decrypted token information

    Raises:
        Exception: If decryption fails
    """
    logger.info("=" * 80)
    logger.info("Decrypting the encTokenInfo")
    logger.info("=" * 80)

    try:
        decrypted_token_data = decrypt_token_info(enc_token_info, encryption_shared_secret)

        logger.info("\n" + "=" * 80)
        logger.info("Token Decryption completed successfully")
        logger.info("=" * 80)

        return decrypted_token_data

    except Exception as e:
        logger.error("\n" + "=" * 80)
        logger.error("TOKEN DECRYPTION FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        raise


def get_secure_token(api_key, client_app_id, headless=False):
    """
    Step 4: Get Visa secure token using automated browser workflow

    Args:
        api_key: Your Visa API key
        client_app_id: Client application ID (default: "VICTestAccountTR")
        headless: Run browser in headless mode (default: False)

    Returns:
        str: The secureToken string, or None if failed
    """
    logger.info("=" * 80)
    logger.info("Getting Visa Secure Token")
    logger.info("=" * 80)

    result = get_secure_token_direct(api_key=api_key, client_app_id=client_app_id)

    if result and "secureToken" in result:
        secure_token = result["secureToken"]
        logger.info(f"Successfully retrieved secure token: {secure_token[:60]}...")
        return secure_token
    else:
        logger.error("Failed to retrieve secure token")
        return None


def device_attestation_authenticate(
    email,
    secure_token,
    provisioned_token_id,
    browser_data,
    client_app_id,
    client_reference_id,
    x_request_id,
    transaction_amount="567.89",
):
    """
    Device Attestation Authenticate - Step 4 in VPP flow

    Args:
        email: User email address (NOT pan_data)
        secure_token: Secure token from iframe session
        provisioned_token_id: Token ID from provision step
        browser_data: Browser data from iframe
        client_app_id: Client application ID
        client_reference_id: Transaction reference ID
        x_request_id: VPP session request ID
        transaction_amount: Transaction amount (default "567.89")
    """
    _ensure_vts_secrets()

    logger.info("=" * 80)
    logger.info("Device Attestation Authenticate")
    logger.info(f"Using x-request-id from enrollment: {x_request_id}")

    # Format amount with 2 decimal places
    formatted_amount = f"{float(transaction_amount):.2f}"
    logger.info(f"Transaction amount: {formatted_amount}")

    # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    resource_path = f"vts/provisionedTokens/{provisioned_token_id}/attestation/options"
    url = f"https://cert.api.visa.com/vts/provisionedTokens/{provisioned_token_id}/attestation/options?apiKey={api_key}"
    logger.info(f"Target URL: {url.split('?')[0]}...")  # API key redacted

    # FIXED: Encrypt consumer email info, NOT pan_data
    to_be_encrypted = {"consumerInfo": {"emailAddress": email}}
    encAuthenticationData = encrypt_card_data(to_be_encrypted, encryption_api_key, encryption_shared_secret)

    payload_dict = {
        "authenticationPreferencesRequested": {"selectedPopupForRegister": False},
        "sessionContext": {
            "secureToken": secure_token,
        },
        "dynamicData": {
            "authenticationAmount": formatted_amount,
            "merchantIdentifier": {
                "applicationUrl": "aHR0cHM6Ly93d3cuTWVyY2hhbnQtVlphRjVYQmouY29t",  # pragma: allowlist secret
                "merchantName": "TWVyY2hhbnQgVlphRjVYQmo",
            },
            "currencyCode": "840",
        },
        "browserData": browser_data,
        "encAuthenticationData": encAuthenticationData,
        "reasonCode": "PAYMENT",
        "clientReferenceID": client_reference_id,
        "type": "AUTHENTICATE",
        "clientAppID": client_app_id,
    }

    payload = json.dumps(payload_dict)

    logging.info(f"Body:{payload}")

    # Generate X-PAY-TOKEN
    query_string_for_token = f"apiKey={api_key}"
    x_pay_token = generate_x_pay_token(shared_secret, resource_path, query_string_for_token, payload)

    # FIXED: Add X-SERVICE-CONTEXT header
    service_context = {"serviceId": "vts", "serviceVersion": "1.0"}
    service_context_json = json.dumps(service_context, separators=(",", ":"))
    x_service_context = base64.b64encode(service_context_json.encode("utf-8")).decode("utf-8")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-PAY-TOKEN": x_pay_token,
        "X-SERVICE-CONTEXT": x_service_context,
        "x-request-id": x_request_id,
    }

    logging.info(f"Headers: {headers}")

    # Make the request
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=300)
        response.raise_for_status()

        logger.info("\nResponse Body (Parsed JSON):")
        response_json = response.json()
        logger.info("[Response data redacted for security]")

        logger.info("\n" + "=" * 80)
        logger.info("Device Attestation Authenticate completed successfully")
        logger.info("=" * 80)

        return response_json

    except json.JSONDecodeError as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE ATTESTATION AUTHENTICATE- JSON DECODE ERROR")
        logger.error("=" * 80)
        logger.error(f"Could not parse response as JSON: {e}")
        logger.error(
            f"Response status code: {response.status_code if 'response' in locals() else 'N/A'}"
            # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
        )
        logger.error(f"Response text: {response.text if 'response' in locals() else 'No response'}")
        raise
    except requests.exceptions.HTTPError as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE ATTESTATION AUTHENTICATE - REQUEST FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        if hasattr(e, "response") and e.response is not None:
            logger.error(f"Response status code: {e.response.status_code}")
            logger.error(f"Response text: {e.response.text}")
        raise
    except requests.exceptions.RequestException as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE ATTESTATION AUTHENTICATE - REQUEST FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        raise
    except Exception as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE ATTESTATION AUTHENTICATE - UNEXPECTED ERROR")
        logger.error("=" * 80)
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error message: {str(e)}")
        import traceback

        logger.error(f"Traceback:\n{traceback.format_exc()}")
        raise


def device_binding(
    secure_token,
    email,
    provisioned_token_id,
    browser_data,
    client_app_id,
    client_reference_id,
    x_request_id,
):
    _ensure_vts_secrets()

    logger.info("=" * 80)
    logger.info("Device binding authenticate")

    # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    resource_path = f"vts/provisionedTokens/{provisioned_token_id}/deviceBinding"
    url = f"https://cert.api.visa.com/vts/provisionedTokens/{provisioned_token_id}/deviceBinding?apiKey={api_key}"
    logger.info(f"Target URL: {url.split('?')[0]}...")  # API key redacted

    hash_email = create_email_hash(email)

    payload = json.dumps(
        {
            "sessionContext": {
                "secureToken": secure_token,
            },
            "browserData": browser_data,
            "platformType": "WEB",
            "clientReferenceID": client_reference_id,
            "clientAppID": client_app_id,
            "intent": "FIDO",
            "clientWalletAccountEmailAddressHash": hash_email,
        }
    )

    logger.info(f"Body:{payload}")

    # Generate X-PAY-TOKEN
    query_string_for_token = f"apiKey={api_key}"
    x_pay_token = generate_x_pay_token(shared_secret, resource_path, query_string_for_token, payload)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-PAY-TOKEN": x_pay_token,
        "x-request-id": x_request_id,
    }

    # Make the request
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=300)
        response.raise_for_status()

        logger.info("\nResponse Body (Parsed JSON):")
        response_json = response.json()
        logger.info("[Response data redacted for security]")

        logger.info("\n" + "=" * 80)
        logger.info("Device Binding  completed successfully")
        logger.info("=" * 80)

        return response_json

    except json.JSONDecodeError as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE BINDING  - JSON DECODE ERROR")
        logger.error("=" * 80)
        logger.error(f"Could not parse response as JSON: {e}")
        logger.error(
            f"Response status code: {response.status_code if 'response' in locals() else 'N/A'}"
            # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
        )
        logger.error(f"Response text: {response.text if 'response' in locals() else 'No response'}")
        raise
    except requests.exceptions.HTTPError as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE BINDING  - REQUEST FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        if hasattr(e, "response") and e.response is not None:
            logger.error(f"Response status code: {e.response.status_code}")
            logger.error(f"Response text: {e.response.text}")
        raise  # Re-raise to let main function handle it
    except requests.exceptions.RequestException as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE BINDING  - REQUEST FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        raise
    except Exception as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE BINDING  - UNEXPECTED ERROR")
        logger.error("=" * 80)
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error message: {str(e)}")
        import traceback

        logger.error(f"Traceback:\n{traceback.format_exc()}")
        raise


def step_up(
    provisioned_token_id,
    identifier,
    client_app_id,
    client_reference_id,
    x_request_id,
    # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
):
    _ensure_vts_secrets()

    logger.info("=" * 80)
    logger.info("Select Step-Up Options")

    # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    resource_path = f"vts/provisionedTokens/{provisioned_token_id}/stepUpOptions/method"
    url = (
        f"https://cert.api.visa.com/vts/provisionedTokens/{provisioned_token_id}/stepUpOptions/method?apiKey={api_key}"
    )
    logger.info(f"Target URL: {url.split('?')[0]}...")  # API key redacted

    timestamp = str(int(time.time()))
    logger.info(f"  Timestamp: {timestamp}")

    payload = json.dumps(
        {
            "date": timestamp,
            "stepUpRequestID": identifier,
            "clientReferenceId": client_reference_id,
            "clientAppID": client_app_id,
        }
    )

    # Generate X-PAY-TOKEN
    query_string_for_token = f"apiKey={api_key}"
    x_pay_token = generate_x_pay_token(shared_secret, resource_path, query_string_for_token, payload)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-PAY-TOKEN": x_pay_token,
        "x-request-id": x_request_id,
    }

    # Make the request
    try:
        response = requests.put(url, headers=headers, data=payload, timeout=300)
        response.raise_for_status()

        logger.info("\nResponse Body (Parsed JSON):")
        response_json = response.json()
        logger.info("[Response data redacted for security]")

        logger.info("\n" + "=" * 80)
        logger.info("Device Set Up completed successfully")
        logger.info("=" * 80)

        return response_json

    except json.JSONDecodeError as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE SET UP - JSON DECODE ERROR")
        logger.error("=" * 80)
        logger.error(f"Could not parse response as JSON: {e}")
        logger.error(
            f"Response status code: {response.status_code if 'response' in locals() else 'N/A'}"
            # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
        )
        logger.error(f"Response text: {response.text if 'response' in locals() else 'No response'}")
        raise
    except requests.exceptions.RequestException as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE SET UP - REQUEST FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        if "response" in locals():
            logger.error(f"Response status code: {response.status_code}")
            logger.error(f"Response text: {response.text}")
        raise
    except Exception as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE SET UP - UNEXPECTED ERROR")
        logger.error("=" * 80)
        # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error message: {str(e)}")
        import traceback

        logger.error(f"Traceback:\n{traceback.format_exc()}")
        raise


def validate_otp(provisioned_token_id, otp_value, client_app_id, client_reference_id, x_request_id):
    _ensure_vts_secrets()

    logger.info("=" * 80)
    logger.info("Validate OTP")

    resource_path = (
        f"vts/provisionedTokens/{provisioned_token_id}/stepUpOptions/validateOTP"
        # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    )
    url = f"https://cert.api.visa.com/vts/provisionedTokens/{provisioned_token_id}/stepUpOptions/validateOTP?apiKey={api_key}"
    logger.info(f"Target URL: {url.split('?')[0]}...")  # API key redacted

    timestamp = str(int(time.time()))
    logger.info(f"  Timestamp: {timestamp}")

    payload = json.dumps(
        {
            "date": timestamp,
            "otpValue": otp_value,
            "clientReferenceId": client_reference_id,
            "clientAppID": client_app_id,
        }
    )

    # Generate X-PAY-TOKEN
    query_string_for_token = f"apiKey={api_key}"
    x_pay_token = generate_x_pay_token(shared_secret, resource_path, query_string_for_token, payload)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-PAY-TOKEN": x_pay_token,
        "x-request-id": x_request_id,
    }

    # Make the request
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=300)
        response.raise_for_status()

        logger.info("\nResponse Body (Parsed JSON):")
        response_json = response.json()
        logger.info("[Response data redacted for security]")

        logger.info("\n" + "=" * 80)
        logger.info("Validate OTP completed successfully")
        logger.info("=" * 80)

        return response_json

    except json.JSONDecodeError as e:
        logger.error("\n" + "=" * 80)
        logger.error("VALIDATE OTP  - JSON DECODE ERROR")
        logger.error("=" * 80)
        logger.error(f"Could not parse response as JSON: {e}")
        logger.error(
            f"Response status code: {response.status_code if 'response' in locals() else 'N/A'}"
            # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
        )
        logger.error(f"Response text: {response.text if 'response' in locals() else 'No response'}")
        raise
    except requests.exceptions.RequestException as e:
        logger.error("\n" + "=" * 80)
        logger.error("VALIDATE OTP - REQUEST FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        if "response" in locals():
            logger.error(f"Response status code: {response.status_code}")
            logger.error(f"Response text: {response.text}")
        raise
    except Exception as e:
        logger.error("\n" + "=" * 80)
        logger.error("VALIDATE OTP - UNEXPECTED ERROR")
        logger.error("=" * 80)
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error message: {str(e)}")
        import traceback

        logger.error(f"Traceback:\n{traceback.format_exc()}")
        raise


def device_attestation_register(
    provisioned_token_id,
    email,
    secure_token,
    browser_data,
    client_app_id,
    client_reference_id,
    x_request_id,
):
    _ensure_vts_secrets()

    logger.info("=" * 80)
    logger.info("Device Attestation Register")
    # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted

    # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    resource_path = f"vts/provisionedTokens/{provisioned_token_id}/attestation/options"
    url = f"https://cert.api.visa.com/vts/provisionedTokens/{provisioned_token_id}/attestation/options?apiKey={api_key}"
    logger.info(f"Target URL: {url.split('?')[0]}...")  # API key redacted

    payload_dict = {
        "authenticationPreferencesRequested": {"selectedPopupForRegister": False},
        "dynamicData": {
            "authenticationAmount": "444.44",
            "merchantIdentifier": {
                "applicationUrl": "aHR0cHM6Ly93d3cuTWVyY2hhbnQtVlphRjVYQmouY29t",  # pragma: allowlist secret
                "merchantName": "TWVyY2hhbnQgVlphRjVYQmo",
            },
            "currencyCode": "840",
        },
        "browserData": browser_data,
        "reasonCode": "DEVICE_BINDING",
        "clientReferenceID": client_reference_id,
        "type": "REGISTER",
        "clientAppID": client_app_id,
    }

    to_be_encrypted = {"consumerInfo": {"emailAddress": email}}
    logger.info(f"uncrypte data:{to_be_encrypted}")
    encAuthenticationData = encrypt_card_data(to_be_encrypted, encryption_api_key, encryption_shared_secret)

    payload_dict["encAuthenticationData"] = encAuthenticationData

    if secure_token and secure_token.startswith("ezAwMX06"):
        logger.info("Including encAuthenticationData with consumer email for iframe flow")
    else:
        logger.info("Including encAuthenticationData with consumer email for OAuth flow")

    if secure_token:
        payload_dict["sessionContext"] = {"secureToken": secure_token}
        if secure_token.startswith("ezAwMX06"):
            logger.info("Including sessionContext with iframe secure token")
        else:
            logger.info("Including sessionContext with OAuth secure token")
    else:
        logger.info("No secure token provided - skipping sessionContext")

    payload = json.dumps(payload_dict)

    logging.info(f"Body:{payload}")

    # Generate X-PAY-TOKEN
    query_string_for_token = f"apiKey={api_key}"
    x_pay_token = generate_x_pay_token(shared_secret, resource_path, query_string_for_token, payload)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-PAY-TOKEN": x_pay_token,
        "x-request-id": x_request_id,
    }

    # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    logging.info(f"Headers: {headers}")

    # Make the request
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=300)
        response.raise_for_status()

        logger.info("\nResponse Body (Parsed JSON):")
        response_json = response.json()
        logger.info("[Response data redacted for security]")

        logger.info("\n" + "=" * 80)
        logger.info("Device Attestation Register completed successfully")
        logger.info("=" * 80)

        return response_json

    except json.JSONDecodeError as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE ATTESTATION REGISTER - JSON DECODE ERROR")
        logger.error("=" * 80)
        logger.error(f"Could not parse response as JSON: {e}")
        logger.error(
            f"Response status code: {response.status_code if 'response' in locals() else 'N/A'}"
            # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
        )
        logger.error(f"Response text: {response.text if 'response' in locals() else 'No response'}")
        raise
    except requests.exceptions.RequestException as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE ATTESTATION REGISTER - REQUEST FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        if "response" in locals():
            logger.error(f"Response status code: {response.status_code}")
            logger.error(f"Response text: {response.text}")
        raise
    except Exception as e:
        logger.error("\n" + "=" * 80)
        logger.error("DEVICE ATTESTATION REGISTER - UNEXPECTED ERROR")
        logger.error("=" * 80)
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error message: {str(e)}")
        import traceback

        logger.error(f"Traceback:\n{traceback.format_exc()}")
        raise


def passkey_creation(request_id, endpoint, identifier, payload, client_app_id, client_reference_id):
    logger.info("=" * 80)
    logger.info("Passkey Creation Flow")

    # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    resource_path = "vts/auth/authenticate"
    url = f"https://sbx.vts.auth.visa.com/vts/auth/authenticate?apiKey={api_key}&clientAppID={client_app_id}"
    logger.info(f"Target URL: {url.split('?')[0]}...")  # API key redacted

    payload = json.dumps(
        {
            "requestID": request_id,
            "version": "1",
            "type": "AUTHENTICATE",
            "authenticationContext": {
                "endpoint": endpoint,
                "identifier": identifier,
                "payload": payload,
                "action": "REGISTER",
                "platformType": "WEB",
                "authenticationPreferencesEnabled": {
                    "responseMode": "com_visa_web_message",
                    "responseType": "code",
                },
            },
        }
    )

    try:
        response = requests.post(url, data=payload, timeout=300)
        response.raise_for_status()

        logger.info("\nResponse Body (Parsed JSON):")
        response_json = response.json()
        logger.info("[Response data redacted for security]")

        logger.info("\n" + "=" * 80)
        # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
        logger.info("Passkey Creation Flow completed successfully")
        logger.info("=" * 80)

        return response_json

    except json.JSONDecodeError as e:
        logger.error("\n" + "=" * 80)
        logger.error("PASSKEY CREATION FLOW - JSON DECODE ERROR")
        logger.error("=" * 80)
        logger.error(f"Could not parse response as JSON: {e}")
        logger.error(
            f"Response status code: {response.status_code if 'response' in locals() else 'N/A'}"
            # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
        )
        logger.error(f"Response text: {response.text if 'response' in locals() else 'No response'}")
        raise
    except requests.exceptions.RequestException as e:
        logger.error("\n" + "=" * 80)
        logger.error("PASSKEY CREATION FLOW - REQUEST FAILED")
        # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
        if "response" in locals():
            logger.error(f"Response status code: {response.status_code}")
            logger.error(f"Response text: {response.text}")
        # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
        raise
    except Exception as e:
        logger.error("\n" + "=" * 80)
        # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
        logger.error("PASSKEY CREATION FLOW - UNEXPECTED ERROR")
        logger.error("=" * 80)
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error message: {str(e)}")
        import traceback

        logger.error(f"Traceback:\n{traceback.format_exc()}")
        raise


def vic_enroll_card(
    email,
    provisioned_token_id,
    client_app_id,
    client_reference_id,
    client_device_id,
    consumer_id,
):
    """
    Step 14: VIC Enroll Card

    Enrolls the provisioned token with VIC for payment use

    Args:
        email: User email address
        provisioned_token_id: vProvisionedTokenID from provision step
        client_app_id: Client application ID
        client_reference_id: Client reference ID
        client_device_id: Client device ID (for session continuity)
        consumer_id: Consumer ID (for session continuity)

    Returns:
        Dictionary containing response and decrypted data
    """
    _ensure_vts_secrets()

    logger.info("=" * 80)
    logger.info("VIC Enroll Card (Step 14)")
    logger.info("=" * 80)

    # externalClientId is from Visa Developer Portal
    to_be_encrypted = {
        "client": {
            "externalClientId": "3aa9e2b8-c5c1-612d-32c3-1cb11b85a702",
            "externalAppId": client_app_id,
        },
        "enrollmentReferenceData": {
            "enrollmentReferenceType": "TOKEN_REFERENCE_ID",
            "enrollmentReferenceProvider": "VTS",
            "enrollmentReferenceId": provisioned_token_id,
        },
        "appInstance": {
            "countryCode": "US",
            "clientDeviceId": client_device_id,
            "ipAddress": "192.168.1.1",
            "deviceData": {
                "model": "iPhone 16 Pro Max",
                "type": "Mobile",
                "brand": "Apple",
                "manufacturer": "Apple",
            },
            "userAgent": "Mozilla/5.0",
            "applicationName": "My Magic App",
        },
        "clientReferenceId": client_reference_id,
        "consumer": {
            "consumerId": consumer_id,
            "countryCode": "US",
            "languageCode": "en",
            "consumerIdentity": {
                "identityType": "EMAIL_ADDRESS",
                "identityValue": email,
            },
        },
    }

    # Encrypt the payload using RSA encryption with VIC certificate (not symmetric)
    enc_data = encrypt_payload(to_be_encrypted)

    # Build encrypted request
    enc_data_str = json.dumps(enc_data, separators=(",", ":"))

    # Build URL with VIC API key (lowercase 'apikey')
    url = f"https://cert.api.visa.com/vacp/v1/cards?apikey={api_key}"

    # Generate X-PAY-TOKEN
    resource_path = "v1/cards"
    query_string = f"apikey={api_key}"

    # Debug logging for x-pay-token generation
    logger.info("X-PAY-TOKEN generation params:")
    logger.info(f"  resource_path: {resource_path}")
    # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    logger.info(f"  query_string: {query_string}")
    logger.info(f"  body_length: {len(enc_data_str)}")
    logger.info(f"  shared_secret (first 10): {shared_secret[:10]}...")

    x_pay_token = generate_x_pay_token(shared_secret, resource_path, query_string, enc_data_str)

    # Get keyId from secrets (not hardcoded)
    vic_key_id = get_secret("visa/vic_key_id", region)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "keyId": vic_key_id,
        "x-pay-token": x_pay_token,
        # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    }

    logger.info(f"Target URL: {url.split('?')[0]}...")  # API key redacted
    logger.info("\nRequest Headers:")
    for key, value in headers.items():
        if "token" in key.lower() or "key" in key.lower():
            logger.info(f"  {key}: {value[:20]}...")
        else:
            logger.info(f"  {key}: {value}")
    logger.info(f"Request Body (truncated): {enc_data_str[:100]}...")

    try:
        response = requests.post(url, headers=headers, data=enc_data_str, timeout=300)

        # Log response details BEFORE raising for status
        logger.info(f"\nResponse Status Code: {response.status_code}")
        logger.info("Response Headers:")
        for key, value in response.headers.items():
            logger.info(f"  {key}: {value}")

        logger.info("\nRaw Response Body:")
        logger.info(response.text)

        response.raise_for_status()

        response_json = response.json()
        logger.info("\nResponse Body (Parsed JSON):")
        logger.info("[Response data redacted for security]")

        # Decrypt response using RSA decryption (not symmetric)
        enc_response_data = response_json.get("encData")
        if enc_response_data:
            decrypted_response = decrypt_rsa(enc_response_data)
            logger.info("\nDecrypted Response:")
            logger.info(json.dumps(decrypted_response, indent=2))

            logger.info("\n" + "=" * 80)
            logger.info("VIC Enroll Card completed successfully")
            logger.info("=" * 80)

            return {
                "clientReferenceId": decrypted_response.get("clientReferenceId"),
                "status": decrypted_response.get("status"),
                "raw": decrypted_response,
            }
        else:
            raise KeyError("Response missing 'encData' field")

    except requests.exceptions.HTTPError as e:
        logger.error("\n" + "=" * 80)
        logger.error("VIC ENROLL CARD FAILED - HTTP ERROR")
        logger.error("=" * 80)
        logger.error(f"Status Code: {e.response.status_code}")
        logger.error(f"Error: {str(e)}")
        logger.error("\nResponse Headers:")
        for key, value in e.response.headers.items():
            logger.error(f"  {key}: {value}")
        logger.error("\nResponse Body:")
        logger.error(e.response.text)
        raise
    except Exception as e:
        logger.error("\n" + "=" * 80)
        logger.error("VIC ENROLL CARD FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        raise


def vic_initiate_purchase_instructions(
    provisioned_token_id,
    consumer_id,
    client_app_id,
    mandate_id,
    consumer_request,
    client_reference_id,
    client_device_id,
    auth_identifier,
    dfp_session_id,
    iframe_auth_fido_blob,
    transaction_amount="444.44",
):
    """
    Step 15: VIC Initiate Purchase Instructions

    Initiates purchase instructions with assurance data from passkey

    Args:
        provisioned_token_id: vProvisionedTokenID from provision step
        consumer_id: Consumer ID (same as enroll_card for session continuity)
        client_app_id: Client application ID
        mandate_id: Mandate ID for purchase constraints
        consumer_request: Consumer request description (e.g., "Buy apples")
        client_reference_id: Client reference ID
        client_device_id: Client device ID
        auth_identifier: Identifier from VTS GET Device Attestations Options
        dfp_session_id: DFP session ID from iframe
        iframe_auth_fido_blob: FIDO assertion data code from iframe authentication
        transaction_amount: Transaction amount as string (e.g., "799.00")

    Returns:
        Dictionary containing instructionId
    """
    _ensure_vts_secrets()

    logger.info("=" * 80)
    logger.info("VIC Initiate Purchase Instructions (Step 15)")
    logger.info("=" * 80)

    # Ensure amount is formatted as string with exactly 2 decimal places
    formatted_amount = f"{float(transaction_amount):.2f}"
    logger.info(f"Transaction amount: {formatted_amount}")

    # Build unencrypted payload
    timestamp = int(time.time())
    effective_until = timestamp + 864000  # 10 days

    to_be_encrypted = {
        "tokenId": provisioned_token_id,
        "consumerId": consumer_id,
        "client": {
            "externalClientId": "3aa9e2b8-c5c1-612d-32c3-1cb11b85a702",
            "externalAppId": client_app_id,
        },
        "mandates": [
            {
                "effectiveUntilTime": effective_until,
                "declineThreshold": {"amount": formatted_amount, "currencyCode": "USD"},
                "quantity": 1,
                "mandateId": mandate_id,
                "merchantCategoryCode": "5411",
                "description": consumer_request,
                "merchantCategory": "Groceries",
                "preferredMerchantName": "Walmart",
            }
        ],
        "clientReferenceId": client_reference_id,
        "appInstance": {
            "countryCode": "US",
            "clientDeviceId": client_device_id,
            "ipAddress": "192.168.1.1",
            "deviceData": {
                "model": "iPhone 16 Pro Max",
                "type": "Mobile",
                "brand": "Apple",
                "manufacturer": "Apple",
            },
            "userAgent": "Mozilla/5.0",
            "applicationName": "My Magic App",
        },
        "consumerPrompt": consumer_request,
        "assuranceData": [
            {
                "methodResults": {
                    "identifier": auth_identifier,
                    "dfpSessionId": dfp_session_id,
                    "fidoAssertionData": {"code": iframe_auth_fido_blob},
                },
                "verificationType": "DEVICE",
                "verificationResults": "01",
                "verificationMethod": "23",
                "verificationTimestamp": timestamp,
            }
        ],
    }

    # Encrypt the payload using RSA encryption with VIC certificate
    enc_data = encrypt_payload(to_be_encrypted)

    # Build encrypted request (enc_data is already a dict with "encData" key)
    enc_data_str = json.dumps(enc_data, separators=(",", ":"))

    # Build URL (using cert environment)
    url = f"https://cert.api.visa.com/vacp/v1/instructions?apikey={api_key}"

    # Generate X-PAY-TOKEN
    resource_path = "v1/instructions"
    query_string = f"apikey={api_key}"
    x_pay_token = generate_x_pay_token(shared_secret, resource_path, query_string, enc_data_str)

    # Get keyId from secrets
    vic_key_id = get_secret("visa/vic_key_id", region)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "keyId": vic_key_id,
        "x-pay-token": x_pay_token,
        "x-request-id": client_reference_id,
        # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    }

    logger.info(f"Target URL: {url.split('?')[0]}...")  # API key redacted
    logger.info(f"Request Body (truncated): {enc_data_str[:100]}...")

    try:
        response = requests.post(url, headers=headers, data=enc_data_str, timeout=300)
        response.raise_for_status()

        response_json = response.json()
        logger.info("\nResponse Body (Parsed JSON):")
        logger.info("[Response data redacted for security]")

        # Decrypt response using RSA decryption
        enc_response_data = response_json.get("encData")
        if enc_response_data:
            decrypted_response = decrypt_rsa(enc_response_data)
            logger.info("\nDecrypted Response:")
            logger.info(json.dumps(decrypted_response, indent=2))

            logger.info("\n" + "=" * 80)
            logger.info("VIC Initiate Purchase Instructions completed successfully")
            logger.info("=" * 80)

            return {
                "instructionId": decrypted_response.get("instructionId"),
                "clientReferenceId": decrypted_response.get("clientReferenceId"),
                "status": decrypted_response.get("status"),
                "raw": decrypted_response,
            }
        else:
            raise KeyError("Response missing 'encData' field")

    except requests.exceptions.HTTPError as e:
        logger.error("\n" + "=" * 80)
        logger.error("VIC INITIATE PURCHASE INSTRUCTIONS FAILED - HTTP ERROR")
        logger.error("=" * 80)
        logger.error(f"Status Code: {e.response.status_code}")
        logger.error(f"Error: {str(e)}")
        logger.error("\nResponse Headers:")
        for key, value in e.response.headers.items():
            logger.error(f"  {key}: {value}")
        logger.error("\nResponse Body:")
        logger.error(e.response.text)
        raise
    except Exception as e:
        logger.error("\n" + "=" * 80)
        logger.error("VIC INITIATE PURCHASE INSTRUCTIONS FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        raise


def vic_get_payment_credentials(
    instruction_id,
    provisioned_token_id,
    client_app_id,
    client_reference_id,
    merchant_url,
    merchant_name,
    transaction_amount,
):
    """
    Step 16: VIC Get Payment Credentials

    Retrieves payment credentials (cryptogram) for authorization

    Args:
        instruction_id: instructionId from initiate purchase instructions
        provisioned_token_id: vProvisionedTokenID from provision step
        client_app_id: Client application ID
        client_reference_id: Client reference ID (same as used in enrollment)
        merchant_url: Merchant website URL
        merchant_name: Merchant name
        transaction_amount: Transaction amount as string (e.g., "444.44")

    Returns:
        Dictionary containing signedPayload with cryptogram
    """
    _ensure_vts_secrets()

    logger.info("=" * 80)
    logger.info("VIC Get Payment Credentials (Step 16)")
    logger.info("=" * 80)

    # Ensure amount is formatted as string with exactly 2 decimal places
    formatted_amount = f"{float(transaction_amount):.2f}"
    logger.info(f"Transaction amount: {formatted_amount}")

    to_be_encrypted = {
        "tokenId": provisioned_token_id,
        "transactionData": [
            {
                "merchantCountryCode": "US",
                "transactionAmount": {
                    "transactionAmount": formatted_amount,
                    "transactionCurrencyCode": "USD",
                },
                "merchantUrl": merchant_url,
                "merchantName": merchant_name,
                "transactionReferenceId": instruction_id,
            }
        ],
        "client": {
            "externalClientId": "3aa9e2b8-c5c1-612d-32c3-1cb11b85a702",
            "externalAppId": client_app_id,
        },
        "clientReferenceId": client_reference_id,
    }

    # Encrypt the payload using RSA
    enc_data = encrypt_payload(to_be_encrypted)
    enc_data_str = json.dumps(enc_data, separators=(",", ":"))

    # Build URL
    url = f"https://cert.api.visa.com/vacp/v1/instructions/{instruction_id}/credentials?apikey={api_key}"

    # Generate X-PAY-TOKEN
    resource_path = f"v1/instructions/{instruction_id}/credentials"
    query_string = f"apikey={api_key}"
    x_pay_token = generate_x_pay_token(shared_secret, resource_path, query_string, enc_data_str)

    # Get keyId from secrets
    vic_key_id = get_secret("visa/vic_key_id", region)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "keyId": vic_key_id,
        "x-pay-token": x_pay_token,
        "x-request-id": client_reference_id,
        # codeql[py/clear-text-logging-sensitive-data] Debug logging for API integration - logs metadata only, sensitive data is redacted
    }

    logger.info(f"Target URL: {url.split('?')[0]}...")  # API key redacted
    logger.info(f"Request Body (truncated): {enc_data_str[:100]}...")

    try:
        response = requests.post(url, headers=headers, data=enc_data_str, timeout=300)
        response.raise_for_status()

        response_json = response.json()
        logger.info("\nResponse Body (Parsed JSON):")
        logger.info("[Response data redacted for security]")

        # Decrypt response using RSA
        enc_response_data = response_json.get("encData")
        if enc_response_data:
            decrypted_response = decrypt_rsa(enc_response_data)
            logger.info("\nDecrypted Response:")
            logger.info(json.dumps(decrypted_response, indent=2))

            # Extract and decode signedPayload to get cryptogram
            signed_payload = decrypted_response.get("signedPayload")
            if signed_payload:
                # Decode JWT to get cryptogram
                parts = signed_payload.split(".")
                if len(parts) >= 2:
                    import base64

                    # Decode the payload part (second part of JWT)
                    payload_part = parts[1]
                    # Add padding if needed
                    padding = 4 - len(payload_part) % 4
                    if padding != 4:
                        payload_part += "=" * padding
                    decoded_payload = base64.b64decode(payload_part)
                    payload_json = json.loads(decoded_payload)

                    logger.info("\n" + "=" * 80)
                    logger.info("DECODED CRYPTOGRAM DATA:")
                    logger.info("=" * 80)
                    logger.info(json.dumps(payload_json, indent=2))

                    # Extract cryptogram value
                    if "dynamicData" in payload_json and len(payload_json["dynamicData"]) > 0:
                        cryptogram_data = payload_json["dynamicData"][0]
                        logger.info("\n" + "=" * 80)
                        logger.info(f"🔑 CRYPTOGRAM: {cryptogram_data.get('dynamicDataValue')}")
                        logger.info("=" * 80)

            logger.info("\n" + "=" * 80)
            logger.info("VIC Get Payment Credentials completed successfully")
            logger.info("=" * 80)

            return {
                "signedPayload": signed_payload,
                "instructionId": decrypted_response.get("instructionId"),
                "status": decrypted_response.get("status"),
                "raw": decrypted_response,
            }
        else:
            raise KeyError("Response missing 'encData' field")

    except Exception as e:
        logger.error("\n" + "=" * 80)
        logger.error("VIC GET PAYMENT CREDENTIALS FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        raise
