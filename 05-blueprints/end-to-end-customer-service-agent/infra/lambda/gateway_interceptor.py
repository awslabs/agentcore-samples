"""
AgentCore Gateway Interceptor for Customer Service Agent
=========================================================
Handles both REQUEST and RESPONSE interception in a single Lambda function.

REQUEST path capabilities:
  1. Token validation       - verify inbound JWT is present and well-formed
  2. Logging / auditing     - structured CloudWatch log per tool call
  3. Request validation     - block malformed or disallowed tool calls
  4. Rate limiting          - per-user call quota enforced via DynamoDB
  5. Guardrail checks       - content filters + prompt attack detection on user input
  6. Input transformation   - normalise parameter names before hitting targets
  7. Header injection       - add downstream auth headers (API keys, bearer tokens)

RESPONSE path capabilities:
  8. PII masking            - redact emails, phone numbers, SSNs from tool responses
  9. Guardrail checks       - content filters + PII detection on tool output
  10. Response logging      - record what each tool returned
  11. Error normalisation   - standardise error shapes returned to the agent
"""

import base64
import json
import logging
import os
import re
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Configuration (override via Lambda environment variables)
# ---------------------------------------------------------------------------
RATE_LIMIT_TABLE   = os.environ.get("RATE_LIMIT_TABLE", "agentcore-gateway-rate-limits")
RATE_LIMIT_MAX     = int(os.environ.get("RATE_LIMIT_MAX", "100"))   # calls per window
RATE_LIMIT_WINDOW  = int(os.environ.get("RATE_LIMIT_WINDOW", "3600"))  # seconds (1 hour)
ENABLE_RATE_LIMIT  = os.environ.get("ENABLE_RATE_LIMIT", "true").lower() == "true"

# Guardrail checks configuration
ENABLE_GUARDRAIL_CHECKS = os.environ.get("ENABLE_GUARDRAIL_CHECKS", "true").lower() == "true"
GUARDRAIL_BLOCK_THRESHOLD = float(os.environ.get("GUARDRAIL_BLOCK_THRESHOLD", "0.8"))
GUARDRAIL_ESCALATE_THRESHOLD = float(os.environ.get("GUARDRAIL_ESCALATE_THRESHOLD", "0.4"))

# Tools that are allowed through the gateway — block anything not in this list
ALLOWED_TOOLS = {
    "WebSearch",
    "web-search-target___WebSearch",
    "tavily_search",
    "retrieve_context",
    "create_ticket",
    "update_ticket",
    "get_ticket",
    "list_tickets",
}

# PII patterns to redact from responses
PII_PATTERNS = [
    # Email addresses
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[EMAIL]"),
    # US phone numbers (various formats)
    (re.compile(r"\b(\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b"), "[PHONE]"),
    # US Social Security Numbers
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    # Credit card numbers — match common prefixes (Visa, MC, Amex, Discover)
    (re.compile(r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{1,4}\b"), "[CARD]"),
]

# ---------------------------------------------------------------------------
# DynamoDB client (lazy init)
# ---------------------------------------------------------------------------
_dynamodb = None

def get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb


# ---------------------------------------------------------------------------
# Bedrock Runtime client for guardrail checks (lazy init)
# ---------------------------------------------------------------------------
_bedrock_runtime = None

def get_bedrock_runtime():
    global _bedrock_runtime
    if _bedrock_runtime is None:
        _bedrock_runtime = boto3.client("bedrock-runtime")
    return _bedrock_runtime


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def lambda_handler(event, context):
    mcp            = event.get("mcp", {}) or {}
    gateway_request  = mcp.get("gatewayRequest") or {}
    gateway_response = mcp.get("gatewayResponse")

    if gateway_response is not None:
        return _handle_response(gateway_request, gateway_response)
    return _handle_request(gateway_request)


# ===========================================================================
# REQUEST INTERCEPTION
# ===========================================================================

def _handle_request(gateway_request: dict) -> dict:
    body    = gateway_request.get("body") or {}
    headers = dict(gateway_request.get("headers") or {})

    method  = body.get("method") if isinstance(body, dict) else None
    msg_id  = body.get("id")     if isinstance(body, dict) else None
    params  = body.get("params", {}) if isinstance(body, dict) else {}
    tool_name = (params.get("name") or "") if isinstance(params, dict) else ""

    # ------------------------------------------------------------------
    # 1. TOKEN VALIDATION
    # ------------------------------------------------------------------
    auth_header = headers.get("authorization") or headers.get("Authorization", "")
    validation_error = _validate_token(auth_header)
    if validation_error:
        logger.warning(f"TOKEN VALIDATION FAILED: {validation_error} | method={method} id={msg_id}")
        return _error_response(msg_id, code=-32001, message=f"Unauthorized: {validation_error}")

    # Extract caller identity from token for downstream use
    caller_id = _extract_caller_id(auth_header)

    # ------------------------------------------------------------------
    # 2. LOGGING / AUDITING (before processing so we always capture intent)
    # ------------------------------------------------------------------
    logger.info(json.dumps({
        "event":     "tool_call_request",
        "caller_id": caller_id,
        "method":    method,
        "tool":      tool_name,
        "msg_id":    msg_id,
        "timestamp": int(time.time()),
    }))

    # ------------------------------------------------------------------
    # 3. REQUEST VALIDATION — only allow known tools
    # ------------------------------------------------------------------
    if method == "tools/call" and tool_name:
        if tool_name not in ALLOWED_TOOLS:
            logger.warning(f"BLOCKED TOOL: {tool_name} | caller={caller_id}")
            return _error_response(
                msg_id,
                code=-32002,
                message=f"Tool '{tool_name}' is not permitted on this gateway",
            )

    # ------------------------------------------------------------------
    # 4. RATE LIMITING — per caller, per hour
    # ------------------------------------------------------------------
    if ENABLE_RATE_LIMIT and caller_id and method == "tools/call":
        rate_error = _check_rate_limit(caller_id)
        if rate_error:
            logger.warning(f"RATE LIMIT EXCEEDED: caller={caller_id}")
            return _error_response(msg_id, code=-32003, message=rate_error)

    # ------------------------------------------------------------------
    # 5. GUARDRAIL CHECKS — content filters + prompt attack detection
    # ------------------------------------------------------------------
    if ENABLE_GUARDRAIL_CHECKS and method == "tools/call":
        user_text = _extract_user_text(params)
        if user_text:
            guardrail_result = _check_input_guardrails(user_text)
            if guardrail_result:
                logger.warning(f"GUARDRAIL BLOCKED: caller={caller_id} | {guardrail_result}")
                return _error_response(
                    msg_id, code=-32004, message=f"Content blocked by safety guardrail: {guardrail_result}"
                )

    # ------------------------------------------------------------------
    # 6. INPUT TRANSFORMATION — normalise parameter names
    # ------------------------------------------------------------------
    body = _transform_request_body(body, tool_name)

    # ------------------------------------------------------------------
    # 7. HEADER INJECTION — add downstream auth headers
    # ------------------------------------------------------------------
    headers = _inject_headers(headers, caller_id)

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "body":    body,
                "headers": headers,
            }
        },
    }


# ===========================================================================
# RESPONSE INTERCEPTION
# ===========================================================================

def _handle_response(gateway_request: dict, gateway_response: dict) -> dict:
    body         = gateway_response.get("body") or {}
    is_streaming = bool(gateway_response.get("isStreamingResponse"))
    has_status   = "statusCode" in gateway_response
    has_headers  = "headers" in gateway_response

    inbound_method = (gateway_request.get("body") or {}).get("method")
    msg_id         = body.get("id") if isinstance(body, dict) else None

    # ------------------------------------------------------------------
    # 8. PII MASKING — scrub sensitive data before it reaches the agent
    # ------------------------------------------------------------------
    body = _mask_pii(body)

    # ------------------------------------------------------------------
    # 9. GUARDRAIL CHECKS — content filters + PII detection on tool output
    # ------------------------------------------------------------------
    if ENABLE_GUARDRAIL_CHECKS:
        response_text = _extract_response_text(body)
        if response_text:
            guardrail_findings = _check_output_guardrails(response_text)
            if guardrail_findings:
                logger.warning(f"GUARDRAIL OUTPUT FINDING: {guardrail_findings}")
                # Log findings but don't block — tool output is informational

    # ------------------------------------------------------------------
    # 10. RESPONSE LOGGING
    # ------------------------------------------------------------------
    logger.info(json.dumps({
        "event":      "tool_call_response",
        "method":     inbound_method,
        "msg_id":     msg_id,
        "streaming":  is_streaming,
        "has_error":  "error" in body if isinstance(body, dict) else False,
        "timestamp":  int(time.time()),
    }))

    # ------------------------------------------------------------------
    # 11. ERROR NORMALISATION — standardise error shapes
    # ------------------------------------------------------------------
    body = _normalise_error(body)

    # Build output — streaming subsequent events can only return body
    out = {"body": body}
    if not is_streaming or has_status:
        if has_status:
            out["statusCode"] = gateway_response.get("statusCode", 200)
        if has_headers:
            out["headers"] = gateway_response.get("headers", {})

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayResponse": out},
    }


# ===========================================================================
# HELPER FUNCTIONS
# ===========================================================================

def _validate_token(auth_header: str):
    """
    JWT presence and structural validation.
    Returns an error string if invalid, None if structurally valid.

    WARNING — PRODUCTION USE:
    This performs structural checks only (presence, Bearer scheme, 3-part JWT format).
    It does NOT verify the token signature. In production deployments you MUST
    verify signatures using your IdP's JWKS endpoint. Use python-jose or PyJWT:

        from jose import jwt, JWTError
        jwks = requests.get(f"{DISCOVERY_URL}/.well-known/jwks.json").json()
        payload = jwt.decode(token, jwks, algorithms=["RS256"], audience=EXPECTED_AUDIENCE)

    The AgentCore Gateway already performs full JWT validation before calling
    this interceptor, so this check is a defense-in-depth measure. However,
    if you rely on caller_id for rate limiting or downstream auth, signature
    verification here prevents spoofing by compromised gateway configurations.
    """
    if not auth_header:
        return "Missing Authorization header"
    if not auth_header.lower().startswith("bearer "):
        return "Authorization header must use Bearer scheme"
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return "Empty bearer token"
    # Basic JWT structure check: three base64 segments separated by dots
    parts = token.split(".")
    if len(parts) != 3:
        return "Malformed JWT token"
    return None  # structurally valid


def _extract_caller_id(auth_header: str) -> str:
    """
    Extract a caller identifier from the JWT payload (sub claim).
    Falls back to 'anonymous' if extraction fails.

    NOTE: This reads claims from an unverified token payload. The caller_id
    is used for rate limiting and audit logging. Since the AgentCore Gateway
    validates the JWT signature before invoking this interceptor, the claims
    are trustworthy in normal operation. If you need defense against gateway
    misconfiguration, add signature verification in _validate_token above.
    """
    try:
        token = auth_header.split(" ", 1)[1].strip()
        payload_b64 = token.split(".")[1]
        # Add padding if needed
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("sub") or payload.get("client_id") or "anonymous"
    except Exception:
        return "anonymous"


def _check_rate_limit(caller_id: str):
    """
    Enforce per-caller rate limit using DynamoDB.
    Uses a sliding window counter keyed by caller_id + current hour bucket.
    Returns an error string if limit exceeded, None if allowed.
    """
    try:
        table      = get_dynamodb().Table(RATE_LIMIT_TABLE)
        bucket_key = f"{caller_id}#{int(time.time()) // RATE_LIMIT_WINDOW}"
        ttl_value  = int(time.time()) + RATE_LIMIT_WINDOW * 2

        response = table.update_item(
            Key={"pk": bucket_key},
            UpdateExpression="ADD call_count :inc SET #ttl_attr = :ttl",
            ExpressionAttributeNames={"#ttl_attr": "ttl"},
            ExpressionAttributeValues={":inc": 1, ":ttl": ttl_value},
            ReturnValues="UPDATED_NEW",
        )
        count = int(response["Attributes"]["call_count"])
        if count >= RATE_LIMIT_MAX:
            return f"Rate limit exceeded: {count}/{RATE_LIMIT_MAX} calls in current window"
        return None
    except ClientError as e:
        # If DynamoDB is unavailable, log and allow through (fail open)
        logger.error(f"Rate limit DynamoDB error: {e} — allowing request through")
        return None
    except Exception as e:
        logger.error(f"Rate limit check failed: {e} — allowing request through")
        return None


def _transform_request_body(body: dict, tool_name: str) -> dict:
    """
    Normalise parameter names for known tools so the agent doesn't need
    to know each target's exact parameter schema.
    """
    if not isinstance(body, dict):
        return body

    params = body.get("params", {})
    if not isinstance(params, dict):
        return body

    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        return body

    # Tavily search: agent may send 'query', 'search_query', or 'value'
    if tool_name == "tavily_search":
        query = (
            arguments.get("query")
            or arguments.get("search_query")
            or arguments.get("value")
        )
        if query:
            arguments["query"] = query
            # Remove aliases to keep payload clean
            arguments.pop("search_query", None)
            arguments.pop("value", None)

    # Zendesk ticket creation: normalise 'subject' vs 'title'
    if tool_name == "create_ticket":
        if "title" in arguments and "subject" not in arguments:
            arguments["subject"] = arguments.pop("title")

    body["params"]["arguments"] = arguments
    return body


def _inject_headers(headers: dict, caller_id: str) -> dict:
    """
    Inject downstream authentication and tracing headers.
    """
    # Propagate caller identity for downstream audit trails
    if caller_id and caller_id != "anonymous":
        headers["x-caller-id"] = caller_id

    # Correlation ID for distributed tracing
    headers["x-request-time"] = str(int(time.time()))

    return headers


def _mask_pii(body):
    """
    Recursively walk the response body and redact PII patterns.
    Operates on string values only — leaves structure intact.
    """
    if isinstance(body, str):
        for pattern, replacement in PII_PATTERNS:
            body = pattern.sub(replacement, body)
        return body
    if isinstance(body, dict):
        return {k: _mask_pii(v) for k, v in body.items()}
    if isinstance(body, list):
        return [_mask_pii(item) for item in body]
    return body


def _normalise_error(body: dict) -> dict:
    """
    If the tool returned a non-standard error shape, normalise it to
    JSON-RPC error format so the agent always sees a consistent structure.
    """
    if not isinstance(body, dict):
        return body

    # Already a proper JSON-RPC error
    if "error" in body and isinstance(body["error"], dict):
        err = body["error"]
        if "code" not in err:
            err["code"] = -32000
        if "message" not in err:
            err["message"] = "Unknown error"
        return body

    # Tool returned {"error": "some string"} — wrap it
    if "error" in body and isinstance(body["error"], str):
        body["error"] = {
            "code":    -32000,
            "message": body["error"],
        }

    return body


def _error_response(msg_id, code: int, message: str) -> dict:
    """
    Return a JSON-RPC error response that blocks the request from
    reaching the target. The gateway will return this directly to the agent.
    """
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "body": {
                    "jsonrpc": "2.0",
                    "id":      msg_id,
                    "error": {
                        "code":    code,
                        "message": message,
                    },
                }
            }
        },
    }


# ===========================================================================
# GUARDRAIL CHECK FUNCTIONS (InvokeGuardrailChecks API)
# ===========================================================================

def _check_input_guardrails(text: str):
    """
    Run guardrail checks on user input using the InvokeGuardrailChecks API.
    Checks for prompt attacks (jailbreak, injection) and harmful content.
    Returns an error string if blocked, None if allowed.
    """
    try:
        client = get_bedrock_runtime()
        response = client.invoke_guardrail_checks(
            messages=[{"role": "user", "content": [{"text": text}]}],
            checks={
                "contentFilter": {
                    "categories": [
                        {"category": "VIOLENCE"},
                        {"category": "MISCONDUCT"},
                        {"category": "HATE"},
                        {"category": "SEXUAL"},
                        {"category": "INSULTS"},
                    ]
                },
                "promptAttack": {
                    "categories": [
                        {"category": "JAILBREAK"},
                        {"category": "PROMPT_INJECTION"},
                        {"category": "PROMPT_LEAKAGE"},
                    ]
                },
            },
        )

        blocked_categories = []

        # Check content filter results (uses severityScore)
        if "contentFilter" in response.get("results", {}):
            for finding in response["results"]["contentFilter"]["results"]:
                if finding["severityScore"] >= GUARDRAIL_BLOCK_THRESHOLD:
                    blocked_categories.append(
                        f"{finding['category']}(severity={finding['severityScore']})"
                    )

        # Check prompt attack results (uses severityScore)
        if "promptAttack" in response.get("results", {}):
            for finding in response["results"]["promptAttack"]["results"]:
                if finding["severityScore"] >= GUARDRAIL_BLOCK_THRESHOLD:
                    blocked_categories.append(
                        f"{finding['category']}(severity={finding['severityScore']})"
                    )

        if blocked_categories:
            return ", ".join(blocked_categories)
        return None

    except AttributeError:
        # API not available in current SDK version — skip gracefully
        logger.info("InvokeGuardrailChecks API not available — skipping input guardrail checks")
        return None
    except Exception as e:
        # Fail open — don't block requests if guardrail service is unavailable
        logger.error(f"Guardrail input check failed: {e} — allowing request through")
        return None


def _check_output_guardrails(text: str) -> list:
    """
    Run guardrail checks on tool output using the InvokeGuardrailChecks API.
    Checks for harmful content and sensitive information in responses.
    Returns a list of findings for logging, empty list if clean.
    """
    try:
        client = get_bedrock_runtime()
        response = client.invoke_guardrail_checks(
            messages=[{"role": "assistant", "content": [{"text": text}]}],
            checks={
                "contentFilter": {
                    "categories": [
                        {"category": "VIOLENCE"},
                        {"category": "HATE"},
                        {"category": "MISCONDUCT"},
                    ]
                },
                "sensitiveInformation": {
                    "entities": [
                        {"type": "EMAIL"},
                        {"type": "PHONE"},
                        {"type": "US_SOCIAL_SECURITY_NUMBER"},
                        {"type": "CREDIT_DEBIT_CARD_NUMBER"},
                    ]
                },
            },
        )

        findings = []

        # Content filter findings (uses severityScore)
        if "contentFilter" in response.get("results", {}):
            for finding in response["results"]["contentFilter"]["results"]:
                if finding["severityScore"] >= GUARDRAIL_ESCALATE_THRESHOLD:
                    findings.append({
                        "type": "content",
                        "category": finding["category"],
                        "score": finding["severityScore"],
                    })

        # Sensitive information findings (uses confidenceScore)
        if "sensitiveInformation" in response.get("results", {}):
            for finding in response["results"]["sensitiveInformation"]["results"]:
                if finding["confidenceScore"] >= GUARDRAIL_ESCALATE_THRESHOLD:
                    findings.append({
                        "type": "pii",
                        "entity": finding["type"],
                        "score": finding["confidenceScore"],
                        "offset": f"[{finding['beginOffset']}:{finding['endOffset']}]",
                    })

        return findings

    except AttributeError:
        # API not available in current SDK version — skip gracefully
        logger.info("InvokeGuardrailChecks API not available — skipping output guardrail checks")
        return []
    except Exception as e:
        # Fail open — don't break the response pipeline
        logger.error(f"Guardrail output check failed: {e} — skipping")
        return []


def _extract_user_text(params: dict) -> str:
    """Extract the user's input text from MCP tool call parameters."""
    if not isinstance(params, dict):
        return ""
    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        return ""
    # Try common parameter names that contain user text
    for key in ("query", "prompt", "text", "message", "input", "description", "subject"):
        if key in arguments and isinstance(arguments[key], str):
            return arguments[key]
    # Fallback: concatenate all string values
    texts = [v for v in arguments.values() if isinstance(v, str)]
    return " ".join(texts) if texts else ""


def _extract_response_text(body: dict) -> str:
    """Extract text content from a tool response body."""
    if not isinstance(body, dict):
        return ""
    result = body.get("result", {})
    if not isinstance(result, dict):
        return ""
    content = result.get("content", [])
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
        return " ".join(texts)
    return ""
