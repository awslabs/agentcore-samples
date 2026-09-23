import importlib.util
import json
import os
import time
import uuid
import streamlit as st
from chat import ChatManager, invoke_endpoint_streaming
from chat_utils import make_urls_clickable
from streamlit_cognito_auth import CognitoAuthenticator

# Load utils from project root using importlib to avoid E402
current_dir = os.path.dirname(os.path.abspath(__file__))
utils_path = os.path.join(current_dir, "..", "utils.py")
spec = importlib.util.spec_from_file_location("utils", utils_path)
utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utils)

secret = json.loads(utils.get_customer_support_secret())


authenticator = CognitoAuthenticator(
    pool_id=secret["pool_id"],
    app_client_id=secret["client_id"],
    app_client_secret=secret["client_secret"],
    use_cookies=False,
)

is_logged_in = authenticator.login()
if not is_logged_in:
    st.stop()


def logout():
    print("Logout in example")
    authenticator.logout()


CONTEXT_WINDOW = 10  # Number of turns (user+assistant pairs) to include in context
qualifier = "DEFAULT"


def build_context(messages, context_window=CONTEXT_WINDOW):
    # Only use the last context_window*2 messages (user+assistant pairs)
    history = messages[-context_window * 2 :] if len(messages) > context_window * 2 else messages
    context = ""
    for msg in history:
        role = "User" if msg["role"] == "user" else "Assistant"
        context += f"{role}: {msg['content']}\n"
    return context


def format_response_text(text):
    """Format response text by unescaping quotes and newlines"""
    if not text:
        return text

    # Remove outer quotes if present
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]

    # Unescape common escape sequences
    text = text.replace('\\"', '"')
    text = text.replace("\\n", "\n")
    text = text.replace("\\t", "\t")
    text = text.replace("\\r", "\r")

    return text


with st.sidebar:
    st.text(f"Welcome,\n{authenticator.get_username()}")
    st.button("Logout", "logout_btn", on_click=logout)

st.title("Customer Support Agent")

chat_manager = ChatManager("default")

if "session_id" not in st.session_state:
    st.session_state["session_id"] = uuid.uuidv4()

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat messages from history on app rerun
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Accept user input
if prompt := st.chat_input("What is up?"):
    # Display user message in chat message container
    with st.chat_message("user"):
        st.markdown(prompt)
    # Add user message to chat history
    st.session_state.messages.append({"role": "user", "content": prompt})
    payload = json.dumps({"prompt": prompt, "actor_id": st.session_state["auth_username"]})

    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        import time

        start_time = time.time()
        accumulated_response = ""

        try:
            # Setup streaming client
            session_id = st.session_state.get("session_id")
            context = build_context(st.session_state.messages, CONTEXT_WINDOW)
            payload = json.dumps({"prompt": context})
            bearer_token = st.session_state.get("auth_access_token")

            # Show initial thinking state with pulsing animation
            message_placeholder.markdown(
                '<span class="thinking-bubble">🤖 💭 Customer Support Agent is thinking...</span>',
                unsafe_allow_html=True,
            )

            # Stream the response with animations
            chunk_count = 0
            formatted_response = ""

            for chunk in invoke_endpoint_streaming(
                agent_arn=st.session_state["agent_arn"],
                payload=payload,
                session_id=session_id,
                bearer_token=bearer_token,
                endpoint_name=qualifier,
            ):
                if chunk.strip():  # Only process non-empty chunks
                    accumulated_response += chunk
                    chunk_count += 1

                    # Check if we have a complete response with End marker (quoted version)
                    if '"End agent execution"' in accumulated_response:
                        # Show processing state
                        message_placeholder.markdown(
                            '<span class="thinking-bubble">🤖 🔄 Processing response...</span>',
                            unsafe_allow_html=True,
                        )

                        # Parse the JSON and extract the formatted response
                        try:
                            # Find the JSON part between quoted Begin and End markers
                            begin_marker = '"Begin agent execution"'
                            end_marker = '"End agent execution"'

                            begin_pos = accumulated_response.find(begin_marker)
                            end_pos = accumulated_response.find(end_marker)

                            if begin_pos != -1 and end_pos != -1:
                                # Extract everything between the markers
                                json_part = accumulated_response[begin_pos + len(begin_marker) : end_pos].strip()

                                # The JSON should start immediately after the Begin marker
                                json_start = json_part.find('{"role":')
                                if json_start != -1:
                                    json_str = json_part[json_start:]
                                    # Find the end of the JSON object by counting braces
                                    brace_count = 0
                                    json_end = -1
                                    for i, char in enumerate(json_str):
                                        if char == "{":
                                            brace_count += 1
                                        elif char == "}":
                                            brace_count -= 1
                                            if brace_count == 0:
                                                json_end = i + 1
                                                break

                                    if json_end != -1:
                                        json_str = json_str[:json_end]
                                        print(f"Extracted JSON: {json_str}")  # Debug print
                                        response_data = json.loads(json_str)

                                        # Extract text from the JSON structure
                                        if (
                                            "content" in response_data
                                            and len(response_data["content"]) > 0
                                            and "text" in response_data["content"][0]
                                        ):
                                            formatted_response = response_data["content"][0]["text"]
                                            print(f"Extracted text: {formatted_response}")  # Debug print

                        except (json.JSONDecodeError, KeyError, IndexError) as e:
                            print(f"JSON parsing error: {e}")
                            print(f"Accumulated response: {accumulated_response}")
                            # Fallback to show full response for debugging
                            formatted_response = accumulated_response
                        break

                    # Display streaming text for non-JSON responses or while accumulating
                    else:
                        # Add typing cursor effect during streaming
                        streaming_text = accumulated_response
                        if chunk_count % 3 == 0:  # Add cursor every few chunks for effect
                            streaming_text += ""

                        # Update display with streaming animation (make URLs clickable)
                        clickable_streaming_text = make_urls_clickable(streaming_text)
                        message_placeholder.markdown(
                            f'<div class="assistant-bubble streaming typing-cursor">🤖 {clickable_streaming_text}</div>',
                            unsafe_allow_html=True,
                        )
                        # Small delay to make streaming visible and smooth
                        time.sleep(0.02)

            # Final response with timing (remove streaming classes)
            elapsed = time.time() - start_time
            answer = (
                formatted_response
                if formatted_response
                else (accumulated_response if accumulated_response else "No response received")
            )

            # Format the response to handle escaped characters
            answer = format_response_text(answer)

            clickable_answer = make_urls_clickable(answer)
            message_placeholder.markdown(
                f'<div class="assistant-bubble">🤖 {clickable_answer}<br><span style="font-size:0.9em;color:#888;">⏱️ Response time: {elapsed:.2f} seconds</span></div>',
                unsafe_allow_html=True,
            )

        except Exception as e:
            error_msg = f"Sorry, I encountered an error: {str(e)}"
            message_placeholder.markdown(
                f'<div class="assistant-bubble">🤖 ❌ {error_msg}</div>',
                unsafe_allow_html=True,
            )
            answer = error_msg
            elapsed = time.time() - start_time

        # Add final response to session state
        final_answer = answer if "answer" in locals() else accumulated_response
        st.session_state.messages.append({"role": "assistant", "content": final_answer, "elapsed": elapsed})
        st.session_state["pending_assistant"] = False
        st.rerun()

        accumulated_response = chat_manager.invoke_endpoint_nostreaming(
            agent_arn=st.session_state["agent_arn"],
            payload=payload,
            bearer_token=st.session_state["auth_access_token"],
            session_id=st.session_state["session_id"],
        )

        print(f"Response: {accumulated_response}")
        # Add assistant response to chat history
        st.session_state.messages.append({"role": "assistant", "content": accumulated_response})
