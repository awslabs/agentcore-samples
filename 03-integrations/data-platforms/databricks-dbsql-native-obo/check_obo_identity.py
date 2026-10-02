#!/usr/bin/env python3
"""Preflight check for per-user (on-behalf-of) identity through Amazon Bedrock AgentCore Gateway
to a Databricks Managed MCP server.

Answers one question that no API or document currently answers: when an end user calls your gateway,
does Databricks Unity Catalog see *that user*, or does it see the shared service principal?

The check calls a tool on an existing gateway target and reports which principal arrived. It creates
and modifies nothing.

Usage:
    python check_obo_identity.py \
        --gateway-url https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp \
        --target-name dbx-sql-te \
        --token "$END_USER_JWT"

Exit codes:
     0  PER_USER              Unity Catalog saw the end user. Per-user delegation is working.
     1  SERVICE_PRINCIPAL     The call ran as the shared service principal, not the end user.
     2  EXCHANGE_REFUSED      The service refused the token exchange (commonly a non-allowlisted account).
     3  CALLER_PERMISSIONS    Your own gateway execution role is missing permissions.
     4  UNKNOWN               Could not determine. The report explains what was seen.
     5  INBOUND_TOKEN         The token you presented was rejected: expired, or wrong issuer or audience.
     6  PROVIDER_NOT_FOUND    The credential provider on the target does not exist where the gateway looked.
     7  TRANSIENT             Rate limited, or a service error. Retry.
     8  TARGET_REJECTED_TOKEN The exchange succeeded and Databricks rejected the token it delivered.
     9  TARGET_NOT_FOUND      No tool matched --target-name, or the target contributed no tools.
    10  IDENTITY_MISMATCH     current_user() is not the subject claim in the token you presented.
    11  PER_USER_UNVERIFIED   A human-shaped principal, but the claim could not be compared. Not a pass.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request

DEFAULT_QUERY = "SELECT current_user() AS who"
DEFAULT_TOOL = "execute_sql"
TOOL_SEPARATOR = "___"

PER_USER = "PER_USER"
SERVICE_PRINCIPAL = "SERVICE_PRINCIPAL"
EXCHANGE_REFUSED = "EXCHANGE_REFUSED"
CALLER_PERMISSIONS = "CALLER_PERMISSIONS"
TARGET_REJECTED_TOKEN = "TARGET_REJECTED_TOKEN"
TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
INBOUND_TOKEN = "INBOUND_TOKEN"
PROVIDER_NOT_FOUND = "PROVIDER_NOT_FOUND"
TRANSIENT = "TRANSIENT"
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
PER_USER_UNVERIFIED = "PER_USER_UNVERIFIED"
WORKSPACE_MEMBERSHIP = "WORKSPACE_MEMBERSHIP"
GATEWAY_UNREACHABLE = "GATEWAY_UNREACHABLE"
UNKNOWN = "UNKNOWN"

EXIT_CODES = {
    PER_USER: 0,
    SERVICE_PRINCIPAL: 1,
    EXCHANGE_REFUSED: 2,
    CALLER_PERMISSIONS: 3,
    UNKNOWN: 4,
    WORKSPACE_MEMBERSHIP: 4,
    GATEWAY_UNREACHABLE: 4,
    INBOUND_TOKEN: 5,
    PROVIDER_NOT_FOUND: 6,
    TRANSIENT: 7,
    TARGET_REJECTED_TOKEN: 8,
    TARGET_NOT_FOUND: 9,
    IDENTITY_MISMATCH: 10,
    PER_USER_UNVERIFIED: 11,
}

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

REMEDY_CALLER_PERMISSIONS = (
    "Your gateway execution role is missing permissions, not the account allowlist. Check CloudTrail for "
    "AccessDenied. A role policy pinned to one Region is the usual cause: scope Secrets Manager to "
    "arn:aws:secretsmanager:*:<account>:secret:bedrock-agentcore-identity* and kms:ViaService to "
    "secretsmanager.*.amazonaws.com."
)
REMEDY_WORKSPACE_MEMBERSHIP = (
    "Identity mapping worked and provisioning is the gap: the federation policy resolved the end user, but "
    "that user is not a member of the target Databricks workspace. Add them to the workspace."
)
REMEDY_TARGET_REJECTED_TOKEN = (
    "The exchange succeeded and Databricks rejected the token that was delivered, so this is authorization "
    "at the target rather than identity. In order of how often we have seen it: the target's oauth scopes "
    "do not include all-apis; the identity lacks the workspace-access and databricks-sql-access "
    "entitlements (re-mint its token after granting them); the credential provider's discovery host is not "
    "the workspace host the target points at; or the caller has no permission on the warehouse, Genie "
    "space or function being called. The same message at target sync time is a different problem - see the "
    "README."
)
REMEDY_TARGET_NOT_FOUND = (
    "No tool matched the target name. Either --target-name is wrong, or the target contributed no tools: an "
    "exchange that fails at listing time on a DYNAMIC target drops it out of tools/list with no error. Call "
    "tools/list yourself to see which targets are present, and see the listingMode note in the README."
)
REMEDY_INBOUND_TOKEN = (
    "The token you presented was rejected before the exchange: expired, or carrying an issuer or audience "
    "the gateway authorizer does not accept. Mint a fresh one and check its iss and aud against the "
    "authorizer's discoveryUrl and allowedAudience."
)
REMEDY_PROVIDER_NOT_FOUND = (
    "The credential provider named on the target does not exist where the gateway looked. Check the "
    "providerArn on the target, and that the provider lives in the same Region as the gateway."
)
REMEDY_TRANSIENT = (
    "Rate limiting or a service error rather than a misconfiguration, so retry before changing anything. "
    "This is matched on the message text alone and both the exchange and the target can emit it, so read "
    "the detail field to see which one did, and give AWS the verbatim text if it persists."
)
REMEDY_EXCHANGE_REFUSED = (
    "The exchange was refused. Databricks issues a per-user token only when no client authentication is "
    "presented, which requires your AWS account and Region to be enabled for the public-client exchange. "
    "Verify the prerequisites first: an account-level Databricks federation policy whose issuer and "
    "audience match your identity provider, with subject_claim set to the claim carrying the user identity."
)
REMEDY_EXCHANGE_FAILED_GENERIC = (
    "The exchange failed without a specific reason. Work through the prerequisites in the README, then ask "
    "AWS whether this account and Region are enabled for the public-client exchange."
)
REMEDY_SERVICE_PRINCIPAL = (
    "The call ran as the shared service principal. The target is using CLIENT_CREDENTIALS, or the "
    "on-behalf-of exchange fell back to machine-to-machine. Confirm grantType is TOKEN_EXCHANGE on both the "
    "credential provider and the target."
)
REMEDY_GATEWAY_UNREACHABLE = (
    "The gateway URL itself could not be reached, so nothing was tested. This is a transport problem "
    "rather than an identity one: check the URL, that the Region in the hostname is the one the gateway "
    "was created in, and that this host has network egress to it."
)
REMEDY_NO_ERROR_TEXT = "No error text was returned. Enable APPLICATION_LOGS delivery on the gateway to see more."
REMEDY_UNRECOGNISED = "Unrecognised error. Include the verbatim text when asking AWS to trace it."
REMEDY_UNPARSEABLE = (
    "Could not classify the principal in the response. A principal that is neither an email address nor "
    "an application UUID lands here — a workspace whose usernames are not email-shaped will do it — so "
    "read the detail field before concluding delegation is broken."
)
REMEDY_IDENTITY_MISMATCH = (
    "Unity Catalog saw a human, but not the human in the token you presented, so an email-shaped principal "
    "is not on its own proof of per-user delegation. Commonly a shared account whose username is "
    "email-shaped, or a federation policy whose subject_claim reads a different claim than --subject-claim."
)
REMEDY_PER_USER = "Per-user delegation is working. Unity Catalog is enforcing the end user's own grants."
REMEDY_PER_USER_UNCHECKED = (
    "Unity Catalog saw a human-shaped principal, but the claim named by --subject-claim is not in the token "
    "presented, so nothing was compared and a shared account with an email-shaped username would look "
    "identical. This deliberately does not exit 0. Name the claim your federation policy maps: a Cognito "
    "access token, for one, carries no email claim at all."
)
REMEDY_SUBJECT_NOT_COMPARABLE = (
    "Unity Catalog saw a human-shaped principal and the claim read from the token is opaque, which sub is "
    "on both Entra and Cognito, so the two cannot be compared and their disagreement proves nothing either "
    "way. Point --subject-claim at the claim your federation policy maps to a username, commonly email or "
    "preferred_username. This deliberately does not exit 0."
)

# Ordered most specific first: several of these messages share substrings. In particular the service
# prefixes distinct failures with "Token exchange failed:", so every specific variant has to sit above
# that catch-all or it inherits the enablement remedy, which is wrong for an expired token, a missing
# provider or a rate limit.
FAILURE_SIGNATURES = (
    ("insufficient permissions for token exchange", CALLER_PERMISSIONS, REMEDY_CALLER_PERMISSIONS),
    ("is not a member of workspace", WORKSPACE_MEMBERSHIP, REMEDY_WORKSPACE_MEMBERSHIP),
    ("no target found for capability", TARGET_NOT_FOUND, REMEDY_TARGET_NOT_FOUND),
    ("inbound token is invalid", INBOUND_TOKEN, REMEDY_INBOUND_TOKEN),
    ("credential provider not found", PROVIDER_NOT_FOUND, REMEDY_PROVIDER_NOT_FOUND),
    ("rate limited", TRANSIENT, REMEDY_TRANSIENT),
    ("token exchange encountered a service error", TRANSIENT, REMEDY_TRANSIENT),
    ("check credential provider scopes, audience, or idp configuration", EXCHANGE_REFUSED, REMEDY_EXCHANGE_REFUSED),
    # A wrapper, not a leaf: the client reports it as "MCP listTools failed: Authorization error when
    # sending message", sometimes with the underlying cause appended. It sits below the leaves so a
    # composite message is classified by its cause rather than by the wrapper.
    ("authorization error when sending message", TARGET_REJECTED_TOKEN, REMEDY_TARGET_REJECTED_TOKEN),
    ("token exchange failed", EXCHANGE_REFUSED, REMEDY_EXCHANGE_FAILED_GENERIC),
)


def classify_identity(value: str | None) -> str:
    """Classify the principal string returned by current_user().

    Databricks returns an email address for a human and an application UUID for a service principal.
    """
    if value is None:
        return UNKNOWN
    candidate = value.strip()
    if not candidate:
        return UNKNOWN
    if _EMAIL.match(candidate):
        return PER_USER
    if _UUID.match(candidate):
        return SERVICE_PRINCIPAL
    return UNKNOWN


def subject_from_token(token: str | None, claim: str = "email") -> str | None:
    """Read one claim out of the token the caller presented, without verifying it.

    By the time this runs the gateway authorizer and Databricks have both validated the token, so there is
    nothing to gain from verifying it again here — it is read only to compare who was presented with who
    arrived. Anything unreadable returns None and the comparison is skipped rather than failed.
    """
    if not token or not claim:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    segment = parts[1]
    try:
        # JWT segments are base64url with the padding stripped; put it back before decoding.
        payload = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get(claim)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def classify_failure(text: str | None) -> tuple[str, str]:
    """Map an error string from the gateway to a cause and a remedy."""
    if not text:
        return UNKNOWN, REMEDY_NO_ERROR_TEXT
    haystack = text.lower()
    for needle, verdict, remedy in FAILURE_SIGNATURES:
        if needle in haystack:
            return verdict, remedy
    return UNKNOWN, REMEDY_UNRECOGNISED


def resolve_identity(shape: str, identity: str | None, subject: str | None) -> tuple[str, str]:
    """Decide the verdict from the principal that arrived and the subject claim that was presented.

    The shape of the principal alone is weak evidence in both directions: a shared service account with an
    email-shaped username looks like a human, and a workspace whose usernames are not email-shaped makes a
    real human look unclassifiable. An exact match against the presented claim outranks both.
    """
    if shape == SERVICE_PRINCIPAL:
        return SERVICE_PRINCIPAL, REMEDY_SERVICE_PRINCIPAL
    arrived = (identity or "").strip()
    if subject and arrived and subject.casefold() == arrived.casefold():
        return PER_USER, REMEDY_PER_USER
    if shape == PER_USER:
        if subject is None:
            return PER_USER_UNVERIFIED, REMEDY_PER_USER_UNCHECKED
        if _EMAIL.match(subject):
            # Two different humans: this is the false positive the comparison exists to catch.
            return IDENTITY_MISMATCH, REMEDY_IDENTITY_MISMATCH
        return PER_USER_UNVERIFIED, REMEDY_SUBJECT_NOT_COMPARABLE
    return UNKNOWN, REMEDY_UNPARSEABLE


def qualified_tool_name(target_name: str, tool_name: str) -> str:
    """Gateway namespaces each target's tools as <target>___<tool>."""
    if not target_name:
        raise ValueError("target_name is required")
    if not tool_name:
        raise ValueError("tool_name is required")
    if TOOL_SEPARATOR in tool_name:
        return tool_name
    return f"{target_name}{TOOL_SEPARATOR}{tool_name}"


def extract_text(response: dict) -> str | None:
    """Pull the first text block out of an MCP tools/call result."""
    content = (response.get("result") or {}).get("content") or []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text") is not None:
            return block.get("text")
    return None


def is_error(response: dict) -> bool:
    """True when the MCP result is an error, or the JSON-RPC envelope itself carries one.

    Note MCP tool failures arrive as HTTP 200 with isError set, so the status code alone is not enough.
    """
    if "error" in response:
        return True
    return bool((response.get("result") or {}).get("isError"))


def extract_identity(text: str | None) -> str | None:
    """Pull current_user() out of a Databricks statement-execution payload."""
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    rows = (payload.get("result") or {}).get("data_array") or []
    for row in rows:
        # Two shapes are returned depending on the disposition: a bare list, or {"values":[{"string_value":...}]}
        if isinstance(row, list) and row:
            first = row[0]
            if isinstance(first, str):
                return first
        if isinstance(row, dict):
            values = row.get("values") or []
            if values and isinstance(values[0], dict):
                value = values[0].get("string_value")
                if isinstance(value, str):
                    return value
    return None


def parse_mcp_body(body: str, expected_id: object = None) -> dict:
    """Decode an MCP HTTP response body into a single JSON-RPC message.

    The endpoint may answer with plain JSON or, because we accept text/event-stream, with one or more
    SSE frames. A greedy scan over the whole body would splice two frames together, so parse frames
    individually and return the last one that carries a JSON-RPC result or error.
    """
    if not body or not body.strip():
        raise ValueError("empty response body")

    messages = [message for message in _sse_payloads(body) if isinstance(message, dict)]
    if messages:
        # A stream can carry notifications or keep-alives alongside the reply, and some of those
        # also contain a "result". Prefer the frame whose id matches the request we sent; only
        # fall back to "last result-bearing frame" when no id was supplied or none matches.
        if expected_id is not None:
            for message in reversed(messages):
                same_id = str(message.get("id")) == str(expected_id)
                if same_id and ("result" in message or "error" in message):
                    return message
        for message in reversed(messages):
            if "result" in message or "error" in message:
                return message
        return messages[-1]

    start = body.find("{")
    if start == -1:
        raise ValueError(f"no JSON object in response: {body[:200]!r}")
    try:
        decoded, _ = json.JSONDecoder().raw_decode(body[start:])
    except ValueError as exc:
        raise ValueError(f"could not decode response: {body[:200]!r}") from exc
    if isinstance(decoded, dict):
        return decoded
    raise ValueError(f"expected a JSON object, got {type(decoded).__name__}")


def _sse_payloads(body: str):
    """Yield the decoded payload of each SSE event that carries valid JSON.

    Within one event, consecutive ``data:`` lines are concatenated.

    The SSE specification joins them with a newline. We concatenate instead, deliberately: the payload
    here is always JSON, which ignores whitespace between tokens, so the two agree on every well-formed
    frame -- and on the one case where they differ, a line split mid-token, concatenation recovers the
    value while a newline join injects a literal newline into a JSON string and fails to decode.
    """
    for block in re.split(r"\r?\n\r?\n", body):
        lines = [line for line in block.splitlines() if line.startswith("data:")]
        if not lines:
            continue
        payload = "".join(line[len("data:") :].strip() for line in lines)
        if not payload:
            continue
        try:
            yield json.loads(payload)
        except ValueError:
            continue


class GatewayUnreachable(Exception):
    """The gateway URL could not be reached at all — DNS, connection or socket timeout."""


def mcp_post(url: str, token: str, payload: dict, protocol_version: str | None, timeout: int = 60) -> dict:
    """POST a single JSON-RPC message to an MCP endpoint and return the decoded body."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # An MCP error arrives as a normal body on a non-2xx response, so this is not a transport failure.
        body = exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:  # noqa: UP041
        # HTTPError is a subclass of URLError and is handled above, so reaching here means the
        # request never got an HTTP response: bad host, refused connection, or the read timed out.
        # socket.timeout is an alias of TimeoutError from 3.10 on, but a distinct OSError subclass
        # before that, so both are listed and the linter is told to leave it alone.
        raise GatewayUnreachable(f"{url}: {getattr(exc, 'reason', exc)}") from exc
    return parse_mcp_body(body, expected_id=payload.get("id"))


def negotiate(url: str, token: str, requested_version: str) -> str:
    """Initialize the session and return the protocol version the gateway actually selected.

    The gateway pins the session to the version it returns here; sending a different one afterwards is
    rejected with -32600.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": requested_version,
            "capabilities": {},
            "clientInfo": {"name": "check-obo-identity", "version": "1.0"},
        },
    }
    response = mcp_post(url, token, payload, protocol_version=None)
    return (response.get("result") or {}).get("protocolVersion") or requested_version


# The identity query itself runs on a SQL warehouse that may be cold. A cold start plus the query can
# exceed a minute, and timing out there reports UNKNOWN for a target that is actually configured
# correctly, so the query gets a longer budget than the handshake. Override with OBO_QUERY_TIMEOUT.
_DEFAULT_QUERY_TIMEOUT = 180


def _query_timeout() -> int:
    """Read OBO_QUERY_TIMEOUT, falling back to the default rather than crashing at import."""
    raw = os.environ.get("OBO_QUERY_TIMEOUT", "")
    if not raw:
        return _DEFAULT_QUERY_TIMEOUT
    try:
        value = int(raw)
    except ValueError:
        print(f"OBO_QUERY_TIMEOUT={raw!r} is not an integer; using {_DEFAULT_QUERY_TIMEOUT}s.", file=sys.stderr)
        return _DEFAULT_QUERY_TIMEOUT
    if value <= 0:
        print(f"OBO_QUERY_TIMEOUT={value} must be positive; using {_DEFAULT_QUERY_TIMEOUT}s.", file=sys.stderr)
        return _DEFAULT_QUERY_TIMEOUT
    return value


QUERY_TIMEOUT = _query_timeout()


def run_check(
    url: str,
    token: str,
    target_name: str,
    tool_name: str,
    query: str,
    requested_version: str,
    subject_claim: str = "email",
) -> dict:
    """Run the identity probe and return a verdict dictionary."""
    negotiated = negotiate(url, token, requested_version)
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": qualified_tool_name(target_name, tool_name),
            "arguments": {"query": query},
        },
    }
    response = mcp_post(url, token, payload, protocol_version=negotiated, timeout=QUERY_TIMEOUT)
    text = extract_text(response)
    if is_error(response):
        verdict, remedy = classify_failure(text or json.dumps(response.get("error") or {}))
        return {"verdict": verdict, "remedy": remedy, "detail": text, "protocol_version": negotiated}
    identity = extract_identity(text)
    subject = subject_from_token(token, subject_claim)
    verdict, remedy = resolve_identity(classify_identity(identity), identity, subject)
    return {
        "verdict": verdict,
        "remedy": remedy,
        "identity": identity,
        "subject": subject,
        "detail": None if verdict == PER_USER else text,
        "protocol_version": negotiated,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check whether AgentCore Gateway delivers per-user identity to Databricks Managed MCP."
    )
    parser.add_argument("--gateway-url", required=True, help="Full gateway MCP URL, ending in /mcp")
    parser.add_argument("--target-name", required=True, help="Gateway target name, e.g. dbx-sql-te")
    parser.add_argument("--token", required=True, help="An end user's JWT from your identity provider")
    parser.add_argument("--tool-name", default=DEFAULT_TOOL, help=f"Tool to call (default: {DEFAULT_TOOL})")
    parser.add_argument("--query", default=DEFAULT_QUERY, help=f"SQL to run (default: {DEFAULT_QUERY})")
    parser.add_argument("--protocol-version", default="2025-06-18", help="MCP version to request at initialize")
    parser.add_argument(
        "--subject-claim",
        default="email",
        help="Claim in the presented token that your federation policy maps to a user (default: email)",
    )
    parser.add_argument("--json", action="store_true", help="Emit the verdict as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_check(
            args.gateway_url,
            args.token,
            args.target_name,
            args.tool_name,
            args.query,
            args.protocol_version,
            args.subject_claim,
        )
    except GatewayUnreachable as exc:
        result = {"verdict": GATEWAY_UNREACHABLE, "remedy": REMEDY_GATEWAY_UNREACHABLE, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a sample should report, not traceback
        result = {"verdict": UNKNOWN, "remedy": f"The check could not complete: {exc}", "detail": None}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"verdict : {result['verdict']}")
        if result.get("identity"):
            print(f"identity: {result['identity']}")
        if result.get("subject"):
            print(f"subject : {result['subject']}")
        print(f"meaning : {result['remedy']}")
        if result.get("detail"):
            print(f"detail  : {result['detail']}")
    return EXIT_CODES.get(result["verdict"], 4)


if __name__ == "__main__":
    sys.exit(main())
