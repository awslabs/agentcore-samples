"""
Streamlit UI for AgentCore Identity Sample 10: Runtime Inbound + Outbound Auth.

Demonstrates invoking an AgentCore Runtime with and without a Cognito JWT bearer token.

Usage:
    streamlit run streamlit_app.py
"""

import json
import os
import time

import boto3
import streamlit as st

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Sample 10: Runtime Auth",
    page_icon="\U0001f510",
    layout="wide",
)

SAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Helper functions (self-contained, mirrors invoke.py patterns)
# ---------------------------------------------------------------------------


def _load_cognito_config() -> dict | None:
    """Load cognito_config.json from the sample directory."""
    path = os.path.join(SAMPLE_DIR, "cognito_config.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _find_project_dir() -> str:
    """Find the agentcore project directory (subdirectory containing agentcore/)."""
    for entry in os.listdir(SAMPLE_DIR):
        candidate = os.path.join(SAMPLE_DIR, entry)
        if os.path.isdir(candidate) and os.path.isdir(os.path.join(candidate, "agentcore")):
            return candidate
    raise FileNotFoundError("No agentcore project directory found. Run 'agentcore create' first.")


def _find_in_json(obj, key):
    """Recursively search for a key in nested JSON."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            result = _find_in_json(v, key)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_in_json(item, key)
            if result:
                return result
    return None


def _resolve_agent_arn() -> str:
    """Read the deployed agent ARN from deployed-state.json.

    Searches for runtimeArn recursively to work across CLI versions.
    """
    project_dir = _find_project_dir()
    state_file = os.path.join(project_dir, "agentcore", ".cli", "deployed-state.json")
    if not os.path.exists(state_file):
        raise FileNotFoundError("No deployed-state.json found. Run 'agentcore deploy -y' first.")
    with open(state_file) as f:
        state = json.load(f)
    arn = _find_in_json(state, "runtimeArn")
    if arn:
        return arn
    raise ValueError("No deployed agent found. Run 'agentcore deploy -y' first.")


def _get_bearer_token(config: dict) -> str:
    """Authenticate with Cognito and return an access token."""
    cognito = boto3.client("cognito-idp", region_name=config["region"])
    auth = cognito.initiate_auth(
        ClientId=config["client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": config["username"],
            "PASSWORD": config["password"],
        },
    )
    return auth["AuthenticationResult"]["AccessToken"]


def _parse_event_stream(response: dict) -> str:
    """Extract text from the boto3 EventStream response."""
    parts: list[str] = []
    for event in response.get("response", []):
        raw = event if isinstance(event, bytes) else event.get("chunk", {}).get("bytes", b"")
        if raw:
            try:
                decoded = json.loads(raw.decode("utf-8"))
                if isinstance(decoded, str):
                    parts.append(decoded)
                elif isinstance(decoded, dict):
                    content = decoded.get("content", [])
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(c["text"])
                        elif isinstance(c, str):
                            parts.append(c)
                    if not content and "message" in decoded:
                        msg = decoded["message"]
                        if isinstance(msg, dict):
                            for c in msg.get("content", []):
                                if isinstance(c, dict) and c.get("type") == "text":
                                    parts.append(c["text"])
            except Exception:
                parts.append(raw.decode("utf-8"))
    return "\n".join(parts) if parts else "(no response)"


def _invoke_agent(agent_arn: str, region: str, prompt: str, bearer_token: str | None = None) -> dict:
    """
    Invoke the agent runtime, optionally with a bearer token.

    Returns a dict with keys: success, text, elapsed, error, status_code.
    """
    client = boto3.client("bedrock-agentcore", region_name=region)
    handler = None

    if bearer_token:

        def _inject_bearer(request, **kwargs):
            request.headers["Authorization"] = f"Bearer {bearer_token}"

        handler = _inject_bearer
        client.meta.events.register("before-send.bedrock-agentcore.InvokeAgentRuntime", handler)

    t0 = time.time()
    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            runtimeUserId="testuser",
            qualifier="DEFAULT",
            payload=json.dumps({"prompt": prompt}),
        )
        elapsed = time.time() - t0
        text = _parse_event_stream(resp)
        return {
            "success": True,
            "text": text,
            "elapsed": elapsed,
            "error": None,
            "status_code": resp.get("ResponseMetadata", {}).get("HTTPStatusCode"),
        }
    except Exception as exc:
        elapsed = time.time() - t0
        return {
            "success": False,
            "text": None,
            "elapsed": elapsed,
            "error": f"{type(exc).__name__}: {exc}",
            "status_code": getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode"),
        }
    finally:
        if handler:
            client.meta.events.unregister("before-send.bedrock-agentcore.InvokeAgentRuntime", handler)


def _format_response(text: str) -> str:
    """Replace literal \\n sequences with real line breaks for display."""
    return text.replace("\\n", "\n")


def _truncate_arn(arn: str, max_len: int = 45) -> str:
    """Truncate an ARN for sidebar display."""
    if len(arn) <= max_len:
        return arn
    return arn[: max_len - 3] + "..."


# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------
for key, default in {
    "logged_in": False,
    "jwt_token": None,
    "username": "",
    "chat_history": [],
    "agent_arn": None,
    "arn_error": None,
    "region": "us-east-1",
    "last_request": None,
    "login_error": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------
config = _load_cognito_config()

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    /* Hide sidebar toggle when not logged in */
    .login-page [data-testid="collapsedControl"] { display: none; }

    /* Login card styling */
    .login-card {
        padding: 2rem 0;
    }
    .login-header {
        text-align: center;
        margin-bottom: 0.5rem;
    }
    .login-subtitle {
        text-align: center;
        color: #6b7280;
        font-size: 0.95rem;
        margin-bottom: 1.5rem;
    }
    .login-desc {
        text-align: center;
        color: #9ca3af;
        font-size: 0.85rem;
        margin-bottom: 2rem;
        line-height: 1.5;
    }

    /* Sidebar signed-in badge */
    .signed-in-badge {
        background: #065f46;
        color: #d1fae5;
        padding: 0.4rem 0.75rem;
        border-radius: 0.5rem;
        font-size: 0.85rem;
        font-weight: 500;
        text-align: center;
        margin-bottom: 0.5rem;
    }
    .sidebar-meta {
        color: #9ca3af;
        font-size: 0.75rem;
        word-break: break-all;
        margin-bottom: 0.25rem;
    }

    /* Preset buttons row */
    .stButton > button {
        font-size: 0.85rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================================
# SCREEN 1: LOGIN (full page, centered)
# =========================================================================
if not st.session_state.logged_in:
    # Hide sidebar on login page
    st.markdown(
        "<style>[data-testid='stSidebar'] { display: none; }</style>",
        unsafe_allow_html=True,
    )

    # Vertical spacer
    st.markdown("")
    st.markdown("")

    # Center column
    _, center, _ = st.columns([1, 2, 1])

    with center:
        st.markdown(
            "<h1 class='login-header'>AgentCore Runtime Auth Demo</h1>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<p class='login-subtitle'>Sample 10: Inbound JWT + Outbound API Key</p>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<p class='login-desc'>"
            "This demo shows how AgentCore Runtime validates inbound JWT tokens "
            "and retrieves outbound API keys securely."
            "</p>",
            unsafe_allow_html=True,
        )

        if not config:
            st.error("**cognito_config.json not found.** Run `python setup_cognito.py` before using this app.")
            st.stop()

        # Show any previous login error
        if st.session_state.login_error:
            st.error(st.session_state.login_error)

        with st.form("login_form"):
            username = st.text_input(
                "Username",
                value=config.get("username", ""),
            )
            password = st.text_input(
                "Password",
                value=config.get("password", ""),
                type="password",
            )
            sign_in = st.form_submit_button("Sign In", use_container_width=True)

        if sign_in:
            if not username or not password:
                st.session_state.login_error = "Username and password are required."
                st.rerun()
            else:
                login_config = {**config, "username": username, "password": password}
                try:
                    with st.spinner("Authenticating with Cognito..."):
                        token = _get_bearer_token(login_config)

                    # Resolve agent ARN right away
                    with st.spinner("Resolving agent ARN..."):
                        try:
                            agent_arn = _resolve_agent_arn()
                        except Exception as exc:
                            agent_arn = None
                            st.session_state.arn_error = str(exc)

                    # Commit to session state
                    st.session_state.jwt_token = token
                    st.session_state.bearer_input = token  # pre-fill the token field
                    st.session_state.username = username
                    st.session_state.logged_in = True
                    st.session_state.agent_arn = agent_arn
                    st.session_state.region = config.get("region", "us-east-1")
                    st.session_state.login_error = None
                    st.rerun()

                except Exception as exc:
                    st.session_state.login_error = f"Login failed: {exc}"
                    st.rerun()

    st.stop()


# =========================================================================
# SCREEN 2: DASHBOARD (after login)
# =========================================================================

# --- Sidebar ---
with st.sidebar:
    st.markdown(
        f"<div class='signed-in-badge'>Signed in as {st.session_state.username}</div>",
        unsafe_allow_html=True,
    )

    if st.session_state.agent_arn:
        st.markdown(
            f"<p class='sidebar-meta'><b>Agent:</b> {_truncate_arn(st.session_state.agent_arn)}</p>",
            unsafe_allow_html=True,
        )
    elif st.session_state.arn_error:
        st.error(st.session_state.arn_error, icon="\u26a0\ufe0f")
    else:
        st.warning("Agent ARN not resolved.")

    st.markdown(
        f"<p class='sidebar-meta'><b>Region:</b> {st.session_state.region}</p>",
        unsafe_allow_html=True,
    )

    if st.button("Sign Out", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

    st.divider()

    # Bearer token — auto-filled, user can clear to test 403
    st.markdown("**Bearer Token**")
    st.caption("Auto-filled after login. Clear to test 403 rejection.")
    bearer_input = st.text_area(
        "Bearer Token",
        height=80,
        key="bearer_input",
        label_visibility="collapsed",
    )
    if bearer_input.strip():
        st.markdown(":green-background[Token will be sent]")
    else:
        st.markdown(":red-background[No token — requests will get 403]")

# --- Main area ---
st.markdown("#### Runtime Inbound + Outbound Auth")
st.markdown("""
```
Inbound:   You ──[JWT Token]──▶ AgentCore Runtime (validates via Cognito)

Outbound:  Agent ──▶ AgentCore Identity ──▶ API Key ──▶ Weather API (wttr.in)
                     @requires_api_key()     securely
                                             retrieved
```
""")
st.caption(
    "Clear the Bearer Token in the sidebar to see a 403 rejection. The agent retrieves the API key from AgentCore Identity — never hardcoded."
)

# Chat history
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(_format_response(msg["content"]))

# Preset buttons
presets = [
    "What's the weather in Seattle?",
    "Calculate 5 * 7 + 3",
    "What can you do?",
]
preset_cols = st.columns(len(presets))
prompt_to_send: str | None = None

for i, preset in enumerate(presets):
    with preset_cols[i]:
        if st.button(preset, key=f"preset_{i}", use_container_width=True):
            prompt_to_send = preset

# Chat input
user_input = st.chat_input("Ask the agent...")
if user_input:
    prompt_to_send = user_input

# Send prompt
if prompt_to_send:
    if not st.session_state.agent_arn:
        st.error("Agent ARN not resolved. Deploy the agent first.")
    else:
        st.session_state.chat_history.append(
            {
                "role": "user",
                "content": prompt_to_send,
            }
        )
        with st.chat_message("user"):
            st.markdown(prompt_to_send)

        with st.chat_message("assistant"):
            with st.spinner("Invoking agent..."):
                result = _invoke_agent(
                    st.session_state.agent_arn,
                    st.session_state.region,
                    prompt_to_send,
                    bearer_token=st.session_state.get("bearer_input", "").strip() or None,
                )

            truncated = st.session_state.jwt_token[:20] + "..."
            st.session_state.last_request = {"auth": f"Bearer {truncated}", **result}

            if result["success"]:
                display_text = _format_response(result["text"])
                st.markdown(display_text)
                st.session_state.chat_history.append(
                    {
                        "role": "assistant",
                        "content": result["text"],
                    }
                )
            else:
                st.error(result["error"])
                st.session_state.chat_history.append(
                    {
                        "role": "assistant",
                        "content": f"Error: {result['error']}",
                    }
                )

# Last request details (collapsed)
if st.session_state.last_request:
    with st.expander("Last request details", expanded=False):
        req = st.session_state.last_request
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Status", "Success" if req.get("success") else "Failed")
        with col2:
            st.metric("Response Time", f"{req.get('elapsed', 0):.2f}s")
        with col3:
            st.metric("HTTP Status", str(req.get("status_code", "N/A")))
        st.code(f"Authorization: {req.get('auth', 'N/A')}", language=None)
        if req.get("error"):
            st.error(req["error"])
        if req.get("text"):
            st.code(req["text"], language=None)
