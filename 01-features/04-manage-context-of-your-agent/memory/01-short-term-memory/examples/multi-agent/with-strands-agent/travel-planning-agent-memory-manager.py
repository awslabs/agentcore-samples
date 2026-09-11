#!/usr/bin/env python

# # Strands multi-agent System with AgentCore Memory Tool (Short term Memory) - Using MemoryManager

# ## Introduction
#
# This notebook demonstrates how to implement a **multi-agent system with shared memory** using AWS AgentCore Memory and the Strands framework. While our previous examples focused on single-agent memory, this notebook explores how multiple specialized agents can work together while accessing a common memory store.
#
# **NOTE: This is the Short Term Memory Sample version using the MemoryManager & MemorySessionManager in place of the original MemoryClient.**
#
# ## Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Short Term Conversational                                                        |
# | Agent usecase       | Travel Planning Assistant                                                        |
# | Agentic Framework   | Strands Agents                                                                   |
# | LLM model           | Anthropic Claude Haiku 4.5                                                   |
# | Tutorial components | AgentCore Short-term Memory, Strands Agents, Memory retrieval via Tool           |
# | Example complexity  | Beginner                                                                         |
#
#
# What you will learn:
#
# - How to set up a shared memory resource that multiple agents can access
# - Creating specialized agents as tools with their own memory access
# - Implementing a coordinator agent that delegates to specialized agents
# - Maintaining conversation context across multiple agent interactions
#
# ### Scenario context
#
# In this example, we'll create a **Travel Planning System** with:
# 1. A Flight Booking Assistant specialized in air travel
# 2. A Hotel Booking Assistant focused on accommodations
# 3. A Travel Coordinator that delegates to these specialized agents
#
# This approach demonstrates how complex domains can be broken down into specialized agents that share memory the same memory store.
#
# ## Architecture
# <div style="text-align:left">
#     <img src="architecture.png" width="65%" />
# </div>
#
# ## Prerequisites
# - Python 3.10+
# - AWS account with appropriate permissions
# - AWS IAM role with appropriate permissions for AgentCore Memory
# - Access to Amazon Bedrock models
#
# Let's get started by setting up our environment and creating our shared memory resource!

# ## Step 1: Environment set up
# Let's begin importing all the necessary libraries and defining the clients to make this notebook work.


# Run: pip install -qr requirements.txt


import logging
import os
from datetime import datetime
from botocore.exceptions import ClientError
from strands.hooks import (
    AgentInitializedEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)

# Import memory management modules
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole
from bedrock_agentcore.memory.session import MemorySession, MemorySessionManager


# Define the region and the role with the appropiate permissions for Amazon Bedrock models and AgentCore


REGION = os.getenv("AWS_REGION", "us-west-2")
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("agentcore-memory")


# ## Step 2: Creating Shared Memory
# In this section, we'll create a memory resource that will be shared among our specialized agents.


memory_client = MemoryClient(region_name=REGION)

try:
    print("Creating Memory...")
    memory_name = "TravelAgent_STM_%s" % datetime.now().strftime("%Y%m%d%H%M%S")

    # Create the memory resource
    memory = memory_client.create_memory_and_wait(
        name=memory_name,
        strategies=[],  # No strategies for short-term memory
        description="Short-term memory for travel agent",
        event_expiry_days=7,  # Retention period for short-term memory
    )

    # Extract and print the memory ID
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


# ### Understanding Shared Memory for Multi-Agent Systems
#
# The memory resource we've created will serve as a shared knowledge base for our travel planning system. All agents will read from and write to this common memory store, enabling:
#
# 1. **Knowledge Consistency**: All agents work with the same information
# 2. **Context Preservation**: Conversation history is maintained across agent transitions
# 3. **Specialized Access**: Each agent will have its own actor_id but share the session_id
#
# This approach allows specialized agents to focus on their domains while still benefiting from the full conversation context.

# ## Step 3: Initialize Session Manager
#
# This section introduces the MemorySessionManager for session-based memory operations and creates a MemorySession to manage actor & session


# Initialize the session memory manager
session_manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)

logger.info(f"✅ Session manager initialized for memory: {memory_id}")
logger.info(f"Session manager type: {type(session_manager)}")


# ## Step 4: Create Memory Hook Provider
#
# This step defines our custom `MemoryHookProvider` class that automates memory operations. Hooks are special functions that run at specific points in an agent's execution lifecycle. The memory hook we're creating serves two primary functions:
#
# 1. **Retrieve Memories**: Automatically fetches relevant past conversations when a user sends a message
# 2. **Save Memories**: Stores new conversations after the agent responds
#
# **KEY CHANGES from MemoryClient version:**
# - Uses MemorySession instead of MemoryClient
# - Uses ConversationalMessage objects instead of tuples
# - Uses add_turns() instead of create_event()
# - Uses MessageRole enum for type safety


class ShortTermMemoryHook(HookProvider):
    def __init__(self, memory_session: MemorySession, memory_id: str):
        self.memory_session = memory_session
        self.memory_id = memory_id

    def on_agent_initialized(self, event: AgentInitializedEvent):
        """Load recent conversation history when agent starts"""
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
                logger.info(f"Context from memory: {context}")

                # Add context to agent's system prompt
                event.agent.system_prompt += f"\n\nRecent conversation history:\n{context}\n\nContinue the conversation naturally based on this context."
                logger.info(f"✅ Loaded {len(recent_turns)} recent conversation turns")
            else:
                logger.info("No previous conversation history found")

        except Exception as e:
            logger.error(f"Failed to load conversation history: {e}")

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

    def register_hooks(self, registry: HookRegistry) -> None:
        # Register memory hooks
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)


# ## Step 5: Create Multi-Agent Architecture with Strands Agents
# In this section, we'll create our multi-agent system with specialized agents for flight and hotel bookings, both sharing access to our memory resource.


# Import the necessary components
from strands import Agent, tool  # noqa: E402


# Create unique actor IDs for each specialized agent but share the session ID
FLIGHT_ACTOR_ID = f"flight-user-{datetime.now().strftime('%Y%m%d%H%M%S')}"
HOTEL_ACTOR_ID = f"hotel-user-{datetime.now().strftime('%Y%m%d%H%M%S')}"
SESSION_ID = f"travel-session-{datetime.now().strftime('%Y%m%d%H%M%S')}"


# ### Creating Specialized Agents with Memory Access
#
# Next, we'll define system prompts for our specialized agents. Each prompt includes the memory parameters in a format that the agent can parse:


# System prompt for the hotel booking specialist
HOTEL_BOOKING_PROMPT = """You are a hotel booking assistant. Help customers find hotels, make reservations, and answer questions about accommodations and amenities. 
Provide clear information about availability, pricing, and booking procedures in a friendly, helpful manner."""

# System prompt for the flight booking specialist
FLIGHT_BOOKING_PROMPT = """You are a flight booking assistant. Help customers find flights, make reservations, and answer questions about airlines, routes, and travel policies. 
Provide clear information about flight availability, pricing, schedules, and booking procedures in a friendly, helpful manner."""


# ### Implementing Agent Tools
# Now we'll implement our specialized agents as tools that can be used by the coordinator agent:


@tool
def flight_booking_assistant(query: str) -> str:
    """
    Process and respond to flight booking queries.

    Args:
        query: A flight-related question about bookings, schedules, airlines, or travel policies

    Returns:
        Detailed flight information, booking options, or travel advice
    """
    try:
        # Create a memory session for the booking assistant
        memory_session = session_manager.create_memory_session(actor_id=FLIGHT_ACTOR_ID, session_id=SESSION_ID)
        flight_memory_hooks = ShortTermMemoryHook(memory_session, memory_id)

        flight_agent = Agent(
            hooks=[flight_memory_hooks],
            model=MODEL_ID,
            system_prompt=FLIGHT_BOOKING_PROMPT,
            state={"actor_id": FLIGHT_ACTOR_ID, "session_id": SESSION_ID},
        )

        response = flight_agent(query)
        return str(response)
    except Exception as e:
        return f"Error in flight booking assistant: {str(e)}"


@tool
def hotel_booking_assistant(query: str) -> str:
    """
    Process and respond to hotel booking queries.

    Args:
        query: A hotel-related question about accommodations, amenities, or reservations

    Returns:
        Detailed hotel information, booking options, or accommodation advice
    """
    try:
        # Create a memory session for the booking assistant
        memory_session = session_manager.create_memory_session(actor_id=HOTEL_ACTOR_ID, session_id=SESSION_ID)

        hotel_memory_hooks = ShortTermMemoryHook(memory_session, memory_id)

        hotel_booking_agent = Agent(
            hooks=[hotel_memory_hooks],
            model=MODEL_ID,
            system_prompt=HOTEL_BOOKING_PROMPT,
            state={"actor_id": HOTEL_ACTOR_ID, "session_id": SESSION_ID},
        )

        response = hotel_booking_agent(query)
        return str(response)
    except Exception as e:
        return f"Error in hotel booking assistant: {str(e)}"


# ### Creating the Coordinator Agent
#
# Finally, we'll create the main travel planning agent that coordinates between these specialized tools:


# System prompt for the coordinator agent
TRAVEL_AGENT_SYSTEM_PROMPT = """
You are a comprehensive travel planning assistant that coordinates between specialized tools:
- For flight-related queries (bookings, schedules, airlines, routes) → Use the flight_booking_assistant tool
- For hotel-related queries (accommodations, amenities, reservations) → Use the hotel_booking_assistant tool
- For complete travel packages → Use both tools as needed to provide comprehensive information
- For general travel advice or simple travel questions → Answer directly

Each agent will have its own memory in case the user asks about historic data.
When handling complex travel requests, coordinate information from both tools to create a cohesive travel plan.
Provide clear organization when presenting information from multiple sources. \
Ask max two questions per turn. Keep the messages short, don't overwhelm the customer.
"""


travel_agent = Agent(
    system_prompt=TRAVEL_AGENT_SYSTEM_PROMPT,
    model=MODEL_ID,
    tools=[flight_booking_assistant, hotel_booking_assistant],
)


# #### Your Multi-Agent System is ready !!
#
# ## Let's test the Agent.
#
# Let's test our multi-agent system with a travel planning scenario:


response = travel_agent("Hello, I would like to book a trip from LA to Madrid. From July 1 to August 2.")


response = travel_agent(
    "I would only like to focus on the flight at the moment. Direct flights, mid-range price, economy class."
)


# ## Testing Memory Persistence
#
# To test if our memory system is working correctly, we'll create a new instance of the travel agent and see if it can access the previously stored information:


# Create a new instance of the travel agent
new_travel_agent = Agent(
    system_prompt=TRAVEL_AGENT_SYSTEM_PROMPT,
    model=MODEL_ID,
    tools=[flight_booking_assistant, hotel_booking_assistant],
)

# Ask about previous conversations
new_travel_agent("Can you remind me about flights talked about before?")


# ## Summary
#
# In this notebook, we've demonstrated:
#
# 1. How to create a shared memory resource for multiple agents
# 2. How to implement specialized agents as tools with memory access
# 3. How to coordinate between multiple agents while maintaining conversation context
# 4. How memory persists across different agent instances
#
# This multi-agent architecture with shared memory provides a powerful approach for building complex conversational AI systems that can handle specialized domains while maintaining a cohesive user experience.

# ## Clean up
# Let's delete the memory to clean up the resources used in this notebook.


# Uncomment to delete memory resource
# try:
#     memory_client.delete_memory_and_wait(memory_id=memory_id)
#     logger.info(f"✅ Deleted memory: {memory_id}")
# except Exception as e:
#     logger.error(f"Failed to delete memory: {e}")


# ## Using the AgentCore CLI
#
# The same memory resources and agent projects demonstrated above can also be
# created and managed with the **AgentCore CLI** (pinned version `0.11.0`).
# This is the recommended developer workflow for iterating quickly.
#
# ### Install the CLI
#
# ```bash
# npm install -g @aws/agentcore@0.11.0
# agentcore --version   # should print 0.11.0
# ```
#
# ### Create a project with memory
#
# ```bash
# # Scaffold a new agent project with short-term + long-term memory
# agentcore create \
#   --name MyMemoryAgent \
#   --framework Strands \
#   --model-provider Bedrock \
#   --memory longAndShortTerm \
#   --defaults
#
# cd MyMemoryAgent
# ```
#
# ### Add memory to an existing project
#
# ```bash
# # Add a memory resource with semantic and user-preference strategies
# agentcore add memory \
#   --name SharedMemory \
#   --strategies SEMANTIC,USER_PREFERENCE \
#   --expiry 30
# ```
#
# ### Deploy to AgentCore Runtime
#
# ```bash
# agentcore deploy
# agentcore status
# ```
#
# ### Invoke the deployed agent
#
# ```bash
# agentcore invoke "Hello, do you remember my name?" --stream
# ```
#
# ### View logs and traces
#
# ```bash
# agentcore logs
# agentcore traces list --limit 10
# ```
#
# ### Clean up
#
# ```bash
# # Remove all deployed resources (runtime + memory)
# agentcore remove all
# ```
#
