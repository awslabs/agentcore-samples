import json
import re
import time
import uuid
from typing import Dict, Iterator, List

import boto3
import streamlit as st
from streamlit.logger import get_logger

logger = get_logger(__name__)
logger.setLevel("INFO")

# Page config
st.set_page_config(
    page_title="Bedrock AgentCore Chat",
    page_icon="static/gen-ai-dark.svg",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Remove Streamlit deployment components
st.markdown(
    """
      <style>
        .stAppDeployButton {display:none;}
        #MainMenu {visibility: hidden;}
      </style>
    """,
    unsafe_allow_html=True,
)

HUMAN_AVATAR = "static/user-profile.svg"
AI_AVATAR = "static/gen-ai-dark.svg"


def fetch_agent_runtimes(region: str = "us-east-1") -> List[Dict]:
    """Fetch available agent runtimes from bedrock-agentcore-control"""
    try:
        client = boto3.client("bedrock-agentcore-control", region_name=region)
        response = client.list_agent_runtimes(maxResults=100)

        # Filter only READY agents and sort by name
        ready_agents = [agent for agent in response.get("agentRuntimes", []) if agent.get("status") == "READY"]

        # Sort by most recent update time (newest first)
        ready_agents.sort(key=lambda x: x.get("lastUpdatedAt", ""), reverse=True)

        return ready_agents
    except Exception as e:
        st.error(f"Error fetching agent runtimes: {e}")
        return []


def fetch_agent_runtime_versions(agent_runtime_id: str, region: str = "us-east-1") -> List[Dict]:
    """Fetch versions for a specific agent runtime"""
    try:
        client = boto3.client("bedrock-agentcore-control", region_name=region)
        response = client.list_agent_runtime_versions(agentRuntimeId=agent_runtime_id)

        # Filter only READY versions
        ready_versions = [version for version in response.get("agentRuntimes", []) if version.get("status") == "READY"]

        # Sort by most recent update time (newest first)
        ready_versions.sort(key=lambda x: x.get("lastUpdatedAt", ""), reverse=True)

        return ready_versions
    except Exception as e:
        st.error(f"Error fetching agent runtime versions: {e}")
        return []


def clean_response_text(text: str, show_thinking: bool = True) -> str:
    """Clean and format response text for better presentation"""
    if not text:
        return text

    # Handle the consecutive quoted chunks pattern
    # Pattern: "word1" "word2" "word3" -> word1 word2 word3
    text = re.sub(r'"\s*"', "", text)
    text = re.sub(r'^"', "", text)
    text = re.sub(r'"$', "", text)

    # Replace literal \n with actual newlines
    text = text.replace("\\n", "\n")

    # Replace literal \t with actual tabs
    text = text.replace("\\t", "\t")

    # Clean up multiple spaces
    text = re.sub(r" {3,}", " ", text)

    # Fix newlines that got converted to spaces
    text = text.replace(" \n ", "\n")
    text = text.replace("\n ", "\n")
    text = text.replace(" \n", "\n")

    # Handle numbered lists
    text = re.sub(r"\n(\d+)\.\s+", r"\n\1. ", text)
    text = re.sub(r"^(\d+)\.\s+", r"\1. ", text)

    # Handle bullet points
    text = re.sub(r"\n-\s+", r"\n- ", text)
    text = re.sub(r"^-\s+", r"- ", text)

    # Handle section headers
    text = re.sub(r"\n([A-Za-z][A-Za-z\s]{2,30}):\s*\n", r"\n**\1:**\n\n", text)

    # Clean up multiple newlines
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Clean up thinking

    if not show_thinking:
        text = re.sub(r"<thinking>.*?</thinking>", "", text)

    return text.strip()


def extract_text_from_response(data) -> str:
    """Extract text content from response data in various formats"""
    if isinstance(data, dict):
        # Handle format: {'role': 'assistant', 'content': [{'text': 'Hello!'}]}
        if "role" in data and "content" in data:
            content = data["content"]
            if isinstance(content, list) and len(content) > 0:
                if isinstance(content[0], dict) and "text" in content[0]:
                    return str(content[0]["text"])
                else:
                    return str(content[0])
            elif isinstance(content, str):
                return content
            else:
                return str(content)

        # Handle other common formats
        if "text" in data:
            return str(data["text"])
        elif "content" in data:
            content = data["content"]
            if isinstance(content, str):
                return content
            else:
                return str(content)
        elif "message" in data:
            return str(data["message"])
        elif "response" in data:
            return str(data["response"])
        elif "result" in data:
            return str(data["result"])

    return str(data)


def parse_streaming_chunk(chunk: str) -> str:
    """Parse individual streaming chunk and extract meaningful content"""
    logger.debug(f"parse_streaming_chunk: received chunk: {chunk}")
    logger.debug(f"parse_streaming_chunk: chunk type: {type(chunk)}")

    try:
        # Try to parse as JSON first
        if chunk.strip().startswith("{"):
            logger.debug("parse_streaming_chunk: Attempting JSON parse")
            data = json.loads(chunk)
            logger.debug(f"parse_streaming_chunk: Successfully parsed JSON: {data}")

            # Handle the specific format: {'role': 'assistant', 'content': [{'text': '...'}]}
            if isinstance(data, dict) and "role" in data and "content" in data:
                content = data["content"]
                if isinstance(content, list) and len(content) > 0:
                    first_item = content[0]
                    if isinstance(first_item, dict) and "text" in first_item:
                        extracted_text = first_item["text"]
                        logger.debug(f"parse_streaming_chunk: Extracted text: {extracted_text}")
                        return extracted_text
                    else:
                        return str(first_item)
                else:
                    return str(content)
            else:
                # Use the general extraction function for other formats
                return extract_text_from_response(data)

        # If not JSON, return the chunk as-is
        logger.debug("parse_streaming_chunk: Not JSON, returning as-is")
        return chunk
    except json.JSONDecodeError as e:
        logger.error(f"parse_streaming_chunk: JSON decode error: {e}")

        # Try to handle Python dict string representation (with single quotes)
        if chunk.strip().startswith("{") and "'" in chunk:
            logger.debug("parse_streaming_chunk: Attempting to handle Python dict string")
            try:
                # Try to convert single quotes to double quotes for JSON parsing
                # This is a simple approach - might need refinement for complex cases
                json_chunk = chunk.replace("'", '"')
                data = json.loads(json_chunk)
                logger.debug(f"parse_streaming_chunk: Successfully converted and parsed: {data}")

                # Handle the specific format
                if isinstance(data, dict) and "role" in data and "content" in data:
                    content = data["content"]
                    if isinstance(content, list) and len(content) > 0:
                        first_item = content[0]
                        if isinstance(first_item, dict) and "text" in first_item:
                            extracted_text = first_item["text"]
                            logger.debug(f"parse_streaming_chunk: Extracted text from converted dict: {extracted_text}")
                            return extracted_text
                        else:
                            return str(first_item)
                    else:
                        return str(content)
                else:
                    return extract_text_from_response(data)
            except json.JSONDecodeError:
                logger.debug("parse_streaming_chunk: Failed to convert Python dict string")
                pass

        # If all parsing fails, return the chunk as-is
        logger.debug("parse_streaming_chunk: All parsing failed, returning chunk as-is")
        return chunk


def invoke_agent_streaming(
    prompt: str,
    agent_arn: str,
    runtime_session_id: str,
    region: str = "us-east-1",
    show_tool: bool = True,
) -> Iterator[str]:
    """Invoke agent and yield streaming response chunks"""
    try:
        agentcore_client = boto3.client("bedrock-agentcore", region_name=region)

        boto3_response = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            qualifier="DEFAULT",
            runtimeSessionId=runtime_session_id,
            payload=json.dumps({"prompt": prompt}),
        )

        logger.debug(f"contentType: {boto3_response.get('contentType', 'NOT_FOUND')}")

        if "text/event-stream" in boto3_response.get("contentType", ""):
            logger.debug("Using streaming response path")
            # Handle streaming response
            for line in boto3_response["response"].iter_lines(chunk_size=1):
                if line:
                    line = line.decode("utf-8")
                    logger.debug(f"Raw line: {line}")
                    if line.startswith("data: "):
                        line = line[6:]
                        logger.debug(f"Line after removing 'data: ': {line}")
                        # Parse and clean each chunk
                        parsed_chunk = parse_streaming_chunk(line)
                        if parsed_chunk.strip():  # Only yield non-empty chunks
                            if "🔧 Using tool:" in parsed_chunk and not show_tool:
                                yield ""
                            else:
                                yield parsed_chunk
                    else:
                        logger.debug(f"Line doesn't start with 'data: ', skipping: {line}")
        else:
            logger.debug("Using non-streaming response path")
            # Handle non-streaming JSON response
            try:
                response_obj = boto3_response.get("response")
                logger.debug(f"response_obj type: {type(response_obj)}")

                if hasattr(response_obj, "read"):
                    # Read the response content
                    content = response_obj.read()
                    if isinstance(content, bytes):
                        content = content.decode("utf-8")

                    logger.debug(f"Raw content: {content}")

                    try:
                        # Try to parse as JSON and extract text
                        response_data = json.loads(content)
                        logger.debug(f"Parsed JSON: {response_data}")

                        # Handle the specific format we're seeing
                        if isinstance(response_data, dict):
                            # Check for 'result' wrapper first
                            if "result" in response_data:
                                actual_data = response_data["result"]
                            else:
                                actual_data = response_data

                            # Extract text from the nested structure
                            if "role" in actual_data and "content" in actual_data:
                                content_list = actual_data["content"]
                                if isinstance(content_list, list) and len(content_list) > 0:
                                    first_item = content_list[0]
                                    if isinstance(first_item, dict) and "text" in first_item:
                                        extracted_text = first_item["text"]
                                        logger.debug(f"Extracted text: {extracted_text}")
                                        yield extracted_text
                                    else:
                                        yield str(first_item)
                                else:
                                    yield str(content_list)
                            else:
                                # Use general extraction
                                text = extract_text_from_response(actual_data)
                                yield text
                        else:
                            yield str(response_data)

                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error: {e}")
                        # If not JSON, yield raw content
                        yield content
                elif isinstance(response_obj, dict):
                    # Direct dict response
                    text = extract_text_from_response(response_obj)
                    yield text
                else:
                    logger.debug(f"Unexpected response_obj type: {type(response_obj)}")
                    yield "No response content"

            except Exception as e:
                logger.error(f"Exception in non-streaming: {e}")
                yield f"Error reading response: {e}"

    except Exception as e:
        yield f"Error invoking agent: {e}"


def main():
    st.logo("static/agentcore-service-icon.png", size="large")
    st.title("Amazon Bedrock AgentCore Chat")

    # Sidebar for settings
    with st.sidebar:
        st.header("Settings")

        # Region selection (moved up since it affects agent fetching)
        region = st.selectbox(
            "AWS Region",
            ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"],
            index=0,
        )

        # Agent selection
        st.subheader("Agent Selection")

        # Fetch available agents
        with st.spinner("Loading available agents..."):
            available_agents = fetch_agent_runtimes(region)

        if available_agents:
            # Get unique agent names and their runtime IDs
            unique_agents = {}
            for agent in available_agents:
                name = agent.get("agentRuntimeName", "Unknown")
                runtime_id = agent.get("agentRuntimeId", "")
                if name not in unique_agents:
                    unique_agents[name] = runtime_id

            # Create agent name options
            agent_names = list(unique_agents.keys())

            # Agent name selection dropdown
            col1, col2 = st.columns([2, 1])

            with col1:
                selected_agent_name = st.selectbox(
                    "Agent Name",
                    options=agent_names,
                    help="Choose an agent to chat with",
                )

            # Get versions for the selected agent using the specific API
            if selected_agent_name and selected_agent_name in unique_agents:
                agent_runtime_id = unique_agents[selected_agent_name]

                with st.spinner("Loading versions..."):
                    agent_versions = fetch_agent_runtime_versions(agent_runtime_id, region)

                if agent_versions:
                    version_options = []
                    version_arn_map = {}

                    for version in agent_versions:
                        version_num = version.get("agentRuntimeVersion", "Unknown")
                        arn = version.get("agentRuntimeArn", "")
                        updated = version.get("lastUpdatedAt", "")
                        description = version.get("description", "")

                        # Format version display with update time
                        version_display = f"v{version_num}"
                        if updated:
                            try:
                                if hasattr(updated, "strftime"):
                                    updated_str = updated.strftime("%m/%d %H:%M")
                                    version_display += f" ({updated_str})"
                            except Exception:
                                pass

                        version_options.append(version_display)
                        version_arn_map[version_display] = {
                            "arn": arn,
                            "description": description,
                        }

                    with col2:
                        selected_version = st.selectbox(
                            "Version",
                            options=version_options,
                            help="Choose the version to use",
                        )

                    # Get the ARN for the selected agent and version
                    version_info = version_arn_map.get(selected_version, {})
                    agent_arn = version_info.get("arn", "")
                    description = version_info.get("description", "")

                    # Show selected agent info
                    if agent_arn:
                        st.info(f"Selected: {selected_agent_name} {selected_version}")
                        if description:
                            st.caption(f"Description: {description}")
                        with st.expander("View ARN"):
                            st.code(agent_arn)
                else:
                    st.warning(f"No versions found for {selected_agent_name}")
                    agent_arn = ""
            else:
                agent_arn = ""
        else:
            st.error("No agent runtimes found or error loading agents")
            agent_arn = ""

            # Fallback manual input
            st.subheader("Manual ARN Input")
            agent_arn = st.text_input("Agent ARN", value="", help="Enter your Bedrock AgentCore ARN manually")
        if st.button("Refresh", key="refresh_agents", help="Refresh agent list"):
            st.rerun()

        # Runtime Session ID
        st.subheader("Session Configuration")

        # Initialize session ID in session state if not exists
        if "runtime_session_id" not in st.session_state:
            st.session_state.runtime_session_id = str(uuid.uuid4())

        # Session ID input with generate button
        runtime_session_id = st.text_input(
            "Runtime Session ID",
            value=st.session_state.runtime_session_id,
            help="Unique identifier for this runtime session",
        )

        if st.button("Refresh", help="Generate new session ID and clear chat"):
            st.session_state.runtime_session_id = str(uuid.uuid4())
            st.session_state.messages = []  # Clear chat messages when resetting session
            st.rerun()

        # Update session state if user manually changed the ID
        if runtime_session_id != st.session_state.runtime_session_id:
            st.session_state.runtime_session_id = runtime_session_id

        # Response formatting options
        st.subheader("Display Options")
        auto_format = st.checkbox(
            "Auto-format responses",
            value=True,
            help="Automatically clean and format responses",
        )
        show_raw = st.checkbox(
            "Show raw response",
            value=False,
            help="Display the raw unprocessed response",
        )
        show_tools = st.checkbox(
            "Show tools",
            value=True,
            help="Display tools used",
        )
        show_thinking = st.checkbox(
            "Show thinking",
            value=False,
            help="Display the AI thinking text",
        )

        # Clear chat button
        if st.button("🗑️ Clear Chat"):
            st.session_state.messages = []
            st.rerun()

        # Connection status
        st.divider()
        if agent_arn:
            st.success("✅ Agent selected and ready")
        else:
            st.error("❌ Please select an agent")

    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Display chat messages
    for message in st.session_state.messages:
        with st.chat_message(message["role"], avatar=message["avatar"]):
            st.markdown(message["content"])

    # Chat input
    if prompt := st.chat_input("Type your message here..."):
        if not agent_arn:
            st.error("Please select an agent in the sidebar first.")
            return

        # Add user message to chat history
        st.session_state.messages.append({"role": "user", "content": prompt, "avatar": HUMAN_AVATAR})
        with st.chat_message("user", avatar=HUMAN_AVATAR):
            st.markdown(prompt)

        # Generate assistant response
        with st.chat_message("assistant", avatar=AI_AVATAR):
            message_placeholder = st.empty()
            chunk_buffer = ""

            try:
                # Stream the response
                for chunk in invoke_agent_streaming(
                    prompt,
                    agent_arn,
                    st.session_state.runtime_session_id,
                    region,
                    show_tools,
                ):
                    # Let's see what we get
                    logger.debug(f"MAIN LOOP: chunk type: {type(chunk)}")
                    logger.debug(f"MAIN LOOP: chunk content: {chunk}")

                    # Ensure chunk is a string before concatenating
                    if not isinstance(chunk, str):
                        logger.debug("MAIN LOOP: Converting non-string chunk to string")
                        chunk = str(chunk)

                    # Add chunk to buffer
                    chunk_buffer += chunk

                    # Only update display every few chunks or when we hit certain characters
                    if len(chunk_buffer) % 3 == 0 or chunk.endswith(" ") or chunk.endswith("\n"):
                        if auto_format:
                            # Clean the accumulated response
                            cleaned_response = clean_response_text(chunk_buffer, show_thinking)
                            message_placeholder.markdown(cleaned_response + " ▌")
                        else:
                            # Show raw response
                            message_placeholder.markdown(chunk_buffer + " ▌")

                    time.sleep(0.01)  # Reduced delay since we're batching updates

                # Final response without cursor
                if auto_format:
                    full_response = clean_response_text(chunk_buffer, show_thinking)
                else:
                    full_response = chunk_buffer

                message_placeholder.markdown(full_response)

                # Show raw response in expander if requested
                if show_raw and auto_format:
                    with st.expander("View raw response"):
                        st.text(chunk_buffer)

            except Exception as e:
                error_msg = f"❌ **Error:** {str(e)}"
                message_placeholder.markdown(error_msg)
                full_response = error_msg

        # Add assistant response to chat history
        st.session_state.messages.append({"role": "assistant", "content": full_response, "avatar": AI_AVATAR})


if __name__ == "__main__":
    main()
