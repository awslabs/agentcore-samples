#!/usr/bin/env python3
"""
Streamlit app for Agent with Guardrails
Matches the working test_simple_agent.py pattern
"""

import streamlit as st
import boto3
import json
import time

# Configure page
st.set_page_config(
    page_title="Agent with Guardrails",
    page_icon="🛡️",
    layout="wide"
)

# Custom CSS
st.markdown("""
<style>
    .stApp {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    }
    .main {
        background-color: white;
        border-radius: 10px;
        padding: 20px;
        margin: 20px;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state
if 'messages' not in st.session_state:
    st.session_state.messages = []

if 'agent_arn' not in st.session_state:
    try:
        ssm = boto3.client('ssm')
        response = ssm.get_parameter(Name='/simple_agent/runtime/agent_arn')
        st.session_state.agent_arn = response['Parameter']['Value']
    except:
        st.session_state.agent_arn = None

# Header
st.title("🛡️ Agent with Guardrails")
st.markdown("*Powered by AWS Bedrock AgentCore Runtime*")
st.markdown("---")

# Initialize guardrail logs in session state
if 'guardrail_logs' not in st.session_state:
    st.session_state.guardrail_logs = []

# Sidebar
with st.sidebar:
    st.header("Configuration")
    if st.session_state.agent_arn:
        st.success("✅ Agent Connected")
        st.code(st.session_state.agent_arn, language=None)
    else:
        st.error("❌ Agent not deployed")
        st.stop()
    
    st.markdown("---")
    st.markdown("### 🛡️ Guardrail Protection")
    st.info("All inputs and outputs are validated by AWS Bedrock Guardrails")
    
    # Show guardrail activity log
    st.markdown("#### 📊 Guardrail Activity")
    if st.session_state.guardrail_logs:
        for log in st.session_state.guardrail_logs[-5:]:  # Show last 5 activities
            if log['type'] == 'input':
                st.success(f"✅ Input validated: {log['time']}")
            elif log['type'] == 'output':
                st.success(f"✅ Output validated: {log['time']}")
            elif log['type'] == 'blocked':
                st.error(f"🚫 Blocked: {log['time']}")
    else:
        st.text("No activity yet")
    
    if st.button("Clear Chat"):
        st.session_state.messages = []
        st.session_state.guardrail_logs = []
        st.rerun()

# Display chat history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Chat input
if prompt := st.chat_input("Ask me anything..."):
    # Add user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # Get agent response
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        
        try:
            # Create client
            client = boto3.client('bedrock-agentcore')
            
            # Log input validation (happens in middleware)
            from datetime import datetime
            st.session_state.guardrail_logs.append({
                'type': 'input',
                'time': datetime.now().strftime("%H:%M:%S"),
                'prompt': prompt[:50] + "..." if len(prompt) > 50 else prompt
            })
            
            # Invoke agent - payload must be bytes
            payload_str = json.dumps({"prompt": prompt})
            payload_bytes = payload_str.encode('utf-8')
            
            with st.spinner("🛡️ Validating input with guardrails..."):
                response = client.invoke_agent_runtime(
                    agentRuntimeArn=st.session_state.agent_arn,
                    qualifier="DEFAULT",
                    payload=payload_bytes
                )
            
            # Stream response - response['response'] yields bytes directly
            full_response = ""
            if "response" in response:
                try:
                    for chunk in response["response"]:
                        # Each chunk is bytes, decode it
                        if isinstance(chunk, bytes):
                            decoded = chunk.decode("utf-8")
                            
                            # Check if content was blocked (starts with ⚠️)
                            if decoded.startswith("⚠️"):
                                # Content was blocked by guardrail
                                st.session_state.guardrail_logs.append({
                                    'type': 'blocked',
                                    'time': datetime.now().strftime("%H:%M:%S"),
                                    'reason': 'Input violated content policies'
                                })
                                message_placeholder.warning(decoded)
                                # Skip the normal flow
                                st.session_state.messages.append({
                                    "role": "assistant",
                                    "content": decoded
                                })
                                full_response = decoded  # Set to prevent further processing
                                break  # Break out of the loop
                            
                            full_response += decoded
                            # Show with cursor for streaming effect
                            message_placeholder.markdown(full_response + "▌")
                            time.sleep(0.02)
                except Exception as stream_error:
                    # Just raise the error - don't assume it's a guardrail block
                    raise stream_error
            
            # Only process normal responses if not blocked
            if full_response and not full_response.startswith("⚠️"):
                # Final display without cursor
                message_placeholder.markdown(full_response)
                
                # Log output validation (happens in middleware)
                st.session_state.guardrail_logs.append({
                    'type': 'output',
                    'time': datetime.now().strftime("%H:%M:%S"),
                    'response': full_response[:50] + "..." if len(full_response) > 50 else full_response
                })
                
                # Add to history
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": full_response
                })
                
                # Show guardrail info
                with st.expander("🛡️ Guardrail Validation Details"):
                    st.success("✅ Input passed guardrail validation")
                    st.success("✅ Output passed guardrail validation")
                    st.info("Note: The agent uses Starlette middleware to intercept and validate all inputs/outputs using AWS Bedrock Guardrails API")
            
        except Exception as e:
            import traceback
            error_msg = f"❌ Error: {str(e)}\n\n```\n{traceback.format_exc()}\n```"
            message_placeholder.error(error_msg)
            st.session_state.messages.append({
                "role": "assistant",
                "content": f"❌ Error: {str(e)}"
            })

# Example prompts
if not st.session_state.messages:
    st.markdown("### Try these examples:")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("🌤️ Weather"):
            st.session_state.messages.append({
                "role": "user",
                "content": "What is the weather in Seattle?"
            })
            st.rerun()
    
    with col2:
        if st.button("🔢 Math"):
            st.session_state.messages.append({
                "role": "user",
                "content": "Calculate 25 * 4 + 10"
            })
            st.rerun()
    
    with col3:
        if st.button("💬 About Bedrock"):
            st.session_state.messages.append({
                "role": "user",
                "content": "Tell me about AWS Bedrock"
            })
            st.rerun()
