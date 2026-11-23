"""Single-user, loopback Streamlit UI for one operator-selected Runtime."""

import os
import uuid
from contextlib import closing

import boto3
import streamlit as st
from botocore.config import Config
from transport import chunks, region_for


def reset_chat():
    st.session_state.messages = []
    st.session_state.runtime_session_id = str(uuid.uuid4())


def main():
    st.set_page_config(page_title="AgentCore chat")
    st.title("AgentCore chat")
    arn = os.getenv("AGENT_RUNTIME_ARN", "")
    try:
        region = region_for(arn)
    except ValueError as exc:
        st.info(str(exc))
        return
    if st.session_state.get("target") != arn:
        reset_chat()
        st.session_state.target = arn
    with st.sidebar:
        st.caption("IAM-authenticated Runtime")
        st.code(arn, language=None)
        show_tools = st.checkbox("Show tool-use markers", value=True)
        st.button("New conversation", on_click=reset_chat)

    for role, message in st.session_state.messages:
        with st.chat_message(role):
            st.text(message)
    if prompt := st.chat_input("Message", max_chars=4000):
        with st.chat_message("user"):
            st.text(prompt)
        st.session_state.messages.append(("user", prompt))
        with st.chat_message("assistant"):
            display = st.empty()
            parts = []
            try:
                with (
                    boto3.client(
                        "bedrock-agentcore",
                        region_name=region,
                        config=Config(read_timeout=120, retries={"total_max_attempts": 1}),
                    ) as client,
                    closing(chunks(client, arn, st.session_state.runtime_session_id, prompt, show_tools)) as stream,
                ):
                    for part in stream:
                        parts.append(part)
                        display.text("".join(parts))
                if not parts:
                    raise ValueError("Empty answer")
            except Exception:  # noqa: BLE001 -- never put arbitrary remote exception bodies into chat.
                display.error(
                    "The response could not be completed. Check the Runtime contract and invocation permissions."
                )
                return
            st.session_state.messages.append(("assistant", "".join(parts)))


if __name__ == "__main__":
    main()
