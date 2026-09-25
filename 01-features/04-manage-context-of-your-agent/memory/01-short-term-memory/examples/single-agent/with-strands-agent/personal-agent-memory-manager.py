#!/usr/bin/env python

# # Strands Agents with AgentCore Memory (Short-Term Memory) - Using MemoryManager
#
#
# ## Introduction
#
# This tutorial demonstrates how to build a **personal agent** using Strands agents with AgentCore **short-term memory** using **MemoryManager** and **MemorySessionManager**. The agent remembers recent conversations in the session using `get_last_k_turns` and can continue conversations seamlessly when user returns.
#
# **NOTE: This is the Short Term Memory Sample version using the MemoryManager & MemorySessionManager.**
#
#
# ### Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Short Term Conversational                                                        |
# | Agent type          | Personal Agent                                                                   |
# | Agentic Framework   | Strands Agents                                                                   |
# | LLM model           | Anthropic Claude Haiku 4.5                                                      |
# | Tutorial components | AgentCore Short-term Memory with MemoryManager, AgentInitializedEvent and MessageAddedEvent hooks   |
# | Example complexity  | Beginner                                                                         |
#
# You'll learn to:
# - Use short-term memory for conversation continuity with MemoryManager
# - Retrieve last K conversation turns using MemorySessionManager
# - Web search tool for real-time information
# - Initialize agents with conversation history using session management
# - Can use this to help Migrate from MemoryClient to MemoryManager architecture
#
# ## Architecture
# <div style="text-align:left">
#     <img src="architecture.png" width="65%" />
# </div>
#
# ## Prerequisites
#
# To execute this tutorial you will need:
# - Python 3.10+
# - AWS credentials with AgentCore Memory permissions
# - Amazon Bedrock AgentCore SDK with MemoryManager support
# - Access to Amazon Bedrock models
#
# Let's get started by setting up our environment!

# ## Step 1: Setup and Imports


# Run: pip install -qr requirements.txt


import logging
from datetime import datetime
from botocore.exceptions import ClientError

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("personal-agent")


# Import required modules for Strands Agent
import os  # noqa: E402
from strands import Agent, tool  # noqa: E402
from strands.hooks import (  # noqa: E402
    AgentInitializedEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)

# Import memory management modules
from bedrock_agentcore.memory import MemoryClient  # noqa: E402
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole  # noqa: E402
from bedrock_agentcore.memory.session import MemorySession, MemorySessionManager  # noqa: E402

# Define message role constants
USER = MessageRole.USER
ASSISTANT = MessageRole.ASSISTANT

# Configuration
REGION = os.getenv("AWS_REGION", "us-east-1")  # AWS region for the agent
ACTOR_ID = "user_123"  # It can be any unique identifier (AgentID, User ID, etc.)
SESSION_ID = "personal_session_001"  # Unique session identifier

# Import boto3 for IAM role creation


# ## Step 2: Web Search Tool
#
# First, let's create a simple web search tool for the agent. This remains unchanged from the original implementation.


from ddgs.exceptions import DDGSException, RatelimitException  # noqa: E402
from ddgs import DDGS  # noqa: E402


@tool
def websearch(keywords: str, region: str = "us-en", max_results: int = 5) -> str:
    """Search the web for updated information.

    Args:
        keywords (str): The search query keywords.
        region (str): The search region: wt-wt, us-en, uk-en, ru-ru, etc..
        max_results (int | None): The maximum number of results to return.
    Returns:
        List of dictionaries with search results.

    """
    try:
        results = DDGS().text(keywords, region=region, max_results=max_results)
        return results if results else "No results found."
    except RatelimitException:
        return "Rate limit reached. Please try again later."
    except DDGSException as e:
        return f"Search error: {e}"
    except Exception as e:
        return f"Search error: {str(e)}"


logger.info("✅ Web search tool ready")


# ## Step 3: Create Memory Resource using MemoryManager
#
# For short-term memory, we create a memory resource without any strategies using MemoryManager. This stores raw conversation turns that can be retrieved with `get_last_k_turns`.
#
# **NOTE: This section uses the MemoryManager architecture instead of the legacy MemoryClient.**


# Initialize Memory Client
memory_client = MemoryClient(region_name=REGION)
memory_name = "PersonalAgentMemoryManager"

logger.info(f"✅ MemoryClient initialized for region: {REGION}")

# Create memory resource using MemoryClient
logger.info(f"Creating memory '{memory_name}' for short-term conversational storage...")

memory_id = None
try:
    memory = memory_client.create_memory_and_wait(
        name=memory_name,
        strategies=[],  # No strategies for short-term memory
        description="Short-term memory for personal agent",
        event_expiry_days=7,  # Retention period for short-term memory
    )
    memory_id = memory["id"]
    logger.info("✅ Successfully created memory:")
    logger.info(f"   Memory ID: {memory_id}")
    logger.info(f"   Memory Name: {memory_name}")
    logger.info(f"   Memory Status: {memory['status']}")

except ClientError as e:
    if e.response["Error"]["Code"] == "ValidationException" and "already exists" in str(e):
        logger.info(f"Memory '{memory_name}' already exists, retrieving ID...")
        memories = memory_client.list_memories()
        memory_id = next((m["id"] for m in memories if m["id"].startswith(memory_name)), None)
        if not memory_id:
            raise RuntimeError(f"Memory '{memory_name}' not found after conflict")
        memory = {"id": memory_id, "name": memory_name}
        logger.info(f"✅ Retrieved existing memory: {memory_id}")
    else:
        logger.error(f"❌ Memory creation failed: {e}")
        raise


# ## Step 4: Initialize Session Manager
#
# This section introduces the MemorySessionManager for session-based memory operations and creates a MemorySession to manage actor & session


# Initialize the session memory manager
session_manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)

# Create a memory session for the specific actor/session combination
user_session = session_manager.create_memory_session(actor_id=ACTOR_ID, session_id=SESSION_ID)

logger.info(f"✅ Session manager initialized for memory: {memory_id}")
logger.info(f"✅ Memory session created for actor: {ACTOR_ID}, session: {SESSION_ID}")
logger.info(f"Session manager type: {type(session_manager)}")
logger.info(f"Memory session type: {type(user_session)}")


# ## Step 5: Memory Hook Provider
#
# This step defines our custom `MemoryHookProvider` class that automates memory operations using the MemorySession. Hooks are special functions that run at specific points in an agent's execution lifecycle. The memory hook we're creating serves two primary functions:
# 1. **To load recent conversation**: We use the `AgentInitializedEvent` hook to automatically load recent conversation history when the agent is initialized.
# 2. **To store the last message**: Stores new conversational messages using the session manager.
#
# **KEY CHANGES from MemoryClient version:**
# - Uses MemorySession instead of MemoryClient
# - Uses ConversationalMessage objects instead of tuples
# - Uses add_turns() instead of create_event()
# - Uses MessageRole enum for type safety


class MemoryHookProvider(HookProvider):
    def __init__(self, memory_session: MemorySession):  # Accept MemorySession instead
        self.memory_session = memory_session

    def on_agent_initialized(self, event: AgentInitializedEvent):
        """Load recent conversation history when agent starts using MemorySession"""
        try:
            # Use the pre-configured memory session (no need for actor_id/session_id)
            recent_turns = self.memory_session.get_last_k_turns(k=5)

            if recent_turns:
                # Format conversation history for context
                context_messages = []
                for turn in recent_turns:
                    for message in turn:
                        # Handle both EventMessage objects and dict formats
                        if hasattr(message, "role") and hasattr(message, "content"):
                            role = message["role"]
                            content = message["content"]
                        else:
                            role = message.get("role", "unknown")
                            content = message.get("content", {}).get("text", "")
                        context_messages.append(f"{role}: {content}")

                context = "\n".join(context_messages)
                # Add context to agent's system prompt
                event.agent.system_prompt += f"\n\nRecent conversation:\n{context}"
                logger.info(f"✅ Loaded {len(recent_turns)} conversation turns using MemorySession")

        except Exception as e:
            logger.error(f"Memory load error: {e}")

    def on_message_added(self, event: MessageAddedEvent):
        """Store messages in memory using MemorySession"""
        messages = event.agent.messages
        try:
            if messages and len(messages) > 0 and messages[-1]["content"][0].get("text"):
                message_text = messages[-1]["content"][0]["text"]
                message_role = MessageRole.USER if messages[-1]["role"] == "user" else MessageRole.ASSISTANT

                # Use memory session instance (no need to pass actor_id/session_id)
                result = self.memory_session.add_turns(messages=[ConversationalMessage(message_text, message_role)])

                event_id = result["eventId"]
                logger.info(f"✅ Stored message with Event ID: {event_id}, Role: {message_role.value}")

        except Exception as e:
            logger.error(f"Memory save error: {e}")
            import traceback

            logger.error(f"Full traceback: {traceback.format_exc()}")

    def register_hooks(self, registry: HookRegistry):
        # Register memory hooks
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)
        logger.info("✅ Memory hooks registered with MemorySession")


# ## Step 6: Create Personal Agent with Web Search
#
# This agent uses the MemoryHookProvider that works with MemorySession created from MemorySessionManager.


def create_personal_agent():
    """Create personal agent with memory and web search using MemorySession"""
    agent = Agent(
        name="PersonalAssistant",
        model="global.anthropic.claude-haiku-4-5-20251001-v1:0",  # or your preferred model
        system_prompt=f"""You are a helpful personal assistant with web search capabilities.
        
        You can help with:
        - General questions and information lookup
        - Web searches for current information
        - Personal task management
        
        When you need current information, use the websearch function.
        Today's date: {datetime.today().strftime("%Y-%m-%d")}
        Be friendly and professional.""",
        hooks=[MemoryHookProvider(user_session)],
        tools=[websearch],
    )
    return agent


# Create agent
agent = create_personal_agent()
logger.info("✅ Personal agent created with MemorySession and web search")


# #### Congratulations ! Your agent is ready with the MemoryManager & MemorySession
# ## Let's test the Agent


# Test conversation with memory
print("=== First Conversation ===")
print("User: My name is Alex and I'm interested in learning about AI.")
print("Agent: ", end="")
agent("My name is Alex and I'm interested in learning about AI.")


print("User: Can you search for the latest AI trends in 2025?")
print("Agent: ", end="")
agent("Can you search for the latest AI trends in 2025?")


print("User: I'm particularly interested in machine learning applications.")
print("Agent: ", end="")
agent("I'm particularly interested in machine learning applications.")


# ## Test Memory Continuity with MemorySessionManager
#
# To test if our memory system is working correctly, we'll create a new instance of the agent and see if it can access the previously stored information using MemorySessionManager:


# Create new agent instance (simulates user returning)
print("=== User Returns - New Session ===")
new_agent = create_personal_agent()

# Test memory continuity
print("User: What was my name again?")
print("Agent: ", end="")
new_agent("What was my name again?")

print("User: Can you search for more information about machine learning?")
print("Agent: ", end="")
new_agent("Can you search for more information about machine learning?")


# ## View Stored Memory using MemorySession


# Check what's stored in memory using MemorySession
print("=== Memory Contents ===")
recent_turns = user_session.get_last_k_turns(k=3)

for i, turn in enumerate(recent_turns, 1):
    print(f"Turn {i}:")
    for message in turn:
        role = message["role"]
        content = (
            message["content"]["text"][:100] + "..."
            if len(message["content"]["text"]) > 100
            else message["content"]["text"]
        )
        print(f"  {role}: {content}")
    print()


# ## Summary
#
# This tutorial showed how to build a personal agent using both MemorySessionManager and MemorySession. You've learned:
#
# - **MemorySessionManager**: High-level manager for memory operations across multiple sessions
# - **MemorySession**: Session-specific interface that eliminates repetitive parameter passing. Using MemorySession removes the need to pass actor_id/session_id to every method
# - **Type Safety**: Session is bound to specific actor/session at creation time
# - **Better Encapsulation**: Session-specific operations are contained within the session object
# - **Memory Hooks**: Agent hooks can work with the session-based architecture
# - **Conversation Continuity**: Maintaining short-term memory functionality with MemoryManager & MemorySession
#
# ### Key Benefits of MemorySession:
# 1. **Simplified API**: No need to pass actor_id/session_id to every method call
# 2. **Pre-configured Context**: Session is bound to specific actor/session at creation
# 3. **Consistent Interface**: All session operations use the same pre-configured context
#

# ## Cleanup (Optional)


# Uncomment to delete memory resource
# try:
#     memory_client.delete_memory_and_wait(memory_id=memory_id)
#     logger.info(f"✅ Deleted memory: {memory_id}")
# except Exception as e:
#     logger.error(f"Failed to delete memory: {e}")
