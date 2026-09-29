"""Spike 4 interceptor: answers Q1 and sets up Q2. Harness only.

Q1  Does the client's custom X-Okta-Id-Token header reach us? The real interceptor
    needs the ID token to run ID-JAG leg 1, and a connection header is the only
    delivery route that keeps it out of the model's context.

Q2  Rewrite Authorization with a marker so the echo target can report whether the
    rewrite survives on a gateway that validated a real JWT.

Also logs whether mcp.gatewayRequest.context is populated under CUSTOM_JWT -- if it
carries verified claims, the real interceptor can read `sub` from the gateway
instead of decoding a token itself.
"""

import json
import os

MARKER = os.environ.get("MARKER", "SPIKE4-REWRITTEN-BY-INTERCEPTOR")
ID_TOKEN_HEADER = os.environ.get("ID_TOKEN_HEADER", "X-Okta-Id-Token")


def handler(event, context):
    req = (event.get("mcp") or {}).get("gatewayRequest") or {}
    headers = dict(req.get("headers") or {})
    lower = {k.lower(): v for k, v in headers.items()}

    # Q1 -- never log the token itself, only whether it arrived and its length.
    id_token = lower.get(ID_TOKEN_HEADER.lower())
    print(f"ID_TOKEN_HEADER_SEEN={bool(id_token)} len={len(id_token or '')}")
    print("INBOUND_HEADER_NAMES=" + json.dumps(sorted(lower.keys())))
    print(f"INBOUND_AUTHORIZATION_PRESENT={'authorization' in lower}")

    # Does CUSTOM_JWT populate the context with verified claims?
    ctx = req.get("context")
    print(
        f"CONTEXT_PRESENT={ctx is not None} CONTEXT_KEYS="
        + json.dumps(sorted(ctx.keys()) if isinstance(ctx, dict) else [])
    )
    if isinstance(ctx, dict):
        print("CONTEXT_DUMP=" + json.dumps(ctx, default=str)[:1200])

    # Q2 -- what we put in Authorization matters as much as whether we replace it.
    #
    # REWRITE_MODE:
    #   none    leave Authorization untouched (Cedar sees the original user JWT)
    #   marker  a NON-JWT string. Unrepresentative of the real design, but it shows
    #           what happens when the value cannot be parsed at all.
    #   same    the SAME inbound JWT, echoed back. Valid, correct issuer -- isolates
    #           "was it replaced" from "was the replacement parseable".
    #   t_tool  a real T_tool minted by the ID-JAG legs: a valid JWT, but issued by a
    #           DIFFERENT authorization server (the resource AS) than the one the
    #           gateway's authorizer trusts. THIS is the real design.
    mode = os.environ.get("REWRITE_MODE", "marker").lower()
    inbound = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
    out_headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}

    if mode == "none":
        out_headers = dict(headers)
        out_headers["X-Spike4-Marker"] = MARKER
        injected = "<unchanged>"
    elif mode == "same":
        out_headers["Authorization"] = inbound
        injected = "same inbound JWT"
    elif mode == "t_tool":
        t_tool = os.environ.get("T_TOOL", "")
        if not t_tool:
            print("T_TOOL env var is empty -- falling back to marker")
            out_headers["Authorization"] = f"Bearer {MARKER}"
            injected = "marker (T_TOOL missing)"
        else:
            out_headers["Authorization"] = f"Bearer {t_tool}"
            injected = "real T_tool from the resource AS"
    else:
        out_headers["Authorization"] = f"Bearer {MARKER}"
        injected = "non-JWT marker"

    print(f"REWRITE_MODE={mode} INJECTED={injected}")

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"headers": out_headers, "body": req.get("body")}},
    }
