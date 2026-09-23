"""
Streamlit UI for Lakehouse Agent with Cognito User Authentication
"""

import streamlit as st
import json
import uuid
import boto3
import os
from typing import Optional

st.set_page_config(page_title="Lakehouse Data Assistant", page_icon="🏥", layout="wide")

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
if "cognito_config" not in st.session_state:
    st.session_state.cognito_config = {}
if "example_prompt" not in st.session_state:
    st.session_state.example_prompt = None


def load_config_from_ssm():
    """Load configuration from SSM Parameter Store"""
    try:
        session = boto3.Session()
        # Get region with proper fallback
        region = (
            session.region_name or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        )
        ssm = boto3.client("ssm", region_name=region)

        config = {}
        params = {
            "runtime_arn": "/app/lakehouse-agent/agent-runtime-arn",
            "cognito_user_pool_id": "/app/lakehouse-agent/cognito-user-pool-id",
            "cognito_app_client_id": "/app/lakehouse-agent/cognito-app-client-id",
            "cognito_domain": "/app/lakehouse-agent/cognito-domain",
            "cognito_region": "/app/lakehouse-agent/cognito-region",
        }

        for key, param_name in params.items():
            try:
                response = ssm.get_parameter(Name=param_name)
                config[key] = response["Parameter"]["Value"]
            except Exception:
                config[key] = None

        config["region"] = region
        return config
    except Exception as e:
        st.error(f"Failed to load config from SSM: {e}")
        # Return config with at least region set
        session = boto3.Session()
        return {
            "region": (
                session.region_name
                or os.environ.get("AWS_REGION")
                or os.environ.get("AWS_DEFAULT_REGION")
                or "us-east-1"
            )
        }


def authenticate_user(username: str, password: str, user_pool_id: str, client_id: str, region: str) -> Optional[dict]:
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
        import hmac
        import hashlib
        import base64

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

        if "ChallengeName" in response:
            if response["ChallengeName"] == "NEW_PASSWORD_REQUIRED":
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
) -> Optional[dict]:
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
        import hmac
        import hashlib
        import base64

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
    """Invoke AgentCore Runtime with OAuth bearer token via HTTPS"""
    try:
        import requests
        import urllib.parse

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

        # Prepare payload
        payload = {"prompt": prompt, "bearer_token": access_token, "id_token": id_token}

        st.info("🔗 Invoking AgentCore Runtime with OAuth")

        # Make HTTPS request
        response = requests.post(url, headers=headers, json=payload, timeout=60)

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
        return f"❌ Request error: {str(e)}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


# Load configuration from SSM on first run
if not st.session_state.cognito_config:
    with st.spinner("Loading configuration from SSM..."):
        st.session_state.cognito_config = load_config_from_ssm()
        if st.session_state.cognito_config.get("runtime_arn"):
            st.session_state.runtime_arn = st.session_state.cognito_config["runtime_arn"]

# Sidebar configuration
with st.sidebar:
    st.title("🏥 Claims Assistant")
    st.markdown("---")

    # Login section
    if not st.session_state.access_token:
        with st.expander("🔐 User Login", expanded=True):
            st.markdown("*Default password: TempPass123!*")
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
            password = st.text_input("Password", type="password", placeholder="TempPass123!")

            config = st.session_state.cognito_config

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
                        config = st.session_state.cognito_config
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
            st.rerun()

    st.markdown("---")

    with st.expander("⚙️ Runtime Configuration", expanded=False):
        runtime_arn = st.text_input("Runtime ARN", value=st.session_state.runtime_arn)
        st.session_state.runtime_arn = runtime_arn

        config = st.session_state.cognito_config
        region = st.text_input("AWS Region", value=config.get("region", "us-east-1"))

        if st.button("🔄 Reload from SSM", use_container_width=True):
            st.session_state.cognito_config = load_config_from_ssm()
            if st.session_state.cognito_config.get("runtime_arn"):
                st.session_state.runtime_arn = st.session_state.cognito_config["runtime_arn"]
            st.success("✅ Configuration reloaded")
            st.rerun()

    st.markdown("---")
    st.markdown("### 💡 Example Queries")

    # Customize examples based on user email/group
    user_email = st.session_state.user_email or ""

    if "admin" in user_email.lower():
        # Admin queries - can access login audit and text-to-sql
        examples = [
            "Show user login info",
            "How many policyholders do we have?",
            "Show all users in the system",
        ]
    elif "adjuster" in user_email.lower():
        # Adjuster queries - can see claims they're assigned to
        examples = [
            "Show me all my assigned claims",
            "What's the status of CLM-2024-001?",
            "Get claims summary for my cases",
            "Show pending claims I'm handling",
        ]
    else:
        # Policyholder queries - can only see their own claims
        examples = [
            "Show me all my claims",
            "What's the status of CLM-2024-001?",
            "Get my claims summary",
            "Show my pending claims",
        ]

    for ex in examples:
        if st.button(ex, key=f"ex_{ex[:15]}", use_container_width=True):
            st.session_state.example_prompt = ex

# Main interface
st.title("🏥 Lakehouse Agent")
st.markdown(f"Ask me about your lakehouse data! *Logged in as: {st.session_state.user_email or 'Not logged in'}*")

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

# Handle example prompt from sidebar
if "example_prompt" in st.session_state and st.session_state.example_prompt:
    prompt = st.session_state.example_prompt
    st.session_state.example_prompt = None  # Clear it immediately

    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        config = st.session_state.cognito_config
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
        st.session_state.messages.append({"role": "assistant", "content": response})

    # Force rerun to show the chat input again
    st.rerun()

# Handle regular chat input
prompt = st.chat_input("Ask about your claims...")

if prompt:
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        config = st.session_state.cognito_config
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
        st.session_state.messages.append({"role": "assistant", "content": response})

    # Rerun to clear the input and show updated chat
    st.rerun()
