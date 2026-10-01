"""Gateway REQUEST interceptor: runs Okta Cross App Access and injects the result.

This is where the sample's central idea lives. On every `tools/call` the gateway hands
this Lambda the inbound request; it:

  1. takes the inbound `Authorization` bearer -- the token the gateway just validated
  2. leg 1 — exchanges it at the Okta **org** server for an ID-JAG (RFC 8693)
  3. leg 2 — redeems the ID-JAG at the **resource** AS for `T_tool` (RFC 7523)
  4. returns the request with `Authorization: Bearer <T_tool>`

Step 1 used to read a separate `X-Okta-Id-Token` header, because leg 1 would only accept
an ID token. Okta's **Machine access** configuration lifts that: register the inbound
token's `cid` as a caller and leg 1 accepts an `access_token`. That removes a whole piece
of plumbing -- the BFF no longer forwards an ID token through the runtime, and the agent
handles one credential instead of two. `XAA_LEG1_SUBJECT=id_token` restores the old
behaviour for orgs without Machine access; see IDP_SETUP_OKTA.md.

The agent therefore never holds a credential that can reach the API, and the API
receives a token whose `sub` is the human and whose `act.sub` is the agent.

Four behaviours here are load-bearing, each established by testing against a live
gateway rather than inferred from the docs:

  * The injected value MUST be a parseable JWT. A non-JWT breaks the gateway's policy
    evaluation with "Policy Evaluation Internal Failure" -- an error, not a deny --
    and also breaks tools/list. A real T_tool satisfies this naturally.
  * This runs BEFORE the Cedar policy engine, and Cedar still sees the *inbound*
    token's claims, so per-user policy keeps working while we swap the credential.
  * Only `Authorization` is forwarded to the upstream; other headers are dropped.
  * `mcp.gatewayRequest.context` is always null, so the user's identity has to come
    from the token we decode ourselves.

T_tool is cached per subject because the ID-JAG is single-use and each leg 1 consumes
one of Okta's 250 ID-JAGs per user, per resource, per month on plain SSO. Without the
cache a chatty conversation would exhaust the quota.

Environment:
  OKTA_ORG_URL          https://<tenant>.okta.com        (leg 1 endpoint)
  RESOURCE_AS_ISSUER    https://<tenant>.okta.com/oauth2/<as-id>   (leg 2 endpoint)
  RESOURCE_SCOPE        todos.read
  AI_AGENT_CLIENT_ID    the wlp... client id
  AI_AGENT_KEY_KID      the kid of the registered public key
  AI_AGENT_KEY_SECRET_ID  Secrets Manager id holding the PEM private key
  XAA_LEG1_SUBJECT      access_token (default) | id_token | auto
  ID_TOKEN_HEADER       X-Okta-Id-Token (only read when XAA_LEG1_SUBJECT is not access_token)
  LOG_CLAIMS            "true" to log token CLAIMS (never token material)
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import boto3
import jwt as pyjwt

ORG_URL = os.environ.get("OKTA_ORG_URL", "").rstrip("/")
RESOURCE_AS = os.environ.get("RESOURCE_AS_ISSUER", "").rstrip("/")
RESOURCE_SCOPE = os.environ.get("RESOURCE_SCOPE", "todos.read")
AGENT_CLIENT_ID = os.environ.get("AI_AGENT_CLIENT_ID", "")
AGENT_KEY_KID = os.environ.get("AI_AGENT_KEY_KID", "")
KEY_SECRET_ID = os.environ.get("AI_AGENT_KEY_SECRET_ID", "")
ID_TOKEN_HEADER = os.environ.get("ID_TOKEN_HEADER", "X-Okta-Id-Token").lower()

# Which token leg 1 exchanges. "access_token" uses the inbound bearer the gateway already
# validated, so nothing extra travels with the request -- it needs a Machine access entry
# in Okta. "id_token" reads ID_TOKEN_HEADER instead and needs the User access binding.
# "auto" prefers the bearer and falls back to the header, which is useful while migrating.
LEG1_SUBJECT = os.environ.get("XAA_LEG1_SUBJECT", "access_token").lower()
LOG_CLAIMS = os.environ.get("LOG_CLAIMS", "true").lower() == "true"

TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
ID_JAG = "urn:ietf:params:oauth:token-type:id-jag"
TT_ID_TOKEN = "urn:ietf:params:oauth:token-type:id_token"
TT_ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"  # nosec B105
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

# subject -> (T_tool, expires_at). Warm-container cache; a multi-tenant deployment
# would use something shared and evictable.
_TOOL_TOKENS: dict[str, tuple[str, float]] = {}
_SKEW = 60
_MAX_ENTRIES = 512
_private_key: str | None = None


class InterceptorError(RuntimeError):
    pass


def log(event: str, **fields) -> None:
    """Structured single-line logs, correlatable across the four log groups."""
    print(json.dumps({"event": event, **fields}, default=str))


def private_key() -> str:
    """Fetch the AI Agent's PEM once per container."""
    global _private_key
    if _private_key is None:
        if not KEY_SECRET_ID:
            raise InterceptorError("AI_AGENT_KEY_SECRET_ID is not set")
        client = boto3.client("secretsmanager")
        _private_key = client.get_secret_value(SecretId=KEY_SECRET_ID)["SecretString"]
    return _private_key


def claims_of(token: str) -> dict:
    """Decode a JWT payload without verifying.

    Safe here: leg 1 and leg 2 verify cryptographically at Okta, and the resource API
    verifies T_tool against the AS 2 JWKS. This is only to read `sub` for the cache
    key and to log claims.
    """
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (ValueError, IndexError, json.JSONDecodeError):
        return {}


def client_assertion(token_endpoint: str) -> str:
    """private_key_jwt assertion. The same key authenticates BOTH legs."""
    now = int(time.time())
    return pyjwt.encode(
        {
            "iss": AGENT_CLIENT_ID,
            "sub": AGENT_CLIENT_ID,
            "aud": token_endpoint,
            "iat": now,
            "exp": now + 300,
            "jti": base64.urlsafe_b64encode(os.urandom(16)).decode().rstrip("="),
        },
        private_key(),
        algorithm="RS256",
        headers={"kid": AGENT_KEY_KID},
    )


def post_form(url: str, form: dict) -> dict:
    body = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        raise InterceptorError(f"{url} -> HTTP {exc.code}: {detail}") from exc


def leg1_id_jag(subject_token: str, subject_token_type: str) -> str:
    """Exchange the caller's token for an ID-JAG at the ORG server.

    Only the org server mints ID-JAGs, and it accepts exactly two subject token types --
    `id_token` and `access_token`. Anything else, including the generic `jwt`, is refused
    with "'subject_token_type' is invalid or not supported".

    Which one you can use is an Okta *configuration* question, not a protocol one:

      * `access_token` requires a **Machine access** entry on the AI Agent naming the
        token's `cid` as a caller, on the custom AS that issued it, for the audience the
        token carries. Without it: "no delegation policy authorizes this token".
      * `id_token` requires the **User access** binding, and the token must come from the
        app bound there.

    This sample prefers `access_token`, because that is the credential the gateway has
    already validated and handed us -- no second token needs to travel with the request.
    """
    endpoint = f"{ORG_URL}/oauth2/v1/token"
    body = post_form(
        endpoint,
        {
            "grant_type": TOKEN_EXCHANGE,
            "requested_token_type": ID_JAG,
            "subject_token": subject_token,
            "subject_token_type": subject_token_type,
            "audience": RESOURCE_AS,
            "scope": RESOURCE_SCOPE,
            "client_id": AGENT_CLIENT_ID,
            "client_assertion_type": CLIENT_ASSERTION_TYPE,
            "client_assertion": client_assertion(endpoint),
        },
    )
    if "access_token" not in body:
        raise InterceptorError(f"leg 1 returned no token: {json.dumps(body)[:200]}")
    return body["access_token"]


def leg2_resource_token(id_jag: str) -> tuple[str, int]:
    """Redeem the ID-JAG for T_tool at the resource AS."""
    endpoint = f"{RESOURCE_AS}/v1/token"
    body = post_form(
        endpoint,
        {
            "grant_type": JWT_BEARER,
            "assertion": id_jag,
            "client_id": AGENT_CLIENT_ID,
            "client_assertion_type": CLIENT_ASSERTION_TYPE,
            "client_assertion": client_assertion(endpoint),
        },
    )
    if "access_token" not in body:
        raise InterceptorError(f"leg 2 returned no token: {json.dumps(body)[:200]}")
    return body["access_token"], int(body.get("expires_in", 3600))


def cache_put(subject: str, token: str, expires_at: float) -> None:
    now = time.time()
    for key, (_, exp) in list(_TOOL_TOKENS.items()):
        if exp <= now:
            del _TOOL_TOKENS[key]
    if len(_TOOL_TOKENS) >= _MAX_ENTRIES and subject not in _TOOL_TOKENS:
        del _TOOL_TOKENS[min(_TOOL_TOKENS, key=lambda k: _TOOL_TOKENS[k][1])]
    _TOOL_TOKENS[subject] = (token, expires_at)


def resource_token_for(subject_token: str, subject_token_type: str, trace_id: str) -> str:
    """Cached T_tool for this user, minting one through both legs when needed."""
    subject = claims_of(subject_token).get("sub", "unknown")
    hit = _TOOL_TOKENS.get(subject)
    if hit and hit[1] - _SKEW > time.time():
        log("t_tool.cache_hit", subject=subject, trace_id=trace_id)
        return hit[0]

    started = time.time()
    id_jag = leg1_id_jag(subject_token, subject_token_type)
    if LOG_CLAIMS:
        c = claims_of(id_jag)
        log(
            "idjag.minted",
            trace_id=trace_id,
            iss=c.get("iss"),
            aud=c.get("aud"),
            sub=c.get("sub"),
            scp=c.get("scp"),
            act=c.get("act"),
            subject_token_type=subject_token_type,
            ttl_s=int(c.get("exp", 0) - time.time()),
        )
    token, expires_in = leg2_resource_token(id_jag)
    if LOG_CLAIMS:
        c = claims_of(token)
        log(
            "t_tool.minted",
            trace_id=trace_id,
            iss=c.get("iss"),
            aud=c.get("aud"),
            sub=c.get("sub"),
            cid=c.get("cid"),
            act_sub=(c.get("act") or {}).get("sub"),
            act=c.get("act"),
            scp=c.get("scp"),
            ms=int((time.time() - started) * 1000),
        )
    cache_put(subject, token, time.time() + expires_in)
    return token


def pick_subject_token(lower: dict) -> tuple[str | None, str, str]:
    """Choose what to send as leg 1's `subject_token`.

    Returns (token, subject_token_type, where it came from). The inbound bearer is
    preferred: the gateway has already validated it, and using it means the request
    carries one credential instead of two. Falling back to the ID token header keeps the
    sample working on orgs that have the User access binding but not Machine access.
    """
    bearer = (lower.get("authorization") or "").removeprefix("Bearer ").removeprefix("bearer ").strip()
    id_token = lower.get(ID_TOKEN_HEADER)

    if LEG1_SUBJECT == "id_token":
        return id_token, TT_ID_TOKEN, ID_TOKEN_HEADER
    if LEG1_SUBJECT == "access_token":
        return (bearer or None), TT_ACCESS_TOKEN, "authorization"
    # auto
    if bearer:
        return bearer, TT_ACCESS_TOKEN, "authorization"
    return id_token, TT_ID_TOKEN, ID_TOKEN_HEADER


def handler(event, context):
    req = (event.get("mcp") or {}).get("gatewayRequest") or {}
    headers = dict(req.get("headers") or {})
    lower = {k.lower(): v for k, v in headers.items()}
    trace_id = lower.get("x-amzn-trace-id", "")
    method = ((req.get("body") or {}) or {}).get("method")
    tool = (((req.get("body") or {}).get("params") or {}) or {}).get("name")

    log("intercept.start", trace_id=trace_id, method=method, tool=tool)

    # Only tools/call reaches the upstream API, so only it needs a resource token.
    # The interceptor is invoked for EVERY MCP method -- initialize,
    # notifications/initialized, tools/list -- and exchanging on the first of those
    # burns an ID-JAG even when the client never calls a tool. Okta allows 250 per
    # user, per resource, per month on plain SSO, so this matters.
    if method != "tools/call":
        log("intercept.skip", trace_id=trace_id, method=method, reason="not a tool call")
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {"transformedGatewayRequest": {"headers": headers, "body": req.get("body")}},
        }

    subject_token, subject_token_type, source = pick_subject_token(lower)
    if not subject_token:
        # Pass the request through untouched rather than injecting junk: a non-JWT in
        # Authorization would break policy evaluation for the whole request.
        log(
            "intercept.no_subject_token",
            trace_id=trace_id,
            mode=LEG1_SUBJECT,
            looked_in=source,
            header=ID_TOKEN_HEADER,
            method=method,
        )
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {"transformedGatewayRequest": {"headers": headers, "body": req.get("body")}},
        }

    try:
        log("intercept.subject", trace_id=trace_id, mode=LEG1_SUBJECT, source=source, type=subject_token_type)
        t_tool = resource_token_for(subject_token, subject_token_type, trace_id)
    except InterceptorError as exc:
        # Same reasoning: leave Authorization alone so the failure surfaces as a clean
        # 401/403 from the API rather than an opaque policy-evaluation error.
        log("intercept.exchange_failed", trace_id=trace_id, error=str(exc)[:400])
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {"transformedGatewayRequest": {"headers": headers, "body": req.get("body")}},
        }

    out = {k: v for k, v in headers.items() if k.lower() != "authorization"}
    out["Authorization"] = f"Bearer {t_tool}"
    log("intercept.injected", trace_id=trace_id, tool=tool)
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"headers": out, "body": req.get("body")}},
    }
