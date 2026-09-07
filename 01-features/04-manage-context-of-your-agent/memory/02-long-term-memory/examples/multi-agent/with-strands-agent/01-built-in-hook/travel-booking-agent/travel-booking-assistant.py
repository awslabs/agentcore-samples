#!/usr/bin/env python

# # Strands Agents with AgentCore Memory (Long term memory via Tool)

# ## Introduction
#
# This notebook demonstrates how to implement a **multi-agent system with shared long-term memory** using AgentCore Memory and the Strands framework. We will explore how multiple specialized agents can work together while accessing a common long-term memory store with dedicated namespaces for each agent.
#
# ### Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long term Conversational                                                         |
# | Agent type          | Travel Booking Assistant                                                         |
# | Agentic Framework   | Strands Agents                                                                   |
# | LLM model           | Anthropic Claude Haiku 4.5                                                      |
# | Tutorial components | AgentCore User Preferences Memory Extraction, Tool for storing and retrieving Memory              |
# | Example complexity  | Intermediate                                                                     |
#
# You will learn:
#
# - How to set up a shared memory resource with a long-term memory strategy
# - Creating specialized agents with access to their own memory namespaces
# - Implementing a coordinator agent that delegates to specialized agents
# - Leveraging structured memory namespaces for agent specialization
#
# ## Scenario context
#
# In this example, we'll create a **Travel Planning System** with:
# 1. A Flight Booking Assistant with long-term memory of travel preferences and history
# 2. A Hotel Booking Assistant with long-term memory of accommodation preferences
# 3. A Travel Coordinator that orchestrates these specialized agents
#
# Each specialized agent will access its own namespace within a common memory store, enabling them to build persistent understanding of user preferences over time. This approach demonstrates how complex domains can be broken down into specialized agents that share a memory infrastructure but maintain their own areas of expertise.
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
# - AWS credentials with Amazon Bedrock AgentCore Memory permissions
# - Amazon Bedrock AgentCore SDK
#
# Let's get started by setting up our environment and creating our shared long-term memory resource!

# ## Step 1: Environment set up
# Let's begin importing all the necessary libraries and defining the clients to make this notebook work.


# Run: pip install -qr requirements.txt


import logging
import time
from datetime import datetime
from strands.hooks import AfterInvocationEvent, HookProvider, HookRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("travel-assistant")


# Define the region with the appropiate permissions for Amazon Bedrock models and AgentCore


region = "us-west-2"  # Replace with your AWS region
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


# ## Step 2: Creating Shared Memory Resource
# In this section, we'll create common long-term memory store with dedicated namespaces for each agent.


from bedrock_agentcore.memory import MemoryClient  # noqa: E402
from bedrock_agentcore.memory.constants import StrategyType  # noqa: E402

client = MemoryClient(region_name=region)
memory_name = "TravelBookingMemory"


print("Creating or retrieving Memory with Long-Term Strategy...")
memory = client.create_or_get_memory(
    name=memory_name,
    description="Travel Agent with Long-Term Memory",
    strategies=[
        {
            StrategyType.USER_PREFERENCE.value: {
                "name": "UserPreferences",
                "description": "Captures user preferences",
                "namespaceTemplates": ["/travel/{actorId}/preferences/"],
            }
        }
    ],
    event_expiry_days=7,  # Short-term conversation expires after 7 days
)

memory_id = memory["id"]
print(f"✅ Memory ready: {memory_id}")


# ### Understanding Long-Term Memory Strategy
#
# The memory resource we're creating uses AgentCore Memory's long-term memory capabilities with a single user preference strategy:
#
# 1. **User Preference Memory Strategy**: Automatically extracts and consolidates user preferences mentioned in conversations
# 2. **Actor-based Namespaces**: Uses sub-namespaces per agent domain (flight/hotel) under the same user actorId
# 3. **Memory Persistence**: Unlike short-term memory that expires, extracted preferences persist beyond conversation expiry
#
# With the namespace pattern `travel/{actorId}/flight/preferences/ and travel/{actorId}/hotel/preferences/`, each specialized agent will have its own unique namespace based on its actor ID:
# - Flight agent will access: `travel/flight-user-TIMESTAMP/preferences/`
# - Hotel agent will access: `travel/hotel-user-TIMESTAMP/preferences/`
#
# This allows each agent to maintain its own specialized knowledge while using a common memory infrastructure.
#
# ### Setting Up Agent Identities


# actorId represents the USER identity - consistent across sessions for memory persistence
user_actor_id = "user-001"
session_id = f"travel-session-{datetime.now().strftime('%Y%m%d%H%M%S')}"

# Both agents share the same namespace - semantic search differentiates flight vs hotel preferences
flight_actor_id = user_actor_id
hotel_actor_id = user_actor_id
flight_namespace = f"travel/{user_actor_id}/preferences/"
hotel_namespace = f"travel/{user_actor_id}/preferences/"


# Import the necessary components
from strands import Agent, tool  # noqa: E402
from strands_tools.agent_core_memory import AgentCoreMemoryToolProvider  # noqa: E402


# ### Step 3: Create Memory Hook Provider
#
# This step defines our custom `MemoryHookProvider` class that automates memory operations. Hooks are special functions that run at specific points in an agent's execution lifecycle. The memory hook we're creating serves one primary function:
#
# 1. **Save Memories**: Stores new conversations after the agent responds
#
# This creates a seamless memory experience without manual management.


class MemoryHookProvider(HookProvider):
    """Hook provider for automatic memory management"""

    def __init__(self, memory_id: str, client: MemoryClient):
        self.memory_id = memory_id
        self.client = client

    def save_memories(self, event: AfterInvocationEvent):
        """Save conversation after agent response"""
        try:
            messages = event.agent.messages
            if len(messages) >= 2:
                # Get last user and assistant messages
                user_msg = None
                assistant_msg = None

                for msg in reversed(messages):
                    if msg["role"] == "assistant" and not assistant_msg:
                        assistant_msg = msg["content"][0]["text"]
                    elif msg["role"] == "user" and not user_msg and "toolResult" not in msg["content"][0]:
                        user_msg = msg["content"][0]["text"]
                        break

                if user_msg and assistant_msg:
                    # Get session info from agent state
                    actor_id = event.agent.state.get("actor_id")
                    session_id = event.agent.state.get("session_id")

                    if not actor_id or not session_id:
                        logger.warning("Missing actor_id or session_id in agent state")
                        return

                    # Save conversation
                    self.client.create_event(
                        memory_id=self.memory_id,
                        actor_id=actor_id,
                        session_id=session_id,
                        messages=[(user_msg, "USER"), (assistant_msg, "ASSISTANT")],
                    )
                    logger.info("Saved conversation to memory")

        except Exception as e:
            logger.error(f"Failed to save memories: {e}")

    def register_hooks(self, registry: HookRegistry) -> None:
        """Register memory hooks"""
        registry.add_callback(AfterInvocationEvent, self.save_memories)
        logger.info("Memory hooks registered")


# ### Creating Specialized Agents with Memory Access
#
# Next, we'll define system prompts for our specialized agents.


# System prompt for the hotel booking specialist
HOTEL_BOOKING_PROMPT = """You are a hotel booking assistant. Help customers find hotels, make reservations, and answer questions about accommodations and amenities. 
Provide clear information about availability, pricing, and booking procedures in a friendly, helpful manner.Keep the messages short, don't overwhelm the customer."""

# System prompt for the flight booking specialist
FLIGHT_BOOKING_PROMPT = """You are a flight booking assistant. Help customers find flights, make reservations, and answer questions about airlines, routes, and travel policies. 
Provide clear information about flight availability, pricing, schedules, and booking procedures in a friendly, helpful manner.Keep the messages short, don't overwhelm the customer."""


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
        provider_flight = AgentCoreMemoryToolProvider(
            memory_id=memory_id,  # Required
            actor_id=flight_actor_id,  # Required
            session_id=session_id,  # Required
            region=region,
            namespace=flight_namespace,
        )

        flight_memory_hooks = MemoryHookProvider(memory_id, client)

        flight_agent = Agent(
            tools=provider_flight.tools,
            hooks=[flight_memory_hooks],
            model=MODEL_ID,
            system_prompt=FLIGHT_BOOKING_PROMPT,
            state={"actor_id": flight_actor_id, "session_id": session_id},
        )

        # Call the agent and return its response
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
        provider_hotel = AgentCoreMemoryToolProvider(
            memory_id=memory_id,
            actor_id=hotel_actor_id,
            session_id=session_id,
            region=region,
            namespace=hotel_namespace,
        )

        hotel_memory_hooks = MemoryHookProvider(memory_id, client)

        hotel_booking_agent = Agent(
            tools=provider_hotel.tools,
            hooks=[hotel_memory_hooks],
            model=MODEL_ID,
            system_prompt=HOTEL_BOOKING_PROMPT,
            state={"actor_id": hotel_actor_id, "session_id": session_id},
        )

        # Call the agent and return its response
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
Keep the messages short, don't overwhelm the customer.
"""


travel_agent = Agent(
    system_prompt=TRAVEL_AGENT_SYSTEM_PROMPT,
    model=MODEL_ID,
    tools=[flight_booking_assistant, hotel_booking_assistant],
)


# ## Testing the Multi-Agent Memory System
#
# Let's test our multi-agent system with a travel planning scenario:

# ### Optional: Hydrate the long term memory of the flight booking assistant
# Uncomment the following cell if you want to hydrate the flight booking agent.


"""flight_previous_messages = [
    ("Hi, I'm Sarah", "USER"),
    ("Hello Sarah! Welcome to FlightAssist. How can I help you with your travel plans today?", "ASSISTANT"),
    ("I'm looking to book a flight from New York to London sometime next month.", "USER"),
    ("I'd be happy to help you find flights from New York to London for next month. Do you have specific dates in mind, or are you flexible?", "ASSISTANT"),
    ("I'm thinking around the 15th to the 25th, but I can be a bit flexible.", "USER"),
    ("Great! That gives us some room to find the best options. Do you have any preferences regarding airlines or flight times?", "ASSISTANT"),
    ("I definitely prefer direct flights if possible. I really don't like layovers.", "USER"),
    ("I completely understand your preference for direct flights. Layovers can be inconvenient. Fortunately, there are several airlines offering direct flights between New York and London, including British Airways, American Airlines, Delta, and Virgin Atlantic.", "ASSISTANT"),
    ("That's good to hear. I've had good experiences with British Airways in the past.", "USER"),
    ("British Airways does offer excellent service on transatlantic routes. I'll keep that in mind when searching for options. Do you have any seating preferences or other requirements for your flight?", "ASSISTANT"),
    ("I always try to get an aisle seat. I like being able to get up without disturbing others, especially on long flights.", "USER"),
    ("An aisle seat is a great choice for long-haul flights like New York to London. I'll note your preference for aisle seating. Would you prefer to fly in the morning, afternoon, or evening?", "ASSISTANT"),
    ("I prefer overnight flights for long journeys. It helps me adjust to the time difference better.", "USER"),
    ("Overnight flights are indeed a smart choice for eastbound transatlantic travel. They allow you to arrive in London in the morning and help minimize jet lag. British Airways, Delta, and American all offer evening departures from New York that arrive in London the next morning.", "ASSISTANT"),
    ("Perfect! And I'm also wondering about baggage allowances since I'll be staying for about a week.", "USER"),
    ("For a week-long trip, most travelers find that a standard checked bag plus a carry-on is sufficient. British Airways typically allows one free checked bag on transatlantic flights in economy class, plus a carry-on and personal item. Would you like me to check the specific allowances for your preferred dates?", "ASSISTANT")
]

print("\nHydrating memories with previous conversations...")

# Save the conversation history to short-term memory
initial = client.create_event(
    memory_id=memory_id,
    actor_id=flight_actor_id,
    session_id=session_id,
    messages=flight_previous_messages,
)
print("✓ Conversation saved in short term memory")"""


travel_agent("Hello, I would like to book a trip from LA to Madrid. From July 1 to August 2.")


travel_agent("I prefer direct flights with Iberia")


travel_agent("I would like a flight in the morning, in economy")


travel_agent("I would like to fly from SNA, and return 15 days later")


# ## Testing Memory Persistence
#
# To test if our memory system is working correctly, we'll create a new instance of the travel agent and see if it can access the previously stored information:


time.sleep(60)  # Let's give the memory some time to process the events..
# Create a new instance of the travel agent
new_travel_agent = Agent(
    system_prompt=TRAVEL_AGENT_SYSTEM_PROMPT,
    model=MODEL_ID,
    tools=[flight_booking_assistant, hotel_booking_assistant],
)

# Ask about previous conversations
new_travel_agent("Can you remind me about flight preferences?")


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
