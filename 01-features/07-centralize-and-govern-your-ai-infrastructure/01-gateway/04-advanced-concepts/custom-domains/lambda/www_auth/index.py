# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""Lambda@Edge origin-response function: rewrite the ``resource_metadata``
parameter of the ``WWW-Authenticate`` header on 401 responses so it points at
the custom-domain PRM URL (RFC 9728 §5.1) instead of the raw gateway.

This only edits a response header (which Lambda@Edge is allowed to do); it does
not read the response body. The correct per-endpoint PRM URL is supplied by the
CloudFront viewer-request function via the ``x-agwcd-resource-metadata`` header.
"""

from __future__ import annotations

import re

_RM_RE = re.compile(r'resource_metadata="[^"]*"')


def rewrite_www_authenticate(header_value: str, resource_metadata: str) -> str:
    """Return ``header_value`` with its ``resource_metadata`` set/replaced.

    Preserves any other challenge parameters (realm, scope, ...). Pure function
    for unit testing.
    """
    new_param = f'resource_metadata="{resource_metadata}"'
    if _RM_RE.search(header_value):
        return _RM_RE.sub(new_param, header_value)
    if header_value.strip():
        return f"{header_value}, {new_param}"
    return f"Bearer {new_param}"


def handler(event, context):
    record = event["Records"][0]["cf"]
    request = record["request"]
    response = record["response"]

    if str(response.get("status")) != "401":
        return response

    rm_header = request.get("headers", {}).get("x-agwcd-resource-metadata")
    if not rm_header:
        return response
    resource_metadata = rm_header[0]["value"]

    headers = response.setdefault("headers", {})
    existing = headers.get("www-authenticate")
    if existing:
        for h in existing:
            h["value"] = rewrite_www_authenticate(h["value"], resource_metadata)
    else:
        headers["www-authenticate"] = [
            {
                "key": "WWW-Authenticate",
                "value": rewrite_www_authenticate("", resource_metadata),
            }
        ]
    return response
