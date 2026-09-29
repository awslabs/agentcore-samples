"""Echo back the headers the caller sent. Spike harness only, not part of the sample.

Answers spike 1: does a JWT_PASSTHROUGH target forward the REQUEST interceptor's
*transformed* Authorization header, or the original inbound one? The whole egress
design depends on the transformed value winning, and nothing in the repo proves it.

Deployed behind API Gateway rather than a Lambda Function URL: this account's SCP
blocks Function URLs (both NONE and AWS_IAM), which surfaces as a bare 403.
"""

import json


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    auth = headers.get("authorization", "<ABSENT>")

    # Never log a real bearer token. During the spike the only tokens present are
    # synthetic markers, but the sample's logging discipline starts here.
    body = {
        "authorization_seen": auth,
        "x_spike_marker": headers.get("x-spike-marker", "<ABSENT>"),
        "header_names": sorted(headers.keys()),
        "path": event.get("rawPath") or event.get("path"),
    }
    print("ECHO_RESULT=" + json.dumps(body))
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
