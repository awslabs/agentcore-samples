"""AgentCore Gateway interceptor — doc-strict passthrough.

Implements the response-streaming contract from the AgentCore Gateway docs:

  - REQUEST interceptor:
      Returns `transformedGatewayRequest.body` unchanged.
  - RESPONSE interceptor (non-streaming, `isStreamingResponse` is False or
    absent):
      Returns `transformedGatewayResponse` with `headers`, `statusCode`, and
      `body` unchanged.
  - RESPONSE interceptor (streaming, `isStreamingResponse=True`):
      First event (statusCode present in input): may override headers,
      statusCode, body. We pass through unchanged.
      Subsequent events (no statusCode in input): only `body` may be returned;
      headers and statusCode are ignored if present.

Logs identify which branch fired and the underlying MCP method/id so
CloudWatch traces can be correlated with the gateway's request id.
"""

import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    mcp = event.get("mcp", {}) or {}
    gateway_request = mcp.get("gatewayRequest") or {}
    gateway_response = mcp.get("gatewayResponse")

    if gateway_response is not None:
        return _handle_response(gateway_request, gateway_response)
    return _handle_request(gateway_request)


def _handle_request(gateway_request):
    body = gateway_request.get("body") or {}
    method = body.get("method") if isinstance(body, dict) else None
    msg_id = body.get("id") if isinstance(body, dict) else None
    has_result = isinstance(body, dict) and "result" in body
    has_error = isinstance(body, dict) and "error" in body
    kind = "request"
    if has_result:
        kind = "response"
    elif has_error:
        kind = "error"
    logger.info(
        "REQUEST interceptor: kind=%s method=%r id=%r passing through",
        kind,
        method,
        msg_id,
    )
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"body": body}},
    }


def _handle_response(gateway_request, gateway_response):
    body = gateway_response.get("body") or {}
    is_streaming = bool(gateway_response.get("isStreamingResponse"))
    has_status_in_input = "statusCode" in gateway_response
    has_headers_in_input = "headers" in gateway_response

    inbound_method = (gateway_request.get("body") or {}).get("method")
    msg_method = body.get("method") if isinstance(body, dict) else None
    msg_id = body.get("id") if isinstance(body, dict) else None

    if not is_streaming:
        logger.info(
            "RESPONSE interceptor (non-streaming): inbound_method=%r "
            "outbound_method=%r id=%r passing through",
            inbound_method,
            msg_method,
            msg_id,
        )
        out = {"body": body}
        if has_status_in_input:
            out["statusCode"] = gateway_response.get("statusCode", 200)
        if has_headers_in_input:
            out["headers"] = gateway_response.get("headers", {})
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {"transformedGatewayResponse": out},
        }

    is_first_event = has_status_in_input
    if is_first_event:
        logger.info(
            "RESPONSE interceptor (streaming, first event): inbound_method=%r "
            "outbound_method=%r id=%r passing through",
            inbound_method,
            msg_method,
            msg_id,
        )
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayResponse": {
                    "body": body,
                    "statusCode": gateway_response.get("statusCode", 200),
                    "headers": gateway_response.get("headers", {}),
                }
            },
        }

    logger.info(
        "RESPONSE interceptor (streaming, subsequent event): inbound_method=%r "
        "outbound_method=%r id=%r passing through (body only)",
        inbound_method,
        msg_method,
        msg_id,
    )
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayResponse": {"body": body}},
    }
