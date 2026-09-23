"""
Streamlit UI for AgentCore Identity Sample 11 — Gateway Inbound + Outbound Auth.

Provides a two-screen experience:
  Screen 1 — Centered login card (no sidebar)
  Screen 2 — Chat dashboard with sidebar status + preset prompts

Run:
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
    page_title="Sample 11: Gateway Auth",
    page_icon="\U0001f510",
    layout="wide",
)

SAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Helper functions (reused from invoke.py patterns)
# ---------------------------------------------------------------------------


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


def _find_project_dir() -> str:
    """Find the agentcore project subdirectory."""
    for entry in os.listdir(SAMPLE_DIR):
        candidate = os.path.join(SAMPLE_DIR, entry)
        if os.path.isdir(candidate) and os.path.isdir(os.path.join(candidate, "agentcore")):
            return candidate
    raise FileNotFoundError("No agentcore project directory found. Run 'agentcore create' first.")


@st.cache_data(ttl=300)
def load_cognito_config() -> dict:
    """Load cognito_config.json from the sample directory."""
    path = os.path.join(SAMPLE_DIR, "cognito_config.json")
    with open(path) as f:
        return json.load(f)


@st.cache_data(ttl=120, show_spinner="Resolving agent ARN...")
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


@st.cache_data(ttl=120, show_spinner="Resolving gateway URL...")
def resolve_gateway_url() -> str:
    """Attempt to read the gateway URL from deployed-state.json.

    Searches for gatewayUrl recursively to work across CLI versions.
    """
    try:
        project_dir = _find_project_dir()
        state_file = os.path.join(project_dir, "agentcore", ".cli", "deployed-state.json")
        if not os.path.exists(state_file):
            return "N/A"
        with open(state_file) as f:
            state = json.load(f)
        url = _find_in_json(state, "gatewayUrl")
        if url:
            return url
    except Exception:
        pass
    return "N/A"


def get_bearer_token(config: dict) -> str:
    """Authenticate against Cognito and return an access token."""
    cognito = boto3.client("cognito-idp", region_name=config["region"])
    auth = cognito.initiate_auth(
        ClientId=config["user_client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": config["username"],
            "PASSWORD": config["password"],
        },
    )
    return auth["AuthenticationResult"]["AccessToken"]


def _format_response(text: str) -> str:
    """Clean up agent response text for display."""
    return text.replace("\\n", "\n").strip('"')


def parse_event_stream(response: dict) -> str:
    """Parse the streaming event response into plain text."""
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


def invoke_agent(agent_arn: str, region: str, prompt: str, bearer_token: str | None = None) -> dict:
    """
    Invoke the AgentCore Runtime agent.

    Returns a dict with keys: text, status_code, elapsed, auth_header, error.
    """
    client = boto3.client("bedrock-agentcore", region_name=region)
    auth_header = None

    handler = None
    if bearer_token:
        auth_header = f"Bearer {bearer_token[:20]}...{bearer_token[-10:]}"

        def _inject_bearer(request, **kwargs):
            request.headers["Authorization"] = f"Bearer {bearer_token}"

        handler = _inject_bearer
        client.meta.events.register("before-send.bedrock-agentcore.InvokeAgentRuntime", handler)

    start = time.time()
    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            runtimeUserId="testuser",
            qualifier="DEFAULT",
            payload=json.dumps({"prompt": prompt}),
        )
        elapsed = time.time() - start
        text = parse_event_stream(resp)
        status_code = resp.get("ResponseMetadata", {}).get("HTTPStatusCode", 200)
        return {
            "text": text,
            "status_code": status_code,
            "elapsed": elapsed,
            "auth_header": auth_header,
            "error": None,
        }
    except Exception as exc:
        elapsed = time.time() - start
        return {
            "text": None,
            "status_code": getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode", None),
            "elapsed": elapsed,
            "auth_header": auth_header,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if handler:
            client.meta.events.unregister("before-send.bedrock-agentcore.InvokeAgentRuntime", handler)


def _truncate_arn(arn: str, max_len: int = 50) -> str:
    """Shorten an ARN for display, keeping the meaningful tail."""
    if len(arn) <= max_len:
        return arn
    return arn[:20] + "..." + arn[-(max_len - 23) :]


# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
for key, default in {
    "logged_in": False,
    "jwt_token": None,
    "username": "",
    "chat_history": [],
    "last_request_info": None,
    "agent_arn": None,
    "gateway_url": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ---------------------------------------------------------------------------
# Load config (required for both screens)
# ---------------------------------------------------------------------------
try:
    config = load_cognito_config()
except FileNotFoundError:
    st.error("cognito_config.json not found. Run `python setup_cognito.py` first.")
    st.stop()


# ===================================================================
# SCREEN 1 — Login (full page, centered, no sidebar)
# ===================================================================
if not st.session_state.logged_in:
    # Hide the sidebar on the login screen
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] { display: none; }
        [data-testid="stSidebarCollapsedControl"] { display: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Vertical spacer
    st.markdown("<div style='padding-top: 6vh'></div>", unsafe_allow_html=True)

    # Centered card using column ratio
    _left, center, _right = st.columns([1, 2, 1])

    with center:
        st.markdown(
            "<h1 style='text-align:center; margin-bottom:0'>AgentCore Gateway Auth Demo</h1>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<p style='text-align:center; color:grey; margin-top:0.25rem; font-size:1.1rem'>"
            "Sample 11: Gateway JWT + MCP Tools</p>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<p style='text-align:center; color:grey; font-size:0.95rem; max-width:520px; margin:0 auto 1.5rem auto'>"
            "This demo shows how AgentCore Gateway validates inbound JWT tokens "
            "and authenticates to upstream MCP servers with OAuth2.</p>",
            unsafe_allow_html=True,
        )

        st.divider()

        with st.form("login_form", clear_on_submit=False):
            username = st.text_input(
                "Username",
                value=config.get("username", "testuser"),
            )
            password = st.text_input(
                "Password",
                value=config.get("password", "AgentCoreTest1!"),
                type="password",
            )
            sign_in = st.form_submit_button("Sign In", use_container_width=True, type="primary")

        if sign_in:
            login_config = {**config, "username": username, "password": password}
            try:
                with st.spinner("Authenticating with Cognito..."):
                    token = get_bearer_token(login_config)
                st.session_state.jwt_token = token
                st.session_state.bearer_input = token  # pre-fill token field
                st.session_state.logged_in = True
                st.session_state.username = username

                # Resolve agent ARN eagerly on login
                try:
                    st.session_state.agent_arn = resolve_agent_arn()
                except Exception:
                    st.session_state.agent_arn = None

                # Resolve gateway URL eagerly on login
                try:
                    st.session_state.gateway_url = resolve_gateway_url()
                except Exception:
                    st.session_state.gateway_url = None

                st.rerun()
            except Exception as exc:
                st.error(f"Login failed: {exc}")

    st.stop()


# ===================================================================
# SCREEN 2 — Dashboard (after login)
# ===================================================================

agent_arn = st.session_state.agent_arn
gateway_url = st.session_state.gateway_url

# ---------------------------------------------------------------------------
# Sidebar (compact status)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(f":green-background[Signed in as **{st.session_state.username}**]")

    st.markdown("")  # small gap

    # Agent ARN
    if agent_arn:
        st.caption(f"**Agent ARN**\n`{_truncate_arn(agent_arn)}`")
    else:
        st.caption("**Agent ARN**\n`Not resolved`")

    # Gateway URL
    st.caption(f"**Gateway**\n`{gateway_url or 'N/A'}`")

    # Region
    st.caption(f"**Region**\n`{config.get('region', 'N/A')}`")

    if st.button("Sign Out", use_container_width=True):
        for key in [
            "logged_in",
            "jwt_token",
            "username",
            "agent_arn",
            "gateway_url",
            "chat_history",
            "last_request_info",
        ]:
            st.session_state[key] = type(st.session_state[key])() if st.session_state[key] is not None else None
        st.session_state.logged_in = False
        st.rerun()

    st.divider()

    # Bearer token — auto-filled after login, user can clear to test 403
    st.markdown("**Bearer Token**")
    st.caption("Auto-filled after login. Clear it to test 403 rejection.")
    bearer_input = st.text_area(
        "Bearer Token",
        height=80,
        key="bearer_input",
        label_visibility="collapsed",
    )
    if bearer_input.strip():
        st.markdown(":green-background[Token will be sent with requests]")
    else:
        st.markdown(":red-background[No token — requests will get 403]")

# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------
st.markdown("#### Gateway Inbound + Outbound Auth")
st.markdown("""
```
Inbound:   You ──[JWT Token]──▶ AgentCore Runtime (validates via Cognito)

Outbound:  Agent ──▶ AgentCore Identity ──▶ OAuth2 Token ──▶ AgentCore Gateway ──▶ MCP Server
                     managed credential      auto-obtained    routes to tools       get_time, echo
```
""")
st.caption(
    "Clear the Bearer Token in the sidebar to see a 403 rejection. The agent has zero knowledge of upstream credentials — the Gateway handles everything."
)

# Chat history
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(_format_response(msg["content"]))

# Preset buttons
presets = [
    "What tools do you have?",
    "Get current time",
    "Echo 'Hello from AgentCore!'",
]
cols = st.columns(len(presets))
preset_clicked = None
for i, preset in enumerate(presets):
    if cols[i].button(preset, key=f"preset_{i}", use_container_width=True):
        preset_clicked = preset


def _send_prompt(prompt: str):
    """Send a prompt to the agent and update chat history."""
    if not agent_arn:
        st.error("Agent ARN not resolved. Deploy the agent first.")
        return

    st.session_state.chat_history.append({"role": "user", "content": prompt})

    # Use the token the user pasted (empty = no auth = 403)
    token = st.session_state.get("bearer_input", "").strip() or None

    with st.spinner("Invoking agent..."):
        result = invoke_agent(agent_arn, config["region"], prompt, bearer_token=token)

    st.session_state.last_request_info = result

    if result["error"]:
        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "content": f"**Error:**\n```\n{result['error']}\n```",
            }
        )
    else:
        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "content": result["text"],
            }
        )


# Handle preset click
if preset_clicked:
    _send_prompt(preset_clicked)
    st.rerun()

# Free text input
user_input = st.chat_input("Ask the agent...")
if user_input:
    _send_prompt(user_input)
    st.rerun()

# Status panel
if st.session_state.last_request_info:
    info = st.session_state.last_request_info
    with st.expander("Last Request Details", expanded=False):
        c1, c2, c3 = st.columns(3)
        c1.metric("Status Code", info.get("status_code") or "N/A")
        c2.metric("Response Time", f"{info.get('elapsed', 0):.2f}s")
        c3.metric("Auth", "With token" if info.get("auth_header") else "No token")
        if info.get("auth_header"):
            st.code(f"Authorization: {info['auth_header']}", language=None)
        if info.get("error"):
            st.error(info["error"])
