"""REQUEST interceptor that (a) injects a marker Authorization header and (b) dumps
its own event. Spike harness only, not part of the sample.

Spike 1: if the echo target reports our marker, a JWT_PASSTHROUGH target forwards
         the interceptor's TRANSFORMED Authorization header. If it reports the
         original inbound value (or ABSENT), it forwards the untransformed request
         and the egress design needs rethinking.

Spike 3: the printed event tells us what the gateway actually passes -- whether a
         session id or an AgentCore identity chain is present. That decides the
         correlation key for tracing, and whether user identity could be read here
         without the agent forwarding a token at all.
"""

import json
import os

MARKER = os.environ.get("SPIKE_MARKER", "SPIKE-INJECTED-TOKEN")


def handler(event, context):
    # Full event dump. Safe here: the only Authorization value in the spike is a
    # synthetic marker, never a real token.
    print("INTERCEPTOR_EVENT=" + json.dumps(event, default=str))

    req = (event.get("mcp") or {}).get("gatewayRequest") or {}
    headers = dict(req.get("headers") or {})

    inbound_auth = next((v for k, v in headers.items() if k.lower() == "authorization"), "<ABSENT>")
    print(f"INBOUND_AUTHORIZATION_PRESENT={inbound_auth != '<ABSENT>'}")

    # Drop any existing Authorization casing variant, then set ours.
    headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}
    headers["Authorization"] = f"Bearer {MARKER}"
    headers["X-Spike-Marker"] = MARKER

    out = {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"headers": headers, "body": req.get("body")}},
    }
    print("INTERCEPTOR_OUTPUT_KEYS=" + json.dumps(sorted(headers.keys())))
    return out
