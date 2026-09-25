# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
import base64
import hmac
import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

EXPECTED_HEADER = os.environ["ORIGIN_VERIFY_HEADER"]

# The expected value is fetched from Secrets Manager by ARN at runtime so the
# plaintext secret never lands in a Lambda environment variable (visible to any
# principal with lambda:GetFunctionConfiguration). ORIGIN_VERIFY_VALUE remains
# an optional plaintext override used by unit tests / local runs only.
_SECRET_ARN = os.environ.get("ORIGIN_VERIFY_SECRET_ARN", "")
_expected_value_cache = os.environ.get("ORIGIN_VERIFY_VALUE") or None


def _expected_value():
    """Return the origin-verify secret, fetching once per warm container."""
    global _expected_value_cache
    if _expected_value_cache is None:
        import boto3  # lazy: keeps the module import-time offline for tests

        client = boto3.client("secretsmanager")
        _expected_value_cache = client.get_secret_value(SecretId=_SECRET_ARN)[
            "SecretString"
        ]
    return _expected_value_cache


def _header(headers, name):
    """Case-insensitive header lookup (gateway preserves original casing)."""
    if not headers:
        return ""
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return ""


def _verified(gateway_request):
    # Constant-time compare to avoid leaking the secret via timing.
    supplied = _header(gateway_request.get("headers", {}), EXPECTED_HEADER)
    return hmac.compare_digest(supplied, _expected_value())


def _handle_mcp(mcp_data):
    """MCP targets: parsed-JSON body; reject with a JSON-RPC error."""
    gateway_request = mcp_data.get("gatewayRequest", {})
    request_body = gateway_request.get("body", {})

    if not _verified(gateway_request):
        logger.warning("Origin verification failed (mcp)")
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayResponse": {
                    "statusCode": 403,
                    "body": {
                        "jsonrpc": "2.0",
                        "id": request_body.get("id"),
                        "error": {"code": -32600, "message": "Forbidden"},
                    },
                }
            },
        }

    logger.info(
        "Verified request (mcp) — method: %s", request_body.get("method", "unknown")
    )
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"body": request_body}},
    }


def _handle_http(http_data):
    """HTTP + inference targets: base64 body; reject with a JSON 403 body."""
    gateway_request = http_data.get("gatewayRequest", {})

    if not _verified(gateway_request):
        logger.warning("Origin verification failed (http)")
        body = base64.b64encode(
            json.dumps({"message": "Forbidden"}).encode("utf-8")
        ).decode("utf-8")
        return {
            "interceptorOutputVersion": "1.0",
            "http": {
                "transformedGatewayResponse": {
                    "statusCode": 403,
                    "contentType": "application/json",
                    "body": body,
                }
            },
        }

    logger.info(
        "Verified request (http) — path: %s", gateway_request.get("path", "unknown")
    )
    # Pass through unchanged.
    return {"interceptorOutputVersion": "1.0", "http": {}}


def lambda_handler(event, context):
    """
    AgentCore Gateway REQUEST interceptor that validates the custom origin
    header added by CloudFront, rejecting requests that bypass CloudFront
    with a 403. Works for every target type on the gateway: MCP targets use
    the `mcp` envelope, while HTTP-passthrough (AgentCore Runtime) and
    inference targets share the `http` envelope.

    Origin verification is a per-gateway toggle; when enabled it applies to
    every endpoint on that gateway. Configure this Lambda as a REQUEST
    interceptor with `passRequestHeaders` enabled (the header is required to
    validate the request).
    """
    if "mcp" in event:
        return _handle_mcp(event.get("mcp") or {})
    if "http" in event:
        return _handle_http(event.get("http") or {})

    # Unknown envelope — fail closed.
    logger.warning("Unknown interceptor payload; no mcp/http envelope")
    return {
        "interceptorOutputVersion": "1.0",
        "http": {
            "transformedGatewayResponse": {
                "statusCode": 403,
                "contentType": "application/json",
                "body": base64.b64encode(b'{"message":"Forbidden"}').decode("utf-8"),
            }
        },
    }
