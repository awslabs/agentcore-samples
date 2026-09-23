#!/usr/bin/env python

# # Strands Agents with AgentCore Memory (Short-Term Memory)
#
#
# ## Introduction
#
# This tutorial demonstrates how to build a **personal agent** using Strands agents with AgentCore **short-term memory** (Raw events). The agent remembers recent conversations in the session using `get_last_k_turns` and can continue conversations seamlessly when user returns.
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
# | Tutorial components | AgentCore Short-term Memory, AgentInitializedEvent and MessageAddedEvent hooks   |
# | Example complexity  | Beginner                                                                         |
#
# You'll learn to:
# - Use short-term memory for conversation continuity
# - Retrieve last K conversation turns
# - Web search tool for real-time information
# - Initialize agents with conversation history
#
# ## Architecture
# <div style="text-align:left">
#     <img src="architecture.png" width="65%" />
# </div>
#
# ## Prerequisites
#
# - Python 3.10+
# - AWS credentials with AgentCore Memory permissions
# - AgentCore Memory role ARN
# - Access to Amazon Bedrock models
#
# Let's get started by setting up our environment!

# ## Step 1: Setup and Imports


# Run: pip install -qr requirements.txt


import logging
from datetime import datetime

# Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("personal-agent")


# Imports
import os  # noqa: E402
from strands import Agent, tool  # noqa: E402
from strands.hooks import (  # noqa: E402
    AgentInitializedEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)
from bedrock_agentcore.memory import MemoryClient  # noqa: E402

# Configuration
REGION = os.getenv("AWS_REGION", "us-west-2")  # AWS region for the agent
ACTOR_ID = "user_123"  # It can be any unique identifier (AgentID, User ID, etc.)
SESSION_ID = "personal_session_001"  # Unique session identifier


# ## Step 2: Web Search Tool
#
# First, let's create a simple web search tool for the agent.


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


# ## Step 3: Create Memory Resource
# For short-term memory, we create a memory resource without any strategies. This stores raw conversation turns that can be retrieved with `get_last_k_turns`.
#


# Initialize Memory Client
client = MemoryClient(region_name=REGION)
memory_name = "PersonalAgentMemory"

# Initialize before the try so the error handler can reference it safely even if
# creation fails before assignment (otherwise `if memory_id` raises NameError).
memory_id = None
try:
    # create_or_get_memory is idempotent: creates the short-term memory (waiting for
    # ACTIVE) or returns the existing one on a re-run, replacing the manual
    # create/except/list-and-match fallback.
    memory = client.create_or_get_memory(
        name=memory_name,
        strategies=[],  # No strategies for short-term memory
        description="Short-term memory for personal agent",
        event_expiry_days=7,  # Retention period for short-term memory. This can be upto 365 days.
    )
    memory_id = memory["id"]
    logger.info(f"✅ Memory ready: {memory_id}")
except Exception as e:
    # Show any errors during memory creation
    logger.error(f"❌ ERROR: {e}")
    import traceback

    traceback.print_exc()
    # Cleanup on error - delete the memory if it was partially created
    if memory_id:
        try:
            client.delete_memory_and_wait(memory_id=memory_id)
            logger.info(f"Cleaned up memory: {memory_id}")
        except Exception as cleanup_error:
            logger.error(f"Failed to clean up memory: {cleanup_error}")


# ## Step 4: Memory Hook
#
# This step defines our custom `MemoryHookProvider` class that automates memory operations. Hooks are special functions that run at specific points in an agent's execution lifecycle. The memory hook we're creating serves two primary functions:
# 1. **To load recent conversation**: We use the `AgentInitializedEvent` hook will automatically load recent conversation history when the agent is initialized.
# 2. **To store the last message**: Stores new conversational message.
#
# This creates a seamless memory experience without manual management.


class MemoryHookProvider(HookProvider):
    def __init__(self, memory_client: MemoryClient, memory_id: str):
        self.memory_client = memory_client
        self.memory_id = memory_id

    def on_agent_initialized(self, event: AgentInitializedEvent):
        """Load recent conversation history when agent starts"""
        try:
            # Get session info from agent state
            actor_id = event.agent.state.get("actor_id")
            session_id = event.agent.state.get("session_id")

            if not actor_id or not session_id:
                logger.warning("Missing actor_id or session_id in agent state")
                return

            # Load the last 5 conversation turns from memory
            recent_turns = self.memory_client.get_last_k_turns(
                memory_id=self.memory_id, actor_id=actor_id, session_id=session_id, k=5
            )

            if recent_turns:
                # Format conversation history for context
                context_messages = []
                for turn in recent_turns:
                    for message in turn:
                        role = message["role"]
                        content = message["content"]["text"]
                        context_messages.append(f"{role}: {content}")

                context = "\n".join(context_messages)
                # Add context to agent's system prompt.
                event.agent.system_prompt += f"\n\nRecent conversation:\n{context}"
                logger.info(f"✅ Loaded {len(recent_turns)} conversation turns")

        except Exception as e:
            logger.error(f"Memory load error: {e}")

    def on_message_added(self, event: MessageAddedEvent):
        """Store messages in memory"""
        messages = event.agent.messages
        try:
            # Get session info from agent state
            actor_id = event.agent.state.get("actor_id")
            session_id = event.agent.state.get("session_id")

            if messages[-1]["content"][0].get("text"):
                self.memory_client.create_event(
                    memory_id=self.memory_id,
                    actor_id=actor_id,
                    session_id=session_id,
                    messages=[(messages[-1]["content"][0]["text"], messages[-1]["role"])],
                )
        except Exception as e:
            logger.error(f"Memory save error: {e}")

    def register_hooks(self, registry: HookRegistry):
        # Register memory hooks
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)


# ## Step 5: Create Personal Agent with Web Search


def create_personal_agent():
    """Create personal agent with memory and web search"""
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
        hooks=[MemoryHookProvider(client, memory_id)],
        tools=[websearch],
        state={"actor_id": ACTOR_ID, "session_id": SESSION_ID},
    )
    return agent


# Create agent
agent = create_personal_agent()
logger.info("✅ Personal agent created with memory and web search")


# #### Congratulations ! Your agent is ready ! :)
# ## Lets test the Agent


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


# ## Test Memory Continuity
#
# To test if our memory system is working correctly, we'll create a new instance of the agent and see if it can access the previously stored information:


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


# ## View Stored Memory


# Check what's stored in memory
print("=== Memory Contents ===")
recent_turns = client.get_last_k_turns(
    memory_id=memory_id,
    actor_id=ACTOR_ID,
    session_id=SESSION_ID,
    k=3,  # Adjust k to see more or fewer turns
)

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
# This tutorial showed how to build a personal agent. You've learned:
#
# - Creating memory resources without strategies
# - Using `get_last_k_turns` for conversation history
# - Adding web search capabilities to agents
# - Implementing memory hooks for context loading
#
# **Next Steps:**
# - Add more sophisticated tools
# - Implement long-term memory strategies
# - Enhance search capabilities with multiple sources

# ## Cleanup (Optional)


# Uncomment to delete memory resource
# client.delete_memory_and_wait(memory_id)
# logger.info(f"✅ Deleted memory: {memory_id}")
