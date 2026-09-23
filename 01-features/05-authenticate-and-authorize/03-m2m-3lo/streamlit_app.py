"""
Streamlit UI for AgentCore Identity Sample 12: M2M + 3LO Auth Flows.

Provides a two-screen experience:
  Screen 1 -- Centered login card (no sidebar)
  Screen 2 -- Dashboard with sidebar flow selector and chat area

Usage:
    streamlit run streamlit_app.py
"""

import atexit
import json
import os
import re
import subprocess
import sys
import time

import boto3
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))
COGNITO_CONFIG_PATH = os.path.join(SAMPLE_DIR, "cognito_config.json")
CALLBACK_SERVER_SCRIPT = os.path.join(SAMPLE_DIR, "oauth2_callback_server.py")
CALLBACK_SERVER_PORT = 9090
CALLBACK_PING_URL = f"http://localhost:{CALLBACK_SERVER_PORT}/ping"
CALLBACK_TOKEN_URL = f"http://localhost:{CALLBACK_SERVER_PORT}/userIdentifier/token"

CONSENT_URL_PATTERN = re.compile(r"https?://[^\s'\")\]]+")

FLOW_KEYS = ["m2m", "github", "google"]
FLOW_LABELS = {
    "m2m": "M2M",
    "github": "GitHub 3LO",
    "google": "Google 3LO",
}
FLOW_HEADERS = {
    "m2m": (
        "Machine-to-Machine Flow",
        "Agent authenticates to internal APIs using client credentials",
    ),
    "github": (
        "GitHub Authorization Code Flow",
        "Agent accesses your GitHub data after you consent",
    ),
    "google": ("Google Calendar Flow", "Agent reads your calendar after you consent"),
}
PRESET_BUTTONS = {
    "m2m": "What's the weather in Seattle?",
    "github": "List my GitHub repositories",
    "google": "Show today's calendar events",
}


# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Sample 12: M2M + 3LO Auth",
    page_icon="\U0001f510",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    /* Login card styling */
    .login-card {
        background: var(--secondary-background-color);
        border-radius: 12px;
        padding: 2.5rem 2rem 2rem 2rem;
        margin-top: 6vh;
    }
    .login-title {
        text-align: center;
        font-size: 1.75rem;
        font-weight: 700;
        margin-bottom: 0.25rem;
    }
    .login-subtitle {
        text-align: center;
        font-size: 0.95rem;
        opacity: 0.7;
        margin-bottom: 0.5rem;
    }
    .login-desc {
        text-align: center;
        font-size: 0.85rem;
        opacity: 0.55;
        margin-bottom: 1.5rem;
    }
    /* Hide sidebar on login screen */
    .no-sidebar [data-testid="stSidebar"] { display: none; }
    .no-sidebar [data-testid="stSidebarCollapsedControl"] { display: none; }
    /* Compact sidebar badges */
    .sidebar-badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 10px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .badge-green {
        background: #16a34a22;
        color: #16a34a;
    }
    .badge-yellow {
        background: #eab30822;
        color: #ca8a04;
    }
    .badge-blue {
        background: #3b82f622;
        color: #3b82f6;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "logged_in": False,
    "jwt_token": None,
    "username": None,
    "agent_arn": None,
    "chat_history": [],
    "consent_url": None,
    "consent_state": "not_started",  # not_started | pending | completed
    "callback_proc": None,
    "selected_flow": "m2m",
    "cognito_config": None,
    "last_3lo_prompt": None,
    "last_response_time": None,
}
for key, val in _DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val


# ---------------------------------------------------------------------------
# Cleanup callback server on exit
# ---------------------------------------------------------------------------
def _cleanup_callback_server():
    proc = st.session_state.get("callback_proc")
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()


atexit.register(_cleanup_callback_server)


# ---------------------------------------------------------------------------
# Helper: load Cognito config
# ---------------------------------------------------------------------------
@st.cache_data
def load_cognito_config() -> dict | None:
    if not os.path.exists(COGNITO_CONFIG_PATH):
        return None
    with open(COGNITO_CONFIG_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Helper: Cognito authentication
# ---------------------------------------------------------------------------
def get_bearer_token(config: dict, username: str, password: str) -> str:
    """Authenticate with Cognito and return an access token."""
    cognito = boto3.client("cognito-idp", region_name=config["region"])
    auth = cognito.initiate_auth(
        ClientId=config["client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    return auth["AuthenticationResult"]["AccessToken"]


# ---------------------------------------------------------------------------
# Helper: resolve deployed agent ARN
# ---------------------------------------------------------------------------
def _find_project_dir() -> str:
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


def resolve_agent_arn() -> str:
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


# ---------------------------------------------------------------------------
# Helper: parse agent streaming response
# ---------------------------------------------------------------------------
def _format_response(text: str) -> str:
    """Clean up agent response text for display."""
    return text.replace("\\n", "\n").strip('"')


def parse_event_stream(response: dict) -> str:
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


# ---------------------------------------------------------------------------
# Helper: invoke agent
# ---------------------------------------------------------------------------
def invoke_agent(agent_arn: str, prompt: str, bearer_token: str, user_id: str, region: str) -> str:
    client = boto3.client("bedrock-agentcore", region_name=region)

    def _inject_bearer(request, **kwargs):
        request.headers["Authorization"] = f"Bearer {bearer_token}"

    client.meta.events.register("before-send.bedrock-agentcore.InvokeAgentRuntime", _inject_bearer)
    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            runtimeUserId=user_id,
            qualifier="DEFAULT",
            payload=json.dumps({"prompt": prompt}),
        )
        return parse_event_stream(resp)
    finally:
        client.meta.events.unregister("before-send.bedrock-agentcore.InvokeAgentRuntime", _inject_bearer)


# ---------------------------------------------------------------------------
# Helper: OAuth2 callback server management
# ---------------------------------------------------------------------------
def _callback_server_running() -> bool:
    try:
        r = requests.get(CALLBACK_PING_URL, timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def start_callback_server(region: str, bearer_token: str):
    """Start the OAuth2 callback server subprocess if not already running."""
    proc = st.session_state.get("callback_proc")
    if proc and proc.poll() is None and _callback_server_running():
        _store_token_in_server(bearer_token)
        return

    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

    st.session_state.callback_proc = subprocess.Popen(
        [sys.executable, CALLBACK_SERVER_SCRIPT, "--region", region],
        cwd=SAMPLE_DIR,
    )

    for _ in range(30):
        if _callback_server_running():
            _store_token_in_server(bearer_token)
            return
        time.sleep(0.5)

    st.error("OAuth2 callback server failed to start within 15 seconds.")


def _store_token_in_server(bearer_token: str):
    """Post the bearer token to the callback server for session binding."""
    try:
        requests.post(
            CALLBACK_TOKEN_URL,
            json={"user_token": bearer_token},
            timeout=2,
        )
    except Exception as exc:
        st.warning(f"Could not store token in callback server: {exc}")


def stop_callback_server():
    proc = st.session_state.get("callback_proc")
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
    st.session_state.callback_proc = None


# ---------------------------------------------------------------------------
# Helper: extract consent URL from agent response
# ---------------------------------------------------------------------------
def extract_consent_url(text: str) -> str | None:
    """Return the first consent-like URL found in agent response text."""
    urls = CONSENT_URL_PATTERN.findall(text)
    consent_prefixes = [
        "https://bedrock-agentcore",
        "https://accounts.google.com",
        "https://github.com/login/oauth",
    ]
    for url in urls:
        for prefix in consent_prefixes:
            if url.startswith(prefix):
                return url.rstrip(".,;")
    for url in urls:
        if "oauth" in url.lower() or "authorize" in url.lower() or "consent" in url.lower():
            return url.rstrip(".,;")
    return None


# ---------------------------------------------------------------------------
# Helper: truncate ARN for display
# ---------------------------------------------------------------------------
def _truncate_arn(arn: str, max_len: int = 45) -> str:
    if len(arn) <= max_len:
        return arn
    return arn[:20] + "..." + arn[-22:]


# ---------------------------------------------------------------------------
# Helper: sign out
# ---------------------------------------------------------------------------
def _sign_out():
    stop_callback_server()
    st.session_state.logged_in = False
    st.session_state.jwt_token = None
    st.session_state.username = None
    st.session_state.agent_arn = None
    st.session_state.chat_history = []
    st.session_state.consent_url = None
    st.session_state.consent_state = "not_started"
    st.session_state.last_3lo_prompt = None
    st.session_state.last_response_time = None
    st.session_state.selected_flow = "m2m"


# ===========================================================================
# SCREEN 1: LOGIN
# ===========================================================================
if not st.session_state.logged_in:
    # Hide sidebar on login screen
    st.markdown('<div class="no-sidebar"></div>', unsafe_allow_html=True)

    config = load_cognito_config()
    if config is None:
        st.error("cognito_config.json not found. Run `python setup_cognito.py` first.")
        st.stop()

    # Centered card layout
    _, center, _ = st.columns([1, 2, 1])
    with center:
        st.markdown('<div class="login-card">', unsafe_allow_html=True)
        st.markdown(
            '<p class="login-title">AgentCore M2M + 3LO Auth Demo</p>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p class="login-subtitle">Sample 12: Client Credentials + Authorization Code Flows</p>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p class="login-desc">'
            "This demo shows M2M (machine-to-machine) and 3-legged OAuth flows "
            "for accessing external APIs on behalf of the user."
            "</p>",
            unsafe_allow_html=True,
        )

        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("Username", value=config.get("username", "testuser"))
            password = st.text_input(
                "Password",
                value=config.get("password", "AgentCoreTest1!"),
                type="password",
            )
            login_btn = st.form_submit_button("Sign In", use_container_width=True, type="primary")

        if login_btn:
            with st.spinner("Authenticating..."):
                try:
                    token = get_bearer_token(config, username, password)
                    st.session_state.jwt_token = token
                    st.session_state.username = username
                    st.session_state.cognito_config = config
                    st.session_state.logged_in = True

                    # Auto-resolve agent ARN
                    try:
                        arn = resolve_agent_arn()
                        st.session_state.agent_arn = arn
                    except Exception:
                        pass  # Will show a warning on the dashboard

                    st.rerun()
                except Exception as exc:
                    st.error(f"Login failed: {exc}")

        st.markdown("</div>", unsafe_allow_html=True)

    st.stop()


# ===========================================================================
# SCREEN 2: DASHBOARD (logged in)
# ===========================================================================
config = st.session_state.cognito_config or load_cognito_config()
if config is None:
    st.error("cognito_config.json not found.")
    st.stop()
st.session_state.cognito_config = config

flow_key = st.session_state.selected_flow
is_3lo = flow_key in ("github", "google")

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    # -- User badge --
    st.markdown(
        f'<span class="sidebar-badge badge-green">Signed in as {st.session_state.username}</span>',
        unsafe_allow_html=True,
    )
    st.caption("")  # spacer

    # -- Agent ARN --
    if st.session_state.agent_arn:
        st.caption(f"Agent: `{_truncate_arn(st.session_state.agent_arn)}`")
    else:
        st.warning("Agent ARN not resolved")
        if st.button("Resolve Agent ARN", use_container_width=True):
            try:
                arn = resolve_agent_arn()
                st.session_state.agent_arn = arn
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    # -- Region --
    st.caption(f"Region: `{config.get('region', 'N/A')}`")

    # -- Sign out --
    if st.button("Sign Out", use_container_width=True):
        _sign_out()
        st.rerun()

    st.divider()

    # -- Flow selector --
    st.markdown("**Auth Flow**")
    selected_flow = st.radio(
        "Select flow",
        FLOW_KEYS,
        format_func=lambda k: FLOW_LABELS[k],
        index=FLOW_KEYS.index(st.session_state.selected_flow) if st.session_state.selected_flow in FLOW_KEYS else 0,
        label_visibility="collapsed",
    )

    # Reset consent state when switching flows
    if selected_flow != st.session_state.selected_flow:
        st.session_state.consent_state = "not_started"
        st.session_state.consent_url = None
        st.session_state.last_3lo_prompt = None
        st.session_state.selected_flow = selected_flow
        # Auto-start callback server for 3LO flows
        if selected_flow in ("github", "google") and st.session_state.jwt_token:
            start_callback_server(config["region"], st.session_state.jwt_token)
        else:
            stop_callback_server()
        st.rerun()

    flow_key = st.session_state.selected_flow
    is_3lo = flow_key in ("github", "google")

    # -- 3LO consent section --
    if is_3lo:
        st.divider()
        provider_label = "GitHub" if flow_key == "github" else "Google"
        state = st.session_state.consent_state

        # Status indicator
        if state == "not_started":
            st.markdown(
                '<span class="sidebar-badge badge-blue">Not started</span>',
                unsafe_allow_html=True,
            )
        elif state == "pending":
            st.markdown(
                '<span class="sidebar-badge badge-yellow">Pending consent</span>',
                unsafe_allow_html=True,
            )
        elif state == "completed":
            st.markdown(
                '<span class="sidebar-badge badge-green">Authorized</span>',
                unsafe_allow_html=True,
            )

        # Consent URL link button
        if st.session_state.consent_url and state == "pending":
            st.link_button(
                f"Authorize on {provider_label}",
                st.session_state.consent_url,
                use_container_width=True,
            )

            # Re-invoke button
            if st.button("Re-invoke after consent", use_container_width=True, type="primary"):
                st.session_state.consent_state = "completed"
                if st.session_state.last_3lo_prompt and st.session_state.jwt_token and st.session_state.agent_arn:
                    prompt = st.session_state.last_3lo_prompt
                    st.session_state.chat_history.append({"role": "user", "content": f"[Re-invoke] {prompt}"})
                    try:
                        t0 = time.time()
                        result = invoke_agent(
                            st.session_state.agent_arn,
                            prompt,
                            st.session_state.jwt_token,
                            config["username"],
                            config["region"],
                        )
                        st.session_state.last_response_time = round(time.time() - t0, 2)
                        st.session_state.chat_history.append({"role": "assistant", "content": result})
                    except Exception as exc:
                        st.session_state.chat_history.append({"role": "assistant", "content": f"Error: {exc}"})
                    st.rerun()

        # Callback server indicator
        cb_running = _callback_server_running()
        st.caption(f"Callback server: {'Running' if cb_running else 'Stopped'}")

    # -- Response time --
    if st.session_state.last_response_time is not None:
        st.caption(f"Last response: {st.session_state.last_response_time}s")

# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

# Gate: require agent ARN
if not st.session_state.agent_arn:
    st.warning("No deployed agent found. Resolve the Agent ARN from the sidebar, or run `agentcore deploy -y`.")
    st.stop()

# -- Flow header --
title, subtitle = FLOW_HEADERS[flow_key]
st.subheader(title)
st.caption(subtitle)

# -- Auto-start callback server for 3LO on first render --
if is_3lo and st.session_state.jwt_token and not _callback_server_running():
    start_callback_server(config["region"], st.session_state.jwt_token)

# -- Chat history --
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(_format_response(msg["content"]))

# -- Preset button --
preset_prompt = None
preset_label = PRESET_BUTTONS[flow_key]
if st.button(preset_label, key=f"preset_{flow_key}", use_container_width=False):
    preset_prompt = preset_label

# -- Chat input --
user_input = st.chat_input("Type a prompt for the agent...")
prompt_to_send = preset_prompt or user_input

# -- Send prompt --
if prompt_to_send:
    st.session_state.chat_history.append({"role": "user", "content": prompt_to_send})

    if is_3lo:
        start_callback_server(config["region"], st.session_state.jwt_token)
        st.session_state.last_3lo_prompt = prompt_to_send

    with st.chat_message("user"):
        st.markdown(prompt_to_send)

    with st.chat_message("assistant"):
        with st.spinner("Invoking agent..."):
            try:
                t0 = time.time()
                result = invoke_agent(
                    st.session_state.agent_arn,
                    prompt_to_send,
                    st.session_state.jwt_token,
                    config["username"],
                    config["region"],
                )
                st.session_state.last_response_time = round(time.time() - t0, 2)

                if is_3lo:
                    consent_url = extract_consent_url(result)
                    if consent_url:
                        st.session_state.consent_url = consent_url
                        st.session_state.consent_state = "pending"
                    elif st.session_state.consent_state != "completed":
                        st.session_state.consent_state = "completed"
                        st.session_state.consent_url = None

                st.markdown(_format_response(result))
                st.session_state.chat_history.append({"role": "assistant", "content": result})

                if is_3lo and st.session_state.consent_state == "pending" and st.session_state.consent_url:
                    provider_label = "GitHub" if flow_key == "github" else "Google"
                    st.info(
                        f"Consent required: click **Authorize on {provider_label}** in the sidebar, "
                        "then click **Re-invoke after consent** once you have authorized."
                    )

            except Exception as exc:
                error_msg = f"Error: {exc}"
                st.error(error_msg)
                st.session_state.chat_history.append({"role": "assistant", "content": error_msg})

    st.rerun()
