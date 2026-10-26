"""Custom Just-in-Time Auth -- AgentCore Gateway RESPONSE interceptor.

This is the Lambda that ``scripts/deploy_interceptor.py`` packages and wires onto
the Entra consent-portal gateway as a RESPONSE interceptor
(https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors.html).

It runs in two modes, chosen by the INTERCEPTOR_MODE environment variable:

    LOG_ONLY   (default) pass every response through unchanged, but log the full
               interceptor event. This is phase 1: the URL-mode elicitation body
               is not documented, so we observe the real shape in CloudWatch
               before rewriting anything.
    REWRITE    when the gateway emits a URL-mode elicitation, replace the
               AgentCore-vended identity/authorization URL with the consent portal
               URL, so an unconsented user is driven through the hosted portal
               instead of the bare identity URL. Everything else passes through.

Confirmed by a live Phase-1 run (2026-09-08): the just-in-time URL-mode
elicitation does NOT arrive as a streaming ``elicitation/create`` request. It
comes back as a JSON-RPC *error* on the ``tools/call`` response --
``gatewayResponse.isStreamingResponse == false``, ``statusCode == 200``, and
``body.error.code == -32042`` with the URL(s) under
``body.error.data.elicitations[*].url`` (see ``rewrite_elicitation_urls``). We
still include ``statusCode`` in the output only when the input carried one, so the
handler stays correct if a future flow does stream (subsequent stream events may
override only ``body``).

The handler is stateless: the gateway may retry it, and identical input must
produce identical output.

Env:
    INTERCEPTOR_MODE     LOG_ONLY | REWRITE            (default LOG_ONLY)
    PORTAL_URL           full consent portal URL to inject in REWRITE mode
    IDENTITY_URL_HOST    host substring identifying the URL to replace
                         (default "bedrock-agentcore"; the AgentCore identity /
                         authorize endpoint that URL-mode elicitation vends)
"""

import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MODE = os.environ.get("INTERCEPTOR_MODE", "LOG_ONLY").upper()
PORTAL_URL = os.environ.get("PORTAL_URL", "")
IDENTITY_URL_HOST = os.environ.get("IDENTITY_URL_HOST", "bedrock-agentcore")

OUTPUT_VERSION = "1.0"


def _passthrough_request(request_body):
    return {
        "interceptorOutputVersion": OUTPUT_VERSION,
        "mcp": {"transformedGatewayRequest": {"body": request_body}},
    }


def _response(body, status_code):
    """Build a transformedGatewayResponse.

    statusCode is included only when the input carried one: on subsequent
    streaming events it is absent (already sent to the client) and returning it
    would be ignored anyway.
    """
    transformed = {"body": body}
    if status_code is not None:
        transformed["statusCode"] = status_code
    return {
        "interceptorOutputVersion": OUTPUT_VERSION,
        "mcp": {"transformedGatewayResponse": transformed},
    }


def _looks_like_identity_url(value):
    return (
        isinstance(value, str)
        and value.startswith(("http://", "https://"))
        and IDENTITY_URL_HOST in value
    )


# Confirmed by a live Phase-1 run (2026-09-08). A just-in-time URL-mode auth
# prompt is NOT an `elicitation/create` request and NOT a streaming event. It
# comes back as a JSON-RPC *error* on the `tools/call` response
# (isStreamingResponse == false, statusCode == 200):
#
#   body.error.code    == -32042
#   body.error.message == "This request requires more information."
#   body.error.data.elicitations == [
#     {"mode": "url", "elicitationId": "...", "url": "<identity/authorize URL>",
#      "message": "Please login to this URL for authorization."}, ...]
#
# The identity/authorize URL is each entry's "url"; its host is
# bedrock-agentcore.<region>.amazonaws.com, so IDENTITY_URL_HOST matches it.
ELICITATION_ERROR_CODE = -32042


def rewrite_elicitation_urls(body, portal_url):
    """Replace each URL-mode elicitation's identity URL with the portal URL.

    Targets the confirmed shape above: body.error.data.elicitations[*].url. The
    host guard (_looks_like_identity_url) keeps this from touching anything that
    is not an AgentCore identity URL. Returns the number of replacements made.
    """
    err = body.get("error")
    if not isinstance(err, dict):
        return 0
    elicitations = err.get("data", {}).get("elicitations")
    if not isinstance(elicitations, list):
        return 0

    count = 0
    for el in elicitations:
        if (
            isinstance(el, dict)
            and el.get("mode") == "url"
            and _looks_like_identity_url(el.get("url"))
        ):
            el["url"] = portal_url
            count += 1
    return count


def lambda_handler(event, context):
    # Full-body logging is the whole point of phase 1. passRequestHeaders is false
    # on this interceptor, so the event carries no inbound auth tokens to leak.
    logger.info("INTERCEPTOR_EVENT %s", json.dumps(event, default=str))

    mcp = event.get("mcp", {})
    gateway_response = mcp.get("gatewayResponse")

    # No gatewayResponse -> this is a REQUEST interception. We only care about
    # responses; pass the request through unchanged.
    if not gateway_response:
        request_body = mcp.get("gatewayRequest", {}).get("body", {})
        return _passthrough_request(request_body)

    body = gateway_response.get("body", {})
    status_code = gateway_response.get("statusCode")

    if (
        MODE == "REWRITE"
        and isinstance(body, dict)
        and isinstance(body.get("error"), dict)
        and body["error"].get("code") == ELICITATION_ERROR_CODE
    ):
        if not PORTAL_URL:
            logger.warning("REWRITE mode but PORTAL_URL is empty; passing through")
        else:
            replaced = rewrite_elicitation_urls(body, PORTAL_URL)
            logger.info(
                "url-mode elicitation detected; rewrote %d identity URL(s) to portal",
                replaced,
            )

    return _response(body, status_code)
