#!/usr/bin/env python

# # Strands multi-agent System with AgentCore Memory Tool (Short term Memory)

# ## Introduction
#
# This notebook demonstrates how to implement a **multi-agent system with shared memory** using AWS AgentCore Memory and the Strands framework. While our previous examples focused on single-agent memory, this notebook explores how multiple specialized agents can work together while accessing a common memory store.
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
from datetime import datetime
from strands.hooks import (
    AgentInitializedEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)


# Define the region and the role with the appropiate permissions for Amazon Bedrock models and AgentCore


import os

region = os.getenv("AWS_REGION", "us-west-2")
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("agentcore-memory")


# ## Step 2: Creating Shared Memory
# In this section, we'll create a memory resource that will be shared among our specialized agents.


from bedrock_agentcore.memory import MemoryClient  # noqa: E402


client = MemoryClient(region_name=region)
memory_name = "TravelAgent_STM_%s" % datetime.now().strftime("%Y%m%d%H%M%S")
memory_id = None


from botocore.exceptions import ClientError  # noqa: E402

try:
    print("Creating Memory...")
    memory_name = memory_name

    # Create the memory resource
    memory = client.create_memory_and_wait(
        name=memory_name,  # Unique name for this memory store
        description="Travel Agent STM",  # Human-readable description
        strategies=[],  # No special memory strategies for short-term memory
        event_expiry_days=7,  # Memories expire after 7 days
        max_wait=300,  # Maximum time to wait for memory creation (5 minutes)
        poll_interval=10,  # Check status every 10 seconds
    )

    # Extract and print the memory ID
    memory_id = memory["id"]
    print(f"Memory created successfully with ID: {memory_id}")
except ClientError as e:
    if e.response["Error"]["Code"] == "ValidationException" and "already exists" in str(e):
        # If memory already exists, retrieve its ID
        memories = client.list_memories()
        memory_id = next((m["id"] for m in memories if m["id"].startswith(memory_name)), None)
        logger.info(f"Memory already exists. Using existing memory ID: {memory_id}")
except Exception as e:
    # Handle any errors during memory creation
    print(f"❌ ERROR: {e}")
    import traceback

    traceback.print_exc()

    # Cleanup on error - delete the memory if it was partially created
    if memory_id:
        try:
            client.delete_memory_and_wait(memory_id=memory_id)
            logger.info(f"Cleaned up memory: {memory_id}")
        except Exception as cleanup_error:
            logger.info(f"Failed to clean up memory: {cleanup_error}")


# ### Understanding Shared Memory for Multi-Agent Systems
#
# The memory resource we've created will serve as a shared knowledge base for our travel planning system. All agents will read from and write to this common memory store, enabling:
#
# 1. **Knowledge Consistency**: All agents work with the same information
# 2. **Context Preservation**: Conversation history is maintained across agent transitions
# 3. **Specialized Access**: Each agent will have its own actor_id but share the session_id
#
# This approach allows specialized agents to focus on their domains while still benefiting from the full conversation context.

# ## Step 3: Create Memory Hook Provider
#
# This step defines our custom `MemoryHookProvider` class that automates memory operations. Hooks are special functions that run at specific points in an agent's execution lifecycle. The memory hook we're creating serves two primary functions:
#
# 1. **Retrieve Memories**: Automatically fetches relevant past conversations when a user sends a message
# 2. **Save Memories**: Stores new conversations after the agent responds
#
# This creates a seamless memory experience without manual management.


class ShortTermMemoryHook(HookProvider):
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

            # Get last 5 conversation turns
            recent_turns = self.memory_client.get_last_k_turns(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                k=5,
                branch_name="main",
            )

            if recent_turns:
                # Format conversation history for context
                context_messages = []
                for turn in recent_turns:
                    for message in turn:
                        role = message["role"].lower()
                        content = message["content"]["text"]
                        context_messages.append(f"{role.title()}: {content}")

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
        """Store conversation turns in memory"""
        messages = event.agent.messages
        try:
            # Get session info from agent state
            actor_id = event.agent.state.get("actor_id")
            session_id = event.agent.state.get("session_id")

            if not actor_id or not session_id:
                logger.warning("Missing actor_id or session_id in agent state")
                return

            self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                messages=[(messages[-1]["content"][0]["text"], messages[-1]["role"])],
            )

        except Exception as e:
            logger.error(f"Failed to store message: {e}")

    def register_hooks(self, registry: HookRegistry) -> None:
        # Register memory hooks
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)


# ## Step 4: Create Multi-Agent Architecture with Strands Agents
# In this section, we'll create our multi-agent system with specialized agents for flight and hotel bookings, both sharing access to our memory resource.


# Import the necessary components
from strands import Agent, tool  # noqa: E402


# Create unique actor IDs for each specialized agent but share the session ID
flight_actor_id = f"flight-user-{datetime.now().strftime('%Y%m%d%H%M%S')}"
hotel_actor_id = f"hotel-user-{datetime.now().strftime('%Y%m%d%H%M%S')}"
session_id = f"travel-session-{datetime.now().strftime('%Y%m%d%H%M%S')}"
flight_namespace = f"travel/{flight_actor_id}/preferences/"
hotel_namespace = f"travel/{hotel_actor_id}/preferences/"


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
        flight_memory_hooks = ShortTermMemoryHook(client, memory_id)

        flight_agent = Agent(
            hooks=[flight_memory_hooks],
            model=MODEL_ID,
            system_prompt=FLIGHT_BOOKING_PROMPT,
            state={"actor_id": flight_actor_id, "session_id": session_id},
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
        hotel_memory_hooks = ShortTermMemoryHook(client, memory_id)

        hotel_booking_agent = Agent(
            hooks=[hotel_memory_hooks],
            model=MODEL_ID,
            system_prompt=HOTEL_BOOKING_PROMPT,
            state={"actor_id": hotel_actor_id, "session_id": session_id},
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


# client.delete_memory_and_wait(
#        memory_id = memory_id,
#        max_wait = 300,
#        poll_interval =10
# )
