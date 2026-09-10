"""AgentCore Gateway RESPONSE interceptor — rewrite the elicitation URL.

Packaged and wired onto the gateway by `deploy/07_deploy_interceptor.py`. See
https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors.html

When a user who has not consented invokes a tool, the gateway answers with a
URL-mode elicitation carrying an AgentCore Identity authorize URL. Following
that URL directly works, but it bypasses the consent portal's session binding.
This interceptor swaps it for the portal URL, so **every** MCP client reaching
this gateway is steered to the portal — not only the agent in this sample, which
does the same substitution in its own code.

Modes, via INTERCEPTOR_MODE:

    REWRITE   (default) replace the identity URL with PORTAL_URL on a -32042
              auth elicitation. Everything else passes through untouched.
    LOG_ONLY  pass everything through but log the full interceptor event. Useful
              if the elicitation payload ever changes shape and the rewrite
              stops matching.

The payload shape below was confirmed live against this sample's gateway: the
just-in-time prompt is **not** a streaming `elicitation/create` request. It is a
JSON-RPC error on the `tools/call` response — `isStreamingResponse: false`,
`statusCode: 200`, `body.error.code: -32042` — with the URLs at
`body.error.data.elicitations[*].url`:

    {"jsonrpc": "2.0", "id": 3,
     "error": {"code": -32042,
               "message": "This request requires more information.",
               "data": {"elicitations": [
                   {"mode": "url", "elicitationId": "…",
                    "url": "https://bedrock-agentcore.<region>.amazonaws.com/identities/oauth2/authorize?request_uri=…",
                    "message": "Please login to this URL for authorization."}]}}}

`statusCode` is echoed only when the input carried one, so the handler stays
correct if a future flow does stream (later stream events may override only the
body — the status code and headers are already sent).

The handler is stateless: the gateway may retry it, and identical input must
produce identical output.

Env:
    INTERCEPTOR_MODE   REWRITE | LOG_ONLY          (default REWRITE)
    PORTAL_URL         absolute https URL of the consent portal
    IDENTITY_URL_HOST  host substring identifying the URL to replace
                       (default "bedrock-agentcore")
"""

import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MODE = os.environ.get("INTERCEPTOR_MODE", "REWRITE").upper()
PORTAL_URL = os.environ.get("PORTAL_URL", "")
IDENTITY_URL_HOST = os.environ.get("IDENTITY_URL_HOST", "bedrock-agentcore")

OUTPUT_VERSION = "1.0"
ELICITATION_ERROR_CODE = -32042


def _passthrough_request(request_body):
    """A REQUEST interception — this interceptor only transforms responses."""
    return {
        "interceptorOutputVersion": OUTPUT_VERSION,
        "mcp": {"transformedGatewayRequest": {"body": request_body}},
    }


def _response(body, status_code):
    transformed = {"body": body}
    if status_code is not None:
        transformed["statusCode"] = status_code
    return {
        "interceptorOutputVersion": OUTPUT_VERSION,
        "mcp": {"transformedGatewayResponse": transformed},
    }


def _looks_like_identity_url(value):
    """Guard so the rewrite cannot touch a URL that is not AgentCore Identity's."""
    return isinstance(value, str) and value.startswith(("http://", "https://")) and IDENTITY_URL_HOST in value


def rewrite_elicitation_urls(body, portal_url):
    """Swap each URL-mode elicitation's identity URL for the portal URL.

    Returns the number of replacements made, so the log line is meaningful when
    the shape stops matching (zero replacements on a -32042 is the signal).
    """
    err = body.get("error")
    if not isinstance(err, dict):
        return 0
    data = err.get("data")
    if not isinstance(data, dict):
        return 0
    elicitations = data.get("elicitations")
    if not isinstance(elicitations, list):
        return 0

    count = 0
    for el in elicitations:
        if isinstance(el, dict) and el.get("mode") == "url" and _looks_like_identity_url(el.get("url")):
            el["url"] = portal_url
            count += 1
    return count


def lambda_handler(event, context):
    if MODE == "LOG_ONLY":
        # passRequestHeaders is false on this interceptor, so the event carries
        # no inbound auth token to leak into CloudWatch.
        logger.info("INTERCEPTOR_EVENT %s", json.dumps(event, default=str))

    mcp = event.get("mcp", {})
    gateway_response = mcp.get("gatewayResponse")
    if not gateway_response:
        return _passthrough_request(mcp.get("gatewayRequest", {}).get("body", {}))

    body = gateway_response.get("body", {})
    status_code = gateway_response.get("statusCode")

    is_elicitation = (
        isinstance(body, dict)
        and isinstance(body.get("error"), dict)
        and body["error"].get("code") == ELICITATION_ERROR_CODE
    )

    if MODE == "REWRITE" and is_elicitation:
        if not PORTAL_URL:
            logger.warning("REWRITE mode but PORTAL_URL is empty; passing through")
        else:
            replaced = rewrite_elicitation_urls(body, PORTAL_URL)
            logger.info("CONSENT_ELICITATION rewrote %d identity URL(s) to the portal", replaced)
            if replaced == 0:
                logger.warning(
                    "a -32042 elicitation matched no URL to rewrite; the payload shape "
                    "may have changed. Redeploy with --mode log-only to inspect it."
                )

    return _response(body, status_code)
