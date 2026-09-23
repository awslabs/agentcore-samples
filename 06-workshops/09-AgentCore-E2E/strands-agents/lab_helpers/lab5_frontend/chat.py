import json
import time
import uuid
import urllib.parse
from typing import Any, Optional
import requests
import streamlit as st
from chat_utils import (
    make_urls_clickable,
    create_safe_markdown_text,
    get_aws_region,
    get_ssm_parameter,
)


def invoke_endpoint_streaming(
    agent_arn: str,
    payload,
    session_id: str,
    bearer_token: str,
    endpoint_name: str = "DEFAULT",
):
    """Invoke agent endpoint and yield streaming response chunks."""
    # Escape agent ARN for URL
    escaped_arn = urllib.parse.quote(agent_arn, safe="")

    # Build URL
    # url = f"{self.dp_endpoint}/runtimes/{escaped_arn}/invocations"
    url = f"https://bedrock-agentcore.{st.session_state['region']}.amazonaws.com/runtimes/{escaped_arn}/invocations"

    # Headers
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }

    # Parse the payload string back to JSON object to send properly
    try:
        body = json.loads(payload) if isinstance(payload, str) else payload
    except json.JSONDecodeError:
        # Fallback for non-JSON strings - wrap in payload object
        print("Failed to parse payload as JSON, wrapping in payload object")
        body = {"payload": payload}

    try:
        # Make streaming request
        response = requests.post(
            url,
            params={"qualifier": endpoint_name},
            headers=headers,
            json=body,
            timeout=100,
            stream=True,
        )
        response.raise_for_status()

        # Check if response is streaming
        if "text/event-stream" in response.headers.get("content-type", ""):
            # Handle streaming response
            for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                if line and line.startswith("data: "):
                    chunk = line[6:]  # Remove "data: " prefix
                    if chunk.strip():  # Only yield non-empty chunks
                        yield chunk
        else:
            # Non-streaming response, yield entire content
            if response.content:
                yield response.text

    except requests.exceptions.RequestException as e:
        print("Failed to invoke agent endpoint: %s", str(e))
        raise


class ChatManager:
    def format_response_text(self, text):
        """Format response text by unescaping quotes and newlines"""
        if not text:
            return text

        # Remove outer quotes if present
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]

        # Unescape common escape sequences
        text = text.replace("\\n", "\n")
        text = text.replace('\\"', '"')
        text = text.replace("\\t", "\t")
        text = text.replace("\\r", "\r")
        text = text.replace("\\\\", "\\")

        return text

    def __init__(self, agent_name: str = "default"):
        self.auth_url_matching = ".amazonaws.com/identities/oauth2/authorize"
        self.agent_name = agent_name
        self._init_session_state()

    def _init_session_state(self):
        """Initialize session state variables"""
        if "session_id" not in st.session_state:
            st.session_state["session_id"] = str(uuid.uuid4())

        if "agent_arn" not in st.session_state:
            agent_arn = get_ssm_parameter("/app/customersupport/agentcore/runtime_arn")
            st.session_state["agent_arn"] = agent_arn

        if "region" not in st.session_state:
            st.session_state["region"] = get_aws_region()

        if "messages" not in st.session_state:
            st.session_state["messages"] = []

        if "pending_assistant" not in st.session_state:
            st.session_state["pending_assistant"] = False

    def invoke_endpoint_nostreaming(
        self,
        agent_arn: str,
        payload,
        session_id: str,
        bearer_token: Optional[str],
        endpoint_name: str = "DEFAULT",
    ) -> Any:
        """Invoke agent endpoint using HTTP request with bearer token."""
        escaped_arn = urllib.parse.quote(agent_arn, safe="")
        url = f"https://bedrock-agentcore.{st.session_state['region']}.amazonaws.com/runtimes/{escaped_arn}/invocations"

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        }

        try:
            body = json.loads(payload) if isinstance(payload, str) else payload
        except json.JSONDecodeError:
            body = {"payload": payload}

        try:
            response = requests.post(
                url,
                params={"qualifier": endpoint_name},
                headers=headers,
                json=body,
                timeout=100,
            )
            return response

        except requests.exceptions.RequestException as e:
            print("Failed to invoke agent endpoint: %s", str(e))
            raise

        return None

    def invoke_endpoint(
        self,
        agent_arn: str,
        payload,
        session_id: str,
        bearer_token: Optional[str],
        endpoint_name: str = "DEFAULT",
    ) -> Any:
        """Invoke agent endpoint using HTTP request with bearer token."""
        escaped_arn = urllib.parse.quote(agent_arn, safe="")
        url = f"https://bedrock-agentcore.{st.session_state['region']}.amazonaws.com/runtimes/{escaped_arn}/invocations"

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        }

        try:
            body = json.loads(payload) if isinstance(payload, str) else payload
        except json.JSONDecodeError:
            body = {"payload": payload}

        try:
            response = requests.post(
                url,
                params={"qualifier": endpoint_name},
                headers=headers,
                json=body,
                timeout=100,
                stream=True,
            )
            last_data = False
            for line in response.iter_lines(chunk_size=1):
                if line:
                    line = line.decode("utf-8")
                    if line.startswith("data: "):
                        last_data = True
                        line = line[6:]
                        line = line.replace('"', "")
                        yield line
                    elif line:
                        line = line.replace('"', "")
                        if last_data:
                            yield "\n" + line
                        last_data = False

        except requests.exceptions.RequestException as e:
            print("Failed to invoke agent endpoint: %s", str(e))
            raise

    def display_chat_history(self):
        """Display chat messages from history"""
        messages_to_show = st.session_state.messages[:]

        if (
            st.session_state.get("pending_assistant", False)
            and messages_to_show
            and messages_to_show[-1]["role"] == "user"
        ):
            messages_to_show = messages_to_show[:-1]

        for message in messages_to_show:
            bubble_class = "user-bubble" if message["role"] == "user" else "assistant-bubble"
            emoji = "🧑‍💻" if message["role"] == "user" else "🤖"

            with st.chat_message(message["role"]):
                if message["role"] == "assistant" and "elapsed" in message:
                    clickable_content = make_urls_clickable(message["content"])
                    create_safe_markdown_text(
                        f'<div class="{bubble_class}">{emoji} {clickable_content}<br><span style="font-size:0.9em;color:#888;">⏱️ Response time: {message["elapsed"]:.2f} seconds</span></div>',
                        st,
                    )
                else:
                    if message["role"] == "assistant":
                        clickable_content = make_urls_clickable(message["content"])
                        create_safe_markdown_text(
                            f'<div class="{bubble_class}">{emoji} {clickable_content}</div>',
                            st,
                        )
                    else:
                        create_safe_markdown_text(
                            f'<span class="{bubble_class}">{emoji} {message["content"]}</span>',
                            st,
                        )

    def process_user_message(self, prompt: str, actor_id: str, bearer_token: str):
        """Process a user message and get assistant response"""
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("user"):
            create_safe_markdown_text(f'<span class="user-bubble">🧑‍💻 {prompt}</span>', st)
            st.session_state["pending_assistant"] = True

        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            start_time = time.time()

            create_safe_markdown_text(
                '<span class="thinking-bubble">🤖 💭 Customer Support Assistant is thinking...</span>',
                message_placeholder,
            )

            chunk_count = 0
            accumulated_response = ""

            for chunk in self.invoke_endpoint(
                agent_arn=st.session_state["agent_arn"],
                payload=json.dumps({"prompt": prompt, "actor_id": actor_id}),
                bearer_token=bearer_token,
                session_id=st.session_state["session_id"],
            ):
                chunk = str(chunk)

                print(f"Chunk: {chunk}")
                if chunk.strip():
                    if self.auth_url_matching in chunk:
                        accumulated_response = f"Please use {chunk}"
                    else:
                        accumulated_response += chunk
                    chunk_count += 1

                    if chunk_count % 3 == 0:
                        accumulated_response += ""

                    clickable_streaming_text = make_urls_clickable(accumulated_response)

                    create_safe_markdown_text(
                        f'<div class="assistant-bubble streaming typing-cursor">🤖 {clickable_streaming_text}</div>',
                        message_placeholder,
                    )

                    if self.auth_url_matching in accumulated_response:
                        accumulated_response = str()

                    time.sleep(0.02)

            elapsed = time.time() - start_time

            formatted_response = self.format_response_text(accumulated_response)
            clickable_streaming_text = make_urls_clickable(formatted_response)

            create_safe_markdown_text(
                f'<div class="assistant-bubble">🤖 {clickable_streaming_text}<br><span style="font-size:0.9em;color:#888;">⏱️ Response time: {elapsed:.2f} seconds</span></div>',
                message_placeholder,
            )

            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": accumulated_response,
                    "elapsed": elapsed,
                }
            )
            st.session_state["pending_assistant"] = False

    def initialize_default_conversation(self, email, actor_id, bearer_token: str):
        """Initialize the conversation with a default message"""
        if not st.session_state.messages:
            default_prompt = f"Hi my email is {email}"
            st.session_state.messages = [{"role": "user", "content": default_prompt}]

            with st.chat_message("user"):
                create_safe_markdown_text(f'<span class="user-bubble">🧑‍💻 {default_prompt}</span>', st)
                st.session_state["pending_assistant"] = True

            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                start_time = time.time()

                create_safe_markdown_text(
                    '<span class="thinking-bubble">🤖 💭 Customer Support Assistant is thinking...</span>',
                    message_placeholder,
                )

                chunk_count = 0
                accumulated_response = ""

                for chunk in self.invoke_endpoint(
                    agent_arn=st.session_state["agent_arn"],
                    payload=json.dumps(
                        {
                            "prompt": default_prompt,
                            "actor_id": actor_id,
                        }
                    ),
                    bearer_token=bearer_token,
                    session_id=st.session_state["session_id"],
                ):
                    chunk = str(chunk)
                    if chunk.strip():
                        accumulated_response += chunk
                        chunk_count += 1

                        if chunk_count % 3 == 0:
                            accumulated_response += ""

                        clickable_streaming_text = make_urls_clickable(accumulated_response)

                        create_safe_markdown_text(
                            f'<div class="assistant-bubble streaming typing-cursor">🤖 {clickable_streaming_text}</div>',
                            message_placeholder,
                        )

                        time.sleep(0.02)

                elapsed = time.time() - start_time
                clickable_answer = make_urls_clickable(accumulated_response)

                create_safe_markdown_text(
                    f'<div class="assistant-bubble">🤖 {clickable_answer}<br><span style="font-size:0.9em;color:#888;">⏱️ Response time: {elapsed:.2f} seconds</span></div>',
                    message_placeholder,
                )

                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": accumulated_response,
                        "elapsed": elapsed,
                    }
                )
                st.session_state["pending_assistant"] = False
                st.rerun()
