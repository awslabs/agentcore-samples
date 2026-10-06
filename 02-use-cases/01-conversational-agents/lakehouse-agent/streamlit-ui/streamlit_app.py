"""
Streamlit UI for the Lakehouse Agent — dual IdP (Cognito or Okta).

The active identity provider is chosen once, in notebook 01's Step-0 cell, and
persisted to SSM as IDP_PROVIDER ("cognito" | "okta"). This UI reads that flag
once at startup and branches ONLY the login widget + config load; the chat UI,
agent invocation, and the per-persona tools panel are IdP-agnostic and shared.
"""

# The Okta /v1/token endpoint already authenticated this token via HTTPS +
# client_secret, so a display-side decode does not require independent
# signature verification.
import asyncio
import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import uuid
from typing import Optional

import boto3
import jwt  # PyJWT — unverified decode of id_token for display only (Okta path).
import nest_asyncio
import requests
import streamlit as st
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# utils/ lives one level up from streamlit-ui/; put it on the path so we can read
# the IDP_PROVIDER flag through the same shared helper the notebooks use.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.idp_config import get_idp_provider

# Streamlit's script thread may or may not already own a running event loop
# depending on the version; apply nest_asyncio as belt-and-suspenders. The
# primary isolation, though, is `_run_async` below, which runs each coroutine in
# a short-lived worker thread with its OWN fresh loop — robust regardless of the
# script thread's loop state.
nest_asyncio.apply()

st.set_page_config(page_title="Lakehouse Data Assistant", page_icon="🏥", layout="wide")


def _resolve_region() -> str:
    """Resolve the AWS region with the tutorial's standard fallback chain."""
    session = boto3.Session()
    return session.region_name or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


# Read the IdP flag ONCE from SSM (set by notebook 01's Step-0 cell). The whole
# app — config load, login widget, badge — branches on this single value.
try:
    IDP_PROVIDER = get_idp_provider(boto3.client("ssm", region_name=_resolve_region()))
except Exception as _flag_err:  # surfaced as a friendly banner, never a traceback
    st.error(f"❌ Could not read IDP_PROVIDER from SSM: {_flag_err}")
    st.info("Run `01-deploy-idp.ipynb` first to choose and persist the IdP flag.")
    st.stop()


def _run_async(coro):
    """Run an async coroutine to completion from Streamlit's script thread.

    Executes it in a short-lived worker thread with its own fresh event loop
    (`asyncio.run`), so it works whether or not the script thread already has a
    running loop. Exceptions are re-raised to the caller for soft handling.
    """
    box = {}

    def _worker():
        try:
            box["value"] = asyncio.run(coro)
        except Exception as e:  # surfaced to caller; never crashes the app
            box["error"] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def fetch_interceptor_tools(gateway_url: str, token: str):
    """Return the sorted tool names GW1 (claims gateway) advertises to THIS
    token's persona.

    The response interceptor filters `tools/list` by the caller's group, so the
    result differs per persona — that's the tool-gating visual. IdP-agnostic:
    GW1 exists on both the Cognito and Okta paths. The token is used only as the
    bearer header; it is never rendered.
    """
    headers = {"Authorization": f"Bearer {token}"}

    async def _run():
        async with (
            streamablehttp_client(gateway_url, headers=headers) as (read, write, _),
            ClientSession(read, write) as sess,
        ):
            await sess.initialize()
            resp = await sess.list_tools()
            names = []
            for t in resp.tools:
                n = t.name
                # Gateway prefixes tool names as "target___tool"; show the bare tool.
                names.append(n.split("___", 1)[1] if "___" in n else n)
            return sorted(names)

    return _run_async(_run())


# Authorization Code + PKCE redirect URI (Okta path) per design §7g.
# Cross-file coupling: this constant MUST match the URI registered in
# `deployment/1-okta-setup/setup_okta.py` (`redirectUris`). If you change this
# value, also amend setup_okta.py atomically so the writer and consumer of the
# redirect URI stay in sync.
REDIRECT_URI = "http://localhost:8501/"

# ── Okta Authorization Code + PKCE, run server-side in this app ──────────────────
# The app IS the web server, so its own origin is the redirect target and no second
# listener or popup is involved. Okta returns the authorization code as a query
# parameter on this page, which the code below exchanges for tokens.
#
# `claims.query` is the smallest scope sufficient for the interceptor side. Scope-name
# form is the bare `claims.query` (Okta convention), NOT `<audience>/<scope>` prefixed
# (Cognito convention) — the prefixed form returns `invalid_scope`. `groups` is a CLAIM
# rather than a scope on this authorization server (alwaysIncludeInToken), so the access
# token carries it regardless of the scopes requested.
OKTA_SCOPE = "openid profile email claims.query"

# How long an in-flight login may take before its PKCE verifier is discarded. Long
# enough for a password change plus authenticator enrolment on a first login; short
# enough that an abandoned attempt does not leave a verifier resident.
OKTA_LOGIN_TTL_SECONDS = 15 * 60


@st.cache_resource
def _okta_pending_logins() -> dict:
    """Server-side store for in-flight logins, keyed by the OAuth `state` value.

    WHY THIS EXISTS: the PKCE verifier is created before the browser leaves for Okta
    and is needed after it comes back, but the return trip is a full page navigation,
    which gives Streamlit a NEW session. `st.session_state` does not survive it. This
    store lives in the server process instead, so it does.

    SECURITY-RELEVANT, not plumbing:
      - the returned `state` is looked up here, so a value we never issued is rejected;
      - entries are SINGLE-USE (popped on lookup), so a replayed authorization code
        cannot be paired with the same verifier twice;
      - entries EXPIRE, so an abandoned login does not leave a verifier resident.
    """
    return {}


def _okta_new_pkce_login(login_hint: str = "") -> tuple[str, str]:
    """Create one login attempt: returns (state, verifier) and records it server-side.

    S256 only. The verifier is high-entropy and never leaves the server; only its
    SHA-256 challenge is sent to Okta.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    store = _okta_pending_logins()
    _okta_expire_pending(store)
    store[state] = {
        "verifier": verifier,
        "created_at": time.time(),
        "login_hint": login_hint,
    }
    return state, verifier


def _okta_expire_pending(store: dict, now: float | None = None) -> int:
    """Drop expired entries. Returns how many were removed."""
    now = time.time() if now is None else now
    stale = [k for k, v in store.items() if now - v.get("created_at", 0) > OKTA_LOGIN_TTL_SECONDS]
    for k in stale:
        store.pop(k, None)
    return len(stale)


def _okta_claim_pending(state: str) -> dict | None:
    """Single-use lookup of an in-flight login. Returns None if unknown or expired.

    Popping before validating is deliberate: even a state that turns out to be expired
    is consumed, so a replay cannot get a second attempt at it.
    """
    store = _okta_pending_logins()
    _okta_expire_pending(store)
    if not state:
        return None
    return store.pop(state, None)


def _okta_pkce_challenge(verifier: str) -> str:
    """S256 challenge for a verifier: base64url(sha256(verifier)), unpadded."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _okta_authorize_url(config: dict, state: str, verifier: str, login_hint: str = "") -> str:
    """Build the /v1/authorize URL for this login attempt."""
    params = {
        "client_id": config["okta_app_client_id"],
        "response_type": "code",
        "scope": OKTA_SCOPE,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": _okta_pkce_challenge(verifier),
        "code_challenge_method": "S256",
        # Force Okta to re-prompt for credentials rather than silently reusing an
        # existing SSO cookie, so a reader can switch test users without signing out
        # of Okta first.
        "prompt": "login",
    }
    if login_hint:
        params["login_hint"] = login_hint
    base = f"https://{config['okta_org_url']}/oauth2/{config['okta_auth_server_id']}"
    return f"{base}/v1/authorize?" + urllib.parse.urlencode(params)


def _okta_exchange_code(config: dict, code: str, verifier: str) -> dict:
    """Exchange an authorization code for tokens. Confidential client, HTTP Basic auth.

    Raises RuntimeError with the provider's message on a non-2xx response, so the caller
    can surface something actionable instead of a bare failure.
    """
    base = f"https://{config['okta_org_url']}/oauth2/{config['okta_auth_server_id']}"
    basic = base64.b64encode(f"{config['okta_app_client_id']}:{config['okta_app_client_secret']}".encode()).decode()
    response = requests.post(
        f"{base}/v1/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        timeout=30,
    )
    if response.status_code // 100 != 2:
        raise RuntimeError(f"token exchange failed (HTTP {response.status_code}): {response.text[:300]}")
    tokens = response.json()
    if not tokens.get("access_token"):
        raise RuntimeError(f"token response carried no access_token (keys: {sorted(tokens)})")
    return tokens


def _okta_identity_from_tokens(tokens: dict) -> str:
    """Best-effort display identity. Unverified decode — display only.

    The tokens were just issued by the provider over TLS in response to our own
    client-authenticated request, so a display-side decode does not require independent
    signature verification. Nothing security-relevant is derived from this value.
    """

    def _claims_or_none(raw_token: str) -> dict | None:
        try:
            return jwt.decode(raw_token, options={"verify_signature": False})
        except Exception as decode_error:
            # Display-only, so a malformed token must never break sign-in — but say so
            # rather than swallowing it silently.
            print(f"⚠️  Could not decode a token for display: {decode_error}")
            return None

    for token_key in ("id_token", "access_token"):
        raw = tokens.get(token_key)
        claims = _claims_or_none(raw) if raw else None
        if not claims:
            continue
        identity = claims.get("email") or claims.get("preferred_username") or claims.get("sub")
        if identity:
            return str(identity)
    return "(unknown)"


# Per-path response labels. The agent response is free text and does NOT expose
# which gateway/tool served it, so we label the path from the clicked example's
# metadata (free-typed prompts stay unlabeled). Two facets of the demo:
#   interceptor = GW1 (claims gateway) → Athena claims: row/column security + tool gating
#   obo         = GW2 (notes gateway)  → OpenSearch notes: per-user isolation
PATH_LABELS = {
    "interceptor": "🔑 Path: Claims Gateway → Athena (claims) · row/column security by role",
    "obo": "🔑 Path: Notes Gateway → OpenSearch (notes) · per-user isolation",
}

# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "access_token" not in st.session_state:
    st.session_state.access_token = None
if "id_token" not in st.session_state:
    st.session_state.id_token = None
if "user_email" not in st.session_state:
    st.session_state.user_email = None
if "runtime_arn" not in st.session_state:
    st.session_state.runtime_arn = ""
if "idp_config" not in st.session_state:
    st.session_state.idp_config = {}
if "example_prompt" not in st.session_state:
    st.session_state.example_prompt = None
if "persona_tools" not in st.session_state:
    st.session_state.persona_tools = None  # cached per login; see the Tools panel


def _lakehouse_save_token_if_enabled(access_token, user_email):
    """Gated dev/testing convenience — DISABLED by default; NEVER for normal use.

    A no-op UNLESS the environment variable ``LAKEHOUSE_SAVE_TOKENS == "1"``.
    When enabled, it persists the just-issued access token to
    ``<.tmp>/<persona>_token.txt`` so the OPTIONAL multi-user isolation notebook
    (``07-optional-multi-user-isolation-test.ipynb``) can pick tokens up
    automatically instead of manual copy/paste. ``<persona>`` is the email
    local-part (``policyholder001@example.com`` -> ``policyholder001``).

    The ``<.tmp>`` directory is resolved by walking UP from cwd for an existing
    ``.tmp`` — identical to nb07's ``_find_tmp_dir`` — so writer (this app) and
    reader (nb07) rendezvous on the same file. ``.tmp`` is outside the git repo,
    so saved tokens can never be committed.

    SECURITY: this NEVER displays the token. When enabled it may show the file
    PATH only (not the token value) as minimal dev feedback. Off by default, it
    changes no behavior whatsoever.
    """
    # OFF by default — the entire feature is gated behind this single check.
    if os.getenv("LAKEHOUSE_SAVE_TOKENS") != "1":
        return
    # Nothing safe/useful to write.
    if not access_token or not user_email or "@" not in user_email:
        return

    # Resolve <.tmp> the SAME way nb07 READS it: walk up from cwd for an existing
    # `.tmp`. Fall back to creating one at cwd only if none exists anywhere up the
    # tree (the workspace `.tmp` normally already exists).
    d = os.path.abspath(os.getcwd())
    tmp_dir = None
    while True:
        cand = os.path.join(d, ".tmp")
        if os.path.isdir(cand):
            tmp_dir = cand
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    if tmp_dir is None:
        tmp_dir = os.path.join(os.getcwd(), ".tmp")
        os.makedirs(tmp_dir, exist_ok=True)

    persona = user_email.split("@", 1)[0]
    token_path = os.path.join(tmp_dir, f"{persona}_token.txt")
    # Single line, no trailing newline (nb07 `.strip()`s on read regardless).
    with open(token_path, "w") as f:
        f.write(access_token)

    # Dev feedback: file PATH only — the token value is never rendered.
    st.caption(f"🔧 dev: saved token to .tmp/{persona}_token.txt")


def load_config_from_ssm():
    """Load configuration from SSM Parameter Store (branched by IDP_PROVIDER)."""
    try:
        region = _resolve_region()
        ssm = boto3.client("ssm", region_name=region)

        config = {}
        # Shared params both IdPs need.
        params = {
            "runtime_arn": "/app/lakehouse-agent/agent-runtime-arn",
            # GW1 claims-gateway URL — consumed by the shared per-persona tools
            # panel (fetch_interceptor_tools); written by 05a on BOTH paths. (The
            # fork called this key interceptor-gateway-url; the consolidated
            # tutorial uses upstream's canonical gateway-url.)
            "gateway_url": "/app/lakehouse-agent/gateway-url",
        }
        if IDP_PROVIDER == "okta":
            # Okta only needs the org URL + auth-server id to compose the
            # /v1/authorize and /v1/token endpoints, plus the OIDC client id.
            params.update(
                {
                    "okta_org_url": "/app/lakehouse-agent/okta-org-url",
                    "okta_auth_server_id": "/app/lakehouse-agent/okta-auth-server-id",
                    "okta_app_client_id": "/app/lakehouse-agent/okta-app-client-id",
                }
            )
        else:
            params.update(
                {
                    "cognito_user_pool_id": "/app/lakehouse-agent/cognito-user-pool-id",
                    "cognito_app_client_id": "/app/lakehouse-agent/cognito-app-client-id",
                    "cognito_domain": "/app/lakehouse-agent/cognito-domain",
                    "cognito_region": "/app/lakehouse-agent/cognito-region",
                }
            )

        for key, param_name in params.items():
            try:
                response = ssm.get_parameter(Name=param_name)
                config[key] = response["Parameter"]["Value"]
            except Exception:
                config[key] = None

        # Okta needs the client secret in-config for the authorization-code token
        # exchange (a confidential Auth-Code+PKCE client). Cognito does NOT: its
        # authenticate_user / set_new_password fetch the secret themselves on
        # demand (SecureString, WithDecryption), so we never hold a decrypted
        # secret in session state on the Cognito path.
        if IDP_PROVIDER == "okta":
            try:
                response = ssm.get_parameter(
                    Name="/app/lakehouse-agent/okta-app-client-secret",
                    WithDecryption=True,
                )
                config["okta_app_client_secret"] = response["Parameter"]["Value"]
            except Exception:
                config["okta_app_client_secret"] = None

        config["region"] = region
        return config
    except Exception as e:
        st.error(f"Failed to load config from SSM: {e}")
        # Return config with at least region set
        return {"region": _resolve_region()}


def authenticate_user(username: str, password: str, user_pool_id: str, client_id: str, region: str) -> dict | None:
    """Authenticate user with Cognito using USER_PASSWORD_AUTH flow"""
    try:
        client = boto3.client("cognito-idp", region_name=region)

        # Get client secret from SSM
        ssm = boto3.client("ssm", region_name=region)
        try:
            client_secret = ssm.get_parameter(
                Name="/app/lakehouse-agent/cognito-app-client-secret",
                WithDecryption=True,
            )["Parameter"]["Value"]
        except Exception:
            st.error("❌ Could not retrieve client secret from SSM")
            return None

        # Calculate SECRET_HASH
        import base64
        import hashlib
        import hmac

        message = bytes(username + client_id, "utf-8")
        secret = bytes(client_secret, "utf-8")
        secret_hash = base64.b64encode(
            hmac.new(secret, message, digestmod=hashlib.sha256).digest()
        ).decode()  # codeql[py/weak-sensitive-data-hashing] - HMAC-SHA256 for Cognito SECRET_HASH, not password hashing

        response = client.admin_initiate_auth(
            UserPoolId=user_pool_id,
            ClientId=client_id,
            AuthFlow="ADMIN_NO_SRP_AUTH",
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": password,
                "SECRET_HASH": secret_hash,
            },
        )

        if "ChallengeName" in response and response["ChallengeName"] == "NEW_PASSWORD_REQUIRED":
            return {
                "challenge": "NEW_PASSWORD_REQUIRED",
                "session": response["Session"],
            }

        if "AuthenticationResult" in response:
            return {
                "access_token": response["AuthenticationResult"]["AccessToken"],
                "id_token": response["AuthenticationResult"]["IdToken"],
                "refresh_token": response["AuthenticationResult"].get("RefreshToken"),
            }

        return None

    except client.exceptions.NotAuthorizedException:
        st.error("❌ Invalid username or password")
        return None
    except Exception as e:
        st.error(f"❌ Authentication failed: {e}")
        return None


def set_new_password(
    username: str,
    new_password: str,
    session: str,
    user_pool_id: str,
    client_id: str,
    region: str,
) -> dict | None:
    """Set new password for user with NEW_PASSWORD_REQUIRED challenge"""
    try:
        client = boto3.client("cognito-idp", region_name=region)

        # Get client secret from SSM
        ssm = boto3.client("ssm", region_name=region)
        try:
            client_secret = ssm.get_parameter(
                Name="/app/lakehouse-agent/cognito-app-client-secret",
                WithDecryption=True,
            )["Parameter"]["Value"]
        except Exception:
            st.error("❌ Could not retrieve client secret from SSM")
            return None

        # Calculate SECRET_HASH
        import base64
        import hashlib
        import hmac

        message = bytes(username + client_id, "utf-8")
        secret = bytes(client_secret, "utf-8")
        secret_hash = base64.b64encode(
            hmac.new(secret, message, digestmod=hashlib.sha256).digest()
        ).decode()  # codeql[py/weak-sensitive-data-hashing] - HMAC-SHA256 for Cognito SECRET_HASH, not password hashing

        response = client.admin_respond_to_auth_challenge(
            UserPoolId=user_pool_id,
            ClientId=client_id,
            ChallengeName="NEW_PASSWORD_REQUIRED",
            ChallengeResponses={
                "USERNAME": username,
                "NEW_PASSWORD": new_password,
                "SECRET_HASH": secret_hash,
            },
            Session=session,
        )

        if "AuthenticationResult" in response:
            return {
                "access_token": response["AuthenticationResult"]["AccessToken"],
                "id_token": response["AuthenticationResult"]["IdToken"],
                "refresh_token": response["AuthenticationResult"].get("RefreshToken"),
            }

        return None

    except Exception as e:
        st.error(f"❌ Failed to set new password: {e}")
        return None


def invoke_agent(runtime_arn: str, prompt: str, access_token: str, id_token: str, region: str) -> str:
    """Invoke AgentCore Runtime with OAuth bearer token via HTTPS (IdP-agnostic)."""
    # Reset per-turn tool telemetry; the JSON branch below stashes the real
    # tools_used (from the agent's toolUse event log) for the caller to render.
    st.session_state["last_tools_used"] = None
    try:
        # URL encode the agent ARN
        escaped_agent_arn = urllib.parse.quote(runtime_arn, safe="")

        # Construct the AWS API endpoint URL
        url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_agent_arn}/invocations?qualifier=DEFAULT"

        # Set up headers with bearer token
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": st.session_state.session_id,
        }

        # Prepare payload. The access token is NOT sent in the body: the agent reads
        # it from the Authorization header above, which the runtime validates and
        # forwards. The agent still accepts a body token as a transitional fallback
        # and logs a warning when it uses one, so leaving it out here is what makes
        # that warning meaningful.
        payload = {"prompt": prompt, "id_token": id_token}

        st.info("🔗 Invoking AgentCore Runtime with OAuth")

        # Timeout is a (connect, read) tuple: a real multi-tool agent turn can run
        # well past 60s (each Athena tool call ≈14s and turns chain several), so the
        # read timeout is 300s. No retries: the invocation is non-idempotent and
        # already long-running.
        with st.spinner("Agent is working…"):
            response = requests.post(url, headers=headers, json=payload, timeout=(10, 300))

        # Check for errors
        if response.status_code != 200:
            error_msg = f"HTTP {response.status_code}"
            try:
                error_detail = response.json()
                error_msg += f": {error_detail}"
            except (json.JSONDecodeError, ValueError):
                error_msg += f": {response.text}"
            return f"❌ Error: {error_msg}"

        # Handle streaming response (text/event-stream)
        content_type = response.headers.get("Content-Type", "")

        if "text/event-stream" in content_type:
            # Parse SSE (Server-Sent Events) format
            content = []
            for line in response.text.split("\n"):
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str:
                        try:
                            data = json.loads(data_str)
                            # Extract content from various possible formats
                            if isinstance(data, dict):
                                if "content" in data:
                                    content.append(str(data["content"]))
                                elif "response" in data:
                                    content.append(str(data["response"]))
                                elif "result" in data:
                                    content.append(str(data["result"]))
                                else:
                                    content.append(str(data))
                            else:
                                content.append(str(data))
                        except json.JSONDecodeError:
                            content.append(data_str)

            return "\n".join(content) if content else "⚠️ No response received"

        else:
            # Handle JSON response
            try:
                result = response.json()
                if isinstance(result, dict):
                    # Stash real per-turn tools for the caller to render (fail-soft).
                    st.session_state["last_tools_used"] = result.get("tools_used")
                    if "content" in result:
                        return result["content"]
                    elif "response" in result:
                        return result["response"]
                    elif "result" in result:
                        return result["result"]
                return str(result)
            except json.JSONDecodeError:
                return response.text

    except requests.exceptions.RequestException as e:
        return f"❌ Request error: {e!s}"
    except Exception as e:
        return f"❌ Error: {e!s}"


# Load configuration from SSM on first run
if not st.session_state.idp_config:
    with st.spinner("Loading configuration from SSM..."):
        st.session_state.idp_config = load_config_from_ssm()
        if st.session_state.idp_config.get("runtime_arn"):
            st.session_state.runtime_arn = st.session_state.idp_config["runtime_arn"]

# Sidebar configuration
with st.sidebar:
    st.title("🏥 Claims Assistant")
    # IdP badge (non-load-bearing): shows the active provider + GW2 notes-auth model.
    # Rendered from shared code ABOVE the login widget, so it is identical on both paths
    # and visible before sign-in. The provider name is emphasised rather than captioned
    # because its job is to answer "which path am I on?" at a glance -- the two login
    # experiences differ by design (a password form here, a redirect to the provider on
    # Okta), and without this a reader reporting a problem cannot say which they saw.
    st.markdown(f"🔐 **Identity Provider: {IDP_PROVIDER.title()}**")
    st.caption("Notes auth: OBO token exchange" if IDP_PROVIDER == "okta" else "Notes auth: REQUEST interceptor")
    st.markdown("---")

    # Login section — branches by IdP; both populate the shared session contract
    # (access_token / id_token / user_email).
    if not st.session_state.access_token:
        if IDP_PROVIDER == "okta":
            # ── Okta: Authorization Code + PKCE flow per design §7g ──────────
            with st.expander("🔐 User Login", expanded=True):
                config = st.session_state.idp_config
                required_keys = (
                    "okta_org_url",
                    "okta_auth_server_id",
                    "okta_app_client_id",
                    "okta_app_client_secret",
                )
                if all(config.get(k) for k in required_keys):
                    # The redirect from Okta lands back on THIS page with ?code= and
                    # ?state=. Handle that first: if a code is present, this rerun is
                    # the tail of a login rather than the start of one.
                    query = st.query_params
                    returned_code = query.get("code")
                    returned_state = query.get("state")
                    returned_error = query.get("error")

                    if returned_error:
                        description = query.get("error_description") or ""
                        st.error(f"❌ Okta returned an error: {returned_error} {description}".strip())
                        # Clear the parameters so the error does not reappear on every
                        # subsequent rerun.
                        st.query_params.clear()
                    elif returned_code:
                        # Single-use, expiring lookup. An unknown or stale state means
                        # this code was not requested by this server.
                        pending = _okta_claim_pending(returned_state or "")
                        if pending is None:
                            st.error(
                                "❌ Sign-in could not be verified: the returned state does not match "
                                "an in-flight login. Start the sign-in again. (This also happens if the "
                                "attempt sat unfinished for more than "
                                f"{OKTA_LOGIN_TTL_SECONDS // 60} minutes, or if the page was reloaded "
                                "with a used link.)"
                            )
                            st.query_params.clear()
                        else:
                            try:
                                tokens = _okta_exchange_code(config, returned_code, pending["verifier"])
                            except Exception as exchange_error:
                                st.error(f"❌ Could not complete sign-in: {exchange_error}")
                                st.query_params.clear()
                            else:
                                st.session_state.access_token = tokens.get("access_token")
                                st.session_state.id_token = tokens.get("id_token")
                                st.session_state.user_email = _okta_identity_from_tokens(tokens)
                                # Gated dev convenience (LAKEHOUSE_SAVE_TOKENS=1); no-op
                                # by default. Never surfaces the token — see the helper
                                # docstring.
                                _lakehouse_save_token_if_enabled(
                                    st.session_state.access_token,
                                    st.session_state.user_email,
                                )
                                st.session_state.persona_tools = None  # refetch for the new persona
                                # Clear ?code= BEFORE rerunning. Streamlit reruns
                                # frequently, and a code left in the URL would be
                                # re-submitted against a code the provider has already
                                # consumed, which fails on the second attempt.
                                st.query_params.clear()
                                st.success(f"✅ Logged in as {st.session_state.user_email}")
                                st.rerun()
                    else:
                        # Start of a login. The optional hint pre-fills the username at
                        # Okta, which makes switching between test users quicker; it is
                        # only a hint and the provider still prompts for credentials.
                        login_hint = st.text_input(
                            "Email (optional)",
                            key="okta_login_hint",
                            placeholder="policyholder001@example.com",
                            help="Pre-fills the username on the Okta sign-in page. Leave blank to type it there.",
                        )
                        # Mint the attempt ONCE per session, not once per rerun.
                        # Streamlit reruns on every keystroke in the field above, and
                        # minting per rerun would add a pending entry each time. Reusing
                        # one attempt is also correct: it is still single-use and still
                        # expiring, and the link always carries the state we hold.
                        if "okta_pkce_attempt" not in st.session_state:
                            st.session_state.okta_pkce_attempt = _okta_new_pkce_login(login_hint.strip())
                        state, verifier = st.session_state.okta_pkce_attempt
                        # Rendered as an anchor with target="_self" rather than with
                        # st.link_button, which always opens a NEW tab. A new tab leaves
                        # the original one sitting at a logged-out login form while the
                        # signed-in session lives somewhere else — it looks broken even
                        # though the sign-in succeeded. Navigating the current tab keeps
                        # the flow in one place: this tab leaves for Okta and comes back
                        # signed in.
                        st.markdown(
                            f"""
                            <a href="{_okta_authorize_url(config, state, verifier, login_hint.strip())}"
                               target="_self"
                               style="display:block; padding:0.5rem 1rem; text-align:center;
                                      border:1px solid rgba(49,51,63,0.2); border-radius:0.5rem;
                                      text-decoration:none; font-weight:600;">
                               🔑 Login with Okta
                            </a>
                            """,
                            unsafe_allow_html=True,
                        )
                        st.caption("Takes this tab to the Okta sign-in page, then returns here signed in.")
                else:
                    st.error("❌ Okta not configured. Run `01-deploy-idp.ipynb` first.")
        else:
            # ── Cognito: password-grant flow (upstream, preserved) ───────────
            with st.expander("🔐 User Login", expanded=True):
                # The test-persona password is documented in notebook 08, immediately
                # before the reader is told to sign in. It is deliberately NOT shown
                # here: the app cannot know which value the setup script actually
                # applied, so a credential printed in this panel can silently disagree
                # with reality -- and a hint that is confidently wrong is worse than no
                # hint, because it is the one value a reader will try.
                st.caption("Test-persona password: see notebook 08.")
                st.markdown("---")

                # Test users dropdown
                test_users = [
                    "policyholder001@example.com",
                    "policyholder002@example.com",
                    "adjuster001@example.com",
                    "adjuster002@example.com",
                    "admin@example.com",
                ]
                username = st.selectbox("Email", options=test_users, index=0)
                password = st.text_input("Password", type="password")

                config = st.session_state.idp_config

                if st.button("🔑 Login", use_container_width=True):
                    if username and password:
                        if not config.get("cognito_user_pool_id") or not config.get("cognito_app_client_id"):
                            st.error("❌ Cognito not configured. Please run setup_cognito.py first.")
                        else:
                            with st.spinner("Authenticating..."):
                                result = authenticate_user(
                                    username,
                                    password,
                                    config["cognito_user_pool_id"],
                                    config["cognito_app_client_id"],
                                    config.get("cognito_region") or config.get("region"),
                                )

                                if result:
                                    if result.get("challenge") == "NEW_PASSWORD_REQUIRED":
                                        st.session_state.password_challenge = {
                                            "username": username,
                                            "session": result["session"],
                                        }
                                        st.warning("⚠️ You must set a new password")
                                        st.rerun()
                                    else:
                                        st.session_state.access_token = result["access_token"]
                                        st.session_state.id_token = result["id_token"]
                                        st.session_state.user_email = username
                                        st.success(f"✅ Logged in as {username}")
                                        st.rerun()
                    else:
                        st.warning("⚠️ Please enter username and password")

            # Handle password change challenge
            if "password_challenge" in st.session_state:
                with st.expander("🔒 Set New Password", expanded=True):
                    st.info("First time login - please set a new password")
                    new_password = st.text_input("New Password", type="password", key="new_pwd")
                    confirm_password = st.text_input("Confirm Password", type="password", key="confirm_pwd")

                    if st.button("Set Password", use_container_width=True):
                        if new_password and new_password == confirm_password:
                            config = st.session_state.idp_config
                            challenge = st.session_state.password_challenge

                            with st.spinner("Setting new password..."):
                                result = set_new_password(
                                    challenge["username"],
                                    new_password,
                                    challenge["session"],
                                    config["cognito_user_pool_id"],
                                    config["cognito_app_client_id"],
                                    config.get("cognito_region") or config.get("region"),
                                )

                                if result:
                                    st.session_state.access_token = result["access_token"]
                                    st.session_state.id_token = result["id_token"]
                                    st.session_state.user_email = challenge["username"]
                                    del st.session_state.password_challenge
                                    st.success(f"✅ Password set! Logged in as {challenge['username']}")
                                    st.rerun()
                        else:
                            st.error("❌ Passwords don't match or are empty")
    else:
        st.success(f"🔓 Logged in as: {st.session_state.user_email}")
        if st.button("🚪 Logout", use_container_width=True):
            st.session_state.access_token = None
            st.session_state.id_token = None
            st.session_state.user_email = None
            st.session_state.messages = []
            st.session_state.session_id = str(uuid.uuid4())
            st.session_state.persona_tools = None
            st.rerun()

    # ── 🧰 Tools available to this persona (the tool-gating visual) ─────────
    # The response interceptor filters tools/list by the caller's group, so a
    # policyholder and an admin see different lists. Fetched once per login and
    # cached (no per-rerun latency). Soft-fails; never breaks the demo. Shared:
    # GW1 exists on both IdP paths.
    if st.session_state.access_token:
        persona = (st.session_state.user_email or "").split("@")[0] or "you"
        if st.session_state.persona_tools is None:
            gw_url = st.session_state.idp_config.get("gateway_url")
            if gw_url:
                try:
                    with st.spinner("Loading your available tools…"):
                        tools = fetch_interceptor_tools(gw_url, st.session_state.access_token)
                    st.session_state.persona_tools = {"ok": True, "tools": tools}
                except Exception as e:
                    st.session_state.persona_tools = {"ok": False, "error": str(e)}
            else:
                st.session_state.persona_tools = {"ok": False, "error": "gateway-url not in SSM"}

        st.markdown("---")
        st.markdown(f"### 🧰 Tools available to you (as {persona})")
        pt = st.session_state.persona_tools
        if pt and pt.get("ok"):
            if pt["tools"]:
                for name in pt["tools"]:
                    st.markdown(f"- `{name}`")
            else:
                st.caption("No claims tools available to this persona.")
            st.caption("↑ claims tools, filtered to your role by the response interceptor")
            st.caption("Plus `search_claim_notes` on the notes path (per-user; not gated).")
        else:
            st.caption("🧰 couldn't fetch tool list (the demo still works).")

    st.markdown("---")

    with st.expander("⚙️ Runtime Configuration", expanded=False):
        runtime_arn = st.text_input("Runtime ARN", value=st.session_state.runtime_arn)
        st.session_state.runtime_arn = runtime_arn

        config = st.session_state.idp_config
        region = st.text_input("AWS Region", value=config.get("region", "us-east-1"))

        if st.button("🔄 Reload from SSM", use_container_width=True):
            st.session_state.idp_config = load_config_from_ssm()
            if st.session_state.idp_config.get("runtime_arn"):
                st.session_state.runtime_arn = st.session_state.idp_config["runtime_arn"]
            st.success("✅ Configuration reloaded")
            st.rerun()

    st.markdown("---")

    # ── Two-pattern legend: which facet each prompt exercises ───────────────
    st.markdown("### 🧭 The two patterns")
    st.caption(
        "**GW1 · Claims Gateway → Athena (claims):** rows + columns secured by role, tool gating, admin-sees-all."
    )
    st.caption("**GW2 · Notes Gateway → OpenSearch (notes):** per-user record isolation.")

    st.markdown("### 💡 Try these (log in as different personas)")

    # Each example carries the `path` it exercises so the response can be labeled
    # (the agent response itself doesn't reveal which gateway/tool served it), and an
    # optional `note` with the per-persona FGAC framing. The lesson lands when access
    # changes with identity — surfaced by the 🧰 tools panel + "what tools?" prompt.
    user_email = st.session_state.user_email or ""

    if "admin" in user_email.lower():
        # Admin: real admin-only tools (query_login_audit / text_to_sql).
        examples = [
            {
                "label": "🧰 What tools do you have access to?",
                "prompt": "What tools do you have access to?",
                "note": "As admin the agent lists its broad toolset (query_login_audit + "
                "text_to_sql). Log in as a policyholder and ask the same — the list changes.",
            },
            {
                "label": "🔎 Recent login audit (admin-only)",
                "prompt": "Show me the recent user login audit",
                "path": "interceptor",
            },
            {
                "label": "📊 Total claim amounts by type",
                "prompt": "Break down total claim amounts by claim type",
                "path": "interceptor",
            },
            {
                "label": "📝 Search my claim notes",
                "prompt": "Search my claim notes",
                "path": "obo",
                "note": "GW2 is per-user: even as admin you see only your OWN notes. "
                "Admin-sees-all is a GW1/claims feature; notes admin-all is v2-deferred.",
            },
        ]
    elif "adjuster" in user_email.lower():
        # Adjuster: claims scoped to assigned claims (all GW1/interceptor).
        examples = [
            {"label": "📋 My assigned claims", "prompt": "Show me all my assigned claims", "path": "interceptor"},
            {
                "label": "🔎 Status of CLM-2024-001",
                "prompt": "What's the status of CLM-2024-001?",
                "path": "interceptor",
            },
            {
                "label": "📈 Claims summary for my cases",
                "prompt": "Get claims summary for my cases",
                "path": "interceptor",
            },
            {
                "label": "⏳ Pending claims I'm handling",
                "prompt": "Show pending claims I'm handling",
                "path": "interceptor",
            },
        ]
    else:
        # Policyholder (John Doe = policyholder001 / Jane Smith = policyholder002).
        examples = [
            {"label": "📋 Show me my claims", "prompt": "Show me my claims", "path": "interceptor"},
            {
                "label": "📝 Search my claim notes for water damage",
                "prompt": "Search my claim notes for water damage",
                "path": "obo",
            },
            {
                "label": "🧰 What tools do you have access to?",
                "prompt": "What tools do you have access to?",
                "note": "The agent enumerates its own (already group-filtered) toolset — a "
                "policyholder won't see admin-only tools like query_login_audit or text_to_sql. "
                "Tool-gating shows up here and in the 🧰 panel, not as a refusal.",
            },
            {
                "label": "🔎 Recent login audit (admin-only)",
                "prompt": "Show me the recent user login audit",
                "path": "interceptor",
                "note": "query_login_audit isn't in a policyholder's toolset, so the agent "
                "answers with the tools it does have — over your own claims only. "
                "Access changed with your identity; nothing was 'refused'.",
            },
        ]

    for ex in examples:
        if st.button(ex["label"], key=f"ex_{ex['label'][:24]}", use_container_width=True):
            st.session_state.example_prompt = ex

# Main interface
st.title("🏥 Lakehouse Agent")
st.markdown(
    "**One agent, two identity-propagation patterns.** Log in as different personas "
    "(John Doe = policyholder001, Jane Smith = policyholder002, admin), run the same prompt, and "
    "watch what each is allowed to see change with their identity. Your token is used "
    "invisibly — it is never shown."
)
st.caption(f"Logged in as: {st.session_state.user_email or 'Not logged in'}")
st.caption("Note: each message is an independent query — this demo doesn't retain conversation history.")

if not st.session_state.access_token:
    st.warning("⚠️ Please login in the sidebar first!")
    st.stop()

if not st.session_state.runtime_arn:
    st.warning("⚠️ Runtime ARN not configured. Please check SSM Parameter Store or enter manually in the sidebar.")
    st.stop()

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        # Optional per-message extras (persist across reruns): tools used + path label + FGAC note.
        if msg.get("tools_used"):
            st.caption("🔧 Tools used: " + ", ".join(msg["tools_used"]))
        if msg.get("caption"):
            st.caption(msg["caption"])
        if msg.get("note"):
            st.info(msg["note"])

# Handle example prompt from sidebar. `example_prompt` is a dict
# {label, prompt, path?, note?} set by the example buttons.
if "example_prompt" in st.session_state and st.session_state.example_prompt:
    ep = st.session_state.example_prompt
    st.session_state.example_prompt = None  # Clear it immediately
    prompt = ep["prompt"] if isinstance(ep, dict) else ep
    path = ep.get("path") if isinstance(ep, dict) else None
    persona_note = ep.get("note") if isinstance(ep, dict) else None

    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        config = st.session_state.idp_config
        response = invoke_agent(
            st.session_state.runtime_arn,
            prompt,
            st.session_state.access_token,
            st.session_state.id_token,
            config.get("region"),
        )
        try:
            data = json.loads(response)
            response = data.get("content", response)
        except (json.JSONDecodeError, ValueError):
            pass
        st.markdown(response)

        # Real per-turn tools (fail-soft; absent/empty → render nothing).
        tools_used = st.session_state.get("last_tools_used")
        if tools_used:
            st.caption("🔧 Tools used: " + ", ".join(tools_used))

        # Path label from the clicked example (the agent response doesn't reveal
        # which gateway/tool served it). Free-typed prompts stay unlabeled.
        caption = PATH_LABELS.get(path) if path else None
        if caption:
            st.caption(caption)

        # Per-persona FGAC note (accurate framing — gating is shown by the 🧰 panel
        # and the "what tools?" prompt, NOT by the agent refusing an answer).
        note = persona_note
        if note:
            st.info(note)

        # Cross-persona nudge: the contrast is the proof.
        st.caption(
            "💡 Log in as a different persona and ask the same thing — the "
            "available tools and the data scope change with your identity."
        )

        st.session_state.messages.append(
            {"role": "assistant", "content": response, "caption": caption, "note": note, "tools_used": tools_used}
        )

    # Force rerun to show the chat input again
    st.rerun()

# Handle regular chat input
prompt = st.chat_input("Ask about your claims...")

if prompt:
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        config = st.session_state.idp_config
        response = invoke_agent(
            st.session_state.runtime_arn,
            prompt,
            st.session_state.access_token,
            st.session_state.id_token,
            config.get("region"),
        )
        try:
            data = json.loads(response)
            response = data.get("content", response)
        except (json.JSONDecodeError, ValueError):
            pass
        st.markdown(response)

        # Real per-turn tools (fail-soft) + cross-persona nudge.
        tools_used = st.session_state.get("last_tools_used")
        if tools_used:
            st.caption("🔧 Tools used: " + ", ".join(tools_used))
        st.caption(
            "💡 Log in as a different persona and ask the same thing — the "
            "available tools and the data scope change with your identity."
        )

        st.session_state.messages.append({"role": "assistant", "content": response, "tools_used": tools_used})

    # Rerun to clear the input and show updated chat
    st.rerun()
