import boto3
import json
import time
import hashlib
import hmac
import logging
import base64
import ntplib
import uuid
from datetime import datetime, timezone
from jwcrypto import jwk, jwe


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _redact_sensitive(value: str, show_chars: int = 4) -> str:
    """Redact sensitive information, showing only first few characters."""
    if not value or not isinstance(value, str):
        return "[REDACTED]"
    if len(value) <= show_chars:
        return "[REDACTED]"
    return f"{value[:show_chars]}...{'*' * 8}"


def get_secret(secret_name, region_name="us-east-1"):
    # codeql[py/clear-text-logging-sensitive-data] Only logs secret name for debugging, not the actual secret value
    logger.info(f"Fetching secret: {secret_name} from region: {region_name}")
    client = boto3.client("secretsmanager", region_name=region_name)
    response = client.get_secret_value(SecretId=secret_name)
    secret_value = response["SecretString"]

    # For PEM files (certificates/keys), replace common escape sequences if accidentally stored
    if "cert" in secret_name.lower() or "key" in secret_name.lower() or "pem" in secret_name.lower():
        # Replace literal \n with actual newlines
        # codeql[py/clear-text-logging-sensitive-data] Logs processing status only, not secret content
        if "\\n" in secret_value:
            logger.info(f"  Processing {secret_name}...")
            secret_value = secret_value.replace("\\n", "\n")
            # codeql[py/clear-text-logging-sensitive-data] Logs processing status only, not secret content
        # Replace literal \r with actual carriage returns
        # codeql[py/clear-text-logging-sensitive-data] Logs processing status only, not secret content
        if "\\r" in secret_value:
            logger.info(f"  Processing {secret_name}...")
            # codeql[py/clear-text-logging-sensitive-data] Logs processing status only, not secret content
            secret_value = secret_value.replace("\\r", "\r")
        # Remove any quotes that might wrap the entire PEM
        if secret_value.startswith('"') and secret_value.endswith('"'):
            logger.info(f"  Processing {secret_name}...")
            secret_value = secret_value[1:-1]
        if secret_value.startswith("'") and secret_value.endswith("'"):
            logger.info(f"  Processing {secret_name}...")
            secret_value = secret_value[1:-1]
    else:
        # For non-PEM secrets, strip whitespace and quotes
        secret_value = secret_value.strip().strip('"')

    logger.info(f"Successfully retrieved secret: {secret_name}")
    return secret_value


def generate_x_pay_token(shared_secret, resource_path, query_string, request_body=""):
    logger.info("Generating X-PAY-TOKEN")
    # Sensitive data redacted from logs for security

    timestamp = str(int(time.time()))

    message = timestamp + resource_path + query_string + request_body

    # SHA256 is mandated by Visa API specification - cannot use stronger algorithm
    # See: https://developer.visa.com/pages/working-with-visa-apis/two-way-ssl
    hmac_digest = hmac.new(
        shared_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,  # nosec - Required by Visa API
    ).hexdigest()

    token = f"xv2:{timestamp}:{hmac_digest}"
    logger.info("X-PAY-TOKEN generated successfully")
    return token


def get_ntp_time():
    """
    Get current time from US NTP server

    Returns:
        Timestamp in seconds (not milliseconds)
    """
    ntp_servers = [
        "time.nist.gov",
        "time-a-g.nist.gov",
        "time-b-g.nist.gov",
        "time.google.com",
    ]

    client = ntplib.NTPClient()

    for ntp_server in ntp_servers:
        try:
            logger.info(f"  Querying NTP server: {ntp_server}")
            response = client.request(ntp_server, version=3, timeout=5)
            ntp_time_seconds = int(response.tx_time)
            _ntp_time_readable = datetime.fromtimestamp(response.tx_time, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
            logger.info(f"  NTP time retrieved successfully from {ntp_server}")
            return ntp_time_seconds
        except Exception as e:
            logger.warning(f"  Failed to get time from {ntp_server}: {str(e)}")
            continue

    # Fallback to system time if all NTP servers fail
    logger.warning("  All NTP servers failed, falling back to system UTC time")
    fallback_time_seconds = int(datetime.now(timezone.utc).timestamp())
    return fallback_time_seconds


def encrypt_card_data(payload, encryption_api_key, encryption_shared_secret):
    """
    Encrypt card data using JWE with A256GCMKW (AES-256-GCM Key Wrap)

    Args:
        payload: Dictionary containing card data to encrypt
        encryption_api_key: API key (used as 'kid' in header)
        encryption_shared_secret: Shared secret for symmetric encryption

    Returns:
        Encrypted JWE token string
    """
    # Step 1: Convert payload to JSON string
    payload_json = json.dumps(payload)

    # Step 2: Get timestamp
    iat_utc = get_ntp_time()

    # Step 3: Create symmetric key from shared secret
    key_bytes = hashlib.sha256(
        encryption_shared_secret.encode("utf-8")
    ).digest()  # CodeQL[py/weak-cryptographic-algorithm] SHA256 is mandated by Visa API specification

    # Create JWK object for symmetric key
    symmetric_key = jwk.JWK(kty="oct", k=base64.urlsafe_b64encode(key_bytes).decode("utf-8").rstrip("="))

    # Step 4: Create protected header
    protected_header = {
        "alg": "A256GCMKW",
        "enc": "A256GCM",
        "kid": encryption_api_key,
        "channelSecurityContext": "SHARED_SECRET",
        "iat": str(iat_utc),
    }

    # Step 5: Create JWE token
    jwetoken = jwe.JWE(
        plaintext=payload_json.encode("utf-8"),
        recipient=symmetric_key,
        protected=protected_header,
    )

    # Step 6: Serialize to compact format
    encrypted_payload = jwetoken.serialize(compact=True)

    return encrypted_payload


def decrypt_token_info(encrypted_jwe, encryption_shared_secret):
    """
    Decrypt JWE token from Visa API response

    Args:
        encrypted_jwe: The encrypted JWE token string (encTokenInfo)
        encryption_shared_secret: Shared secret for symmetric decryption

    Returns:
        Decrypted data as dictionary
    """
    # Step 1: Create symmetric key from shared secret
    key_bytes = hashlib.sha256(
        encryption_shared_secret.encode("utf-8")
    ).digest()  # CodeQL[py/weak-cryptographic-algorithm] SHA256 is mandated by Visa API specification
    symmetric_key = jwk.JWK(kty="oct", k=base64.urlsafe_b64encode(key_bytes).decode("utf-8").rstrip("="))

    # Step 2: Deserialize and decrypt the JWE token
    jwetoken = jwe.JWE()
    jwetoken.deserialize(encrypted_jwe, key=symmetric_key)

    # Step 3: Extract and parse the decrypted payload
    decrypted_payload = jwetoken.payload.decode("utf-8")
    decrypted_data = json.loads(decrypted_payload)

    return decrypted_data


def create_email_hash(email):
    """Create a compliant email hash for Visa API"""
    email_hash = hashlib.sha256(
        email.lower().encode("utf-8")
    ).digest()  # CodeQL[py/weak-cryptographic-algorithm] SHA256 is mandated by Visa API specification
    url_safe = base64.urlsafe_b64encode(email_hash).decode("utf-8").rstrip("=")
    return url_safe[:48]


def generate_client_reference_id():
    """
    Generate a random client reference ID

    Returns:
        str: Random UUID string
    """
    return str(uuid.uuid4())


def loadPem(pem_data):
    """Load a PEM certificate/key into a JWK object"""
    if isinstance(pem_data, str):
        pem_data = pem_data.encode("utf-8")
    return jwk.JWK.from_pem(pem_data)


def encrypt_payload(
    payload,
    server_cert_secret_name="visa/server-mle-cert",
    region="us-east-1",
    key_id=None,
):  # pragma: allowlist secret
    """
    Encrypt payload using RSA-OAEP-256 with VIC server certificate from Secrets Manager

    This is used for VIC API calls (vacp/v1/cards) which require RSA encryption,
    unlike VTS API calls which use symmetric encryption (A256GCMKW).

    Args:
        payload: Dictionary or string to encrypt
        server_cert_secret_name: Name of the secret containing the server certificate
        region: AWS region for Secrets Manager
        key_id: The keyId to use in the JWE header (if None, fetches from visa/vic_key_id)

    Returns:
        Dictionary with {"encData": encrypted_jwe}
    """
    # Convert payload dict to JSON string if needed
    if isinstance(payload, dict):
        payload = json.dumps(payload)

    # Get certificate from Secrets Manager
    server_cert = get_secret(server_cert_secret_name, region)

    # Get keyId from Secrets Manager if not provided
    if key_id is None:
        key_id = get_secret("visa/vic_key_id", region)

    protected_header = {
        "alg": "RSA-OAEP-256",
        "enc": "A128GCM",
        "kid": key_id,
        "iat": int(round(time.time() * 1000)),
    }
    jwetoken = jwe.JWE(
        payload.encode("utf-8"),
        recipient=loadPem(server_cert),
        protected=protected_header,
    )

    return {"encData": jwetoken.serialize(compact=True)}


def decrypt_rsa(
    encrypted_jwe, private_key_secret_name="visa/mle-private-cert", region="us-east-1"
):  # pragma: allowlist secret
    """
    Decrypt JWE token using RSA private key from Secrets Manager

    This is used for VIC API responses which use RSA encryption.

    Args:
        encrypted_jwe: The encrypted JWE token string
        private_key_secret_name: Name of the secret containing the private key
        region: AWS region for Secrets Manager

    Returns:
        Decrypted data as dictionary
    """
    # Get private key from Secrets Manager
    private_key_pem = get_secret(private_key_secret_name, region)
    private_key = loadPem(private_key_pem)

    # Deserialize and decrypt the JWE token
    jwetoken = jwe.JWE()
    jwetoken.deserialize(encrypted_jwe, key=private_key)

    # Extract and parse the decrypted payload
    decrypted_payload = jwetoken.payload.decode("utf-8")
    decrypted_data = json.loads(decrypted_payload)

    logger.info(f"Decrypted data: {decrypted_data}")

    return decrypted_data
