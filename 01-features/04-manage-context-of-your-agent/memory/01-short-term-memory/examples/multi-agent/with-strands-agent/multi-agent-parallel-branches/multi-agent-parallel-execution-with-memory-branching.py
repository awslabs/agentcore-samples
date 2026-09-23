#!/usr/bin/env python

# # Strands multi-agent System with parallel execution using AgentCore Memory Branching

# ## Introduction
#
# This notebook demonstrates **AgentCore Memory Branching** - a powerful capability that enables multiple specialized agents to maintain isolated memory contexts while sharing a common memory resource. This is essential for multi-agent systems, especially those using parallel execution patterns like Strands Agent Graphs.
#
# ## Why Memory Branching Matters
#
# In multi-agent systems, different agents often need to:
# - **Maintain separate conversation contexts** - Each agent focuses on its domain without interference
# - **Execute in parallel** - Multiple agents can work simultaneously without memory conflicts
# - **Share a common session** - All agents contribute to the same user session while keeping their contexts isolated
# - **Access relevant history** - Agents can retrieve their own past interactions without mixing contexts
#
# AgentCore Memory Branching solves these challenges by allowing multiple conversation branches within a single memory session, similar to Git branches for code.
#
# ## Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Multi-Agent with Memory Branching                                                |
# | Agent usecase       | Travel Planning Assistant                                                        |
# | Agentic Framework   | Strands Agent Graph (supports parallel execution)                                |
# | LLM model           | Anthropic Claude Haiku 4.5                                                      |
# | Tutorial components | AgentCore Memory Branching, Strands Multi-Agent Graph, Parallel Execution        |
# | Example complexity  | Intermediate                                                                     |
#
#
# What you will learn:
#
# - How to create and manage memory branches for different agents
# - Implementing isolated memory contexts in a multi-agent architecture
# - Building agent graphs with Strands that support parallel execution
# - How branching enables safe concurrent memory access
# - Viewing and inspecting branch-specific conversation history
#
# ### Scenario context
#
# We'll build a **Travel Planning System** with three agents, each with its own memory branch:
# 1. **Travel Coordinator** (main branch) - Orchestrates the overall travel planning
# 2. **Flight Booking Assistant** (flight_agent_memory branch) - Handles air travel queries
# 3. **Hotel Booking Assistant** (hotel_agent_memory branch) - Manages accommodation requests
#
# The coordinator can delegate to specialized agents that execute in parallel, with each maintaining its own conversation history through memory branching.
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
# Let's get started by setting up our environment and creating our shared memory resource with branching support!

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


# ## Step 2: Creating Shared Memory with Branching Support
#
# We'll create a single memory resource that will support multiple branches - one for each agent. This shared memory resource acts as the foundation, while branches provide isolated contexts for each agent's conversations.
#
# Think of it like a Git repository: one repository (memory resource) with multiple branches (agent contexts).


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


# ### Understanding Memory Branching for Multi-Agent Systems
#
# The memory resource we've created supports **branching** - a critical feature for multi-agent architectures. Here's how it works:
#
# **Single Memory Resource, Multiple Branches:**
# - All agents share the same `memory_id` and `session_id`
# - Each agent gets its own `branch_name` for isolated context
#
# **Key Benefits for Multi-Agent Systems:**
#
# 1. **Context Isolation**: Each agent maintains its own conversation history without interference
#    - Flight agent only sees flight-related conversations
#    - Hotel agent only sees hotel-related conversations
#    - Coordinator sees the main orchestration flow
#
# 2. **Parallel Execution Safety**: Multiple agents can execute simultaneously
#    - No memory conflicts when agents run in parallel
#    - Each branch is independently accessible
#    - Critical for Strands Agent Graphs that support concurrent execution
#
# 3. **Clear Audit Trail**: Each agent's interactions are traceable
#    - Inspect what each agent discussed
#    - Debug agent-specific issues
#    - Understand the flow of multi-agent conversations

# ## Step 3: Create Memory Hook Provider with Branch Support
#
# The `ShortTermMemoryHook` class implements branch-aware memory management. This is the key component that enables memory branching in our multi-agent system.
#
# **Key Features:**
#
# 1. **Branch Initialization**: Automatically creates branches for each agent
#    - Main branch for the coordinator agent
#    - Specialized branches (e.g., `flight_agent_memory`, `hotel_agent_memory`) for sub-agents
#    - Branches are forked from the main conversation timeline
#
# 2. **Branch-Specific Memory Retrieval**: Each agent loads only its own context
#    - `on_agent_initialized()` fetches conversation history from the agent's branch
#    - Prevents context pollution between agents
#    - Enables agents to maintain focused, domain-specific conversations
#
# 3. **Branch-Specific Memory Storage**: Conversations are saved to the correct branch
#    - `on_message_added()` stores messages in the agent's designated branch
#    - Supports concurrent writes from parallel agent execution
#    - No race conditions or memory conflicts
#
# This hook provider is what makes parallel agent execution safe and efficient with AgentCore Memory.


from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole  # noqa: E402
from bedrock_agentcore.memory import MemorySessionManager  # noqa: E402


class ShortTermMemoryHook(HookProvider):
    def __init__(self, memory_id: str, region_name: str = "us-west-2", branch_name: str = "main"):
        """Initialize the hook with a MemorySessionManager.

        Args:
            memory_id: The AgentCore Memory ID
            region_name: AWS region for the memory service
            branch_name: Branch name for this agent's memory (default: "main")
        """
        self.memory_manager = MemorySessionManager(memory_id=memory_id, region_name=region_name)
        self.memory_id = memory_id
        self.branch_name = branch_name
        self._sessions = {}  # Cache session objects per actor/session combo
        self._branch_initialized = False  # Track if branch has been created

    def _get_or_create_session(self, actor_id: str, session_id: str):
        """Get or create a MemorySession for the given actor/session.

        Args:
            actor_id: The actor identifier
            session_id: The session identifier

        Returns:
            MemorySession object
        """
        key = f"{actor_id}:{session_id}"
        if key not in self._sessions:
            self._sessions[key] = self.memory_manager.create_memory_session(actor_id=actor_id, session_id=session_id)
        return self._sessions[key]

    def _initialize_branch(self, actor_id: str, session_id: str):
        """Initialize a branch if it doesn't exist and this is not the main branch.

        Args:
            actor_id: The actor identifier
            session_id: The session identifier
        """
        if self._branch_initialized or self.branch_name == "main":
            return

        try:
            memory_session = self._get_or_create_session(actor_id, session_id)

            # Check if branch already exists
            branches = memory_session.list_branches()
            branch_exists = any(b.name == self.branch_name for b in branches)

            if not branch_exists:
                # Get the last event from main branch to fork from
                main_events = memory_session.list_events(branch_name="main")
                if main_events:
                    last_event = main_events[-1]
                    # Create the branch with an initial message
                    memory_session.fork_conversation(
                        root_event_id=last_event.eventId,
                        branch_name=self.branch_name,
                        messages=[
                            ConversationalMessage(
                                f"Starting {self.branch_name} branch",
                                MessageRole.ASSISTANT,
                            )
                        ],
                    )
                    logger.info(f"✅ Created branch: {self.branch_name}")

            self._branch_initialized = True

        except Exception as e:
            logger.error(f"Failed to initialize branch {self.branch_name}: {e}", exc_info=True)

    def on_agent_initialized(self, event: AgentInitializedEvent):
        """Load recent conversation history when agent starts"""
        try:
            # Get session info from agent state
            actor_id = event.agent.state.get("actor_id")
            session_id = event.agent.state.get("session_id")

            if not actor_id or not session_id:
                logger.warning("Missing actor_id or session_id in agent state")
                return

            # Get the memory session
            memory_session = self._get_or_create_session(actor_id, session_id)

            # For non-main branches, initialize if there are events in main branch
            if self.branch_name != "main":
                try:
                    main_events = memory_session.list_events(branch_name="main")
                    if len(main_events) > 0:
                        self._initialize_branch(actor_id, session_id)
                except Exception as e:
                    # Main branch might not exist yet on first call
                    logger.info(f"Main branch not found yet, will initialize {self.branch_name} branch later: {e}")

            # Check if the branch exists before trying to get turns
            branches = memory_session.list_branches()
            branch_exists = any(b.name == self.branch_name for b in branches)

            recent_turns = []
            if branch_exists:
                # Only fetch turns if branch exists
                recent_turns = memory_session.get_last_k_turns(k=5, branch_name=self.branch_name)
            else:
                logger.info(f"Branch '{self.branch_name}' does not exist yet, skipping turn retrieval")

            if len(recent_turns) > 0:
                # Format conversation history for context
                context_messages = []
                for turn in recent_turns:
                    for message in turn:
                        role = message.get("role", "unknown").lower()
                        text = message.get("content", {}).get("text", "")
                        if text:
                            context_messages.append(f"{role.title()}: {text}")

                if context_messages:
                    context = "\n".join(context_messages)
                    logger.info(f"Loaded context from branch '{self.branch_name}' ({len(context_messages)} messages)")

                    # Add context to agent's system prompt
                    event.agent.system_prompt += (
                        f"\n\nRecent conversation history (from {self.branch_name}):\n{context}\n\n"
                        "Continue the conversation naturally based on this context."
                    )

                    logger.info(
                        f"✅ Loaded {len(recent_turns)} recent conversation turns from branch '{self.branch_name}'"
                    )
            else:
                logger.info(f"No previous conversation history found in branch '{self.branch_name}'")

        except Exception as e:
            logger.error(f"Failed to load conversation history: {e}", exc_info=True)

    def on_message_added(self, event: MessageAddedEvent):
        """Store conversation turns in memory on the appropriate branch"""
        try:
            # Get session info from agent state
            actor_id = event.agent.state.get("actor_id")
            session_id = event.agent.state.get("session_id")

            if not actor_id or not session_id:
                logger.warning("Missing actor_id or session_id in agent state")
                return

            # Get the memory session
            memory_session = self._get_or_create_session(actor_id, session_id)

            # Get the last message
            messages = event.agent.messages
            if not messages:
                return

            last_message = messages[-1]
            role_str = last_message.get("role", "").upper()
            content_text = last_message.get("content", [{}])[0].get("text", "")

            if not content_text:
                logger.debug("Skipping empty message")
                return

            # Map role string to MessageRole enum
            role_mapping = {
                "USER": MessageRole.USER,
                "ASSISTANT": MessageRole.ASSISTANT,
                "TOOL": MessageRole.TOOL,
            }
            message_role = role_mapping.get(role_str, MessageRole.USER)

            # Store the message on the appropriate branch
            if self.branch_name == "main":
                # Main branch - just add turns normally
                memory_session.add_turns(messages=[ConversationalMessage(content_text, message_role)])
            else:
                # Non-main branch - need to append to existing branch
                # Initialize branch if it doesn't exist
                if not self._branch_initialized:
                    self._initialize_branch(actor_id, session_id)

                # Get the latest event from this branch
                branch_events = memory_session.list_events(branch_name=self.branch_name)
                if branch_events:
                    # Add to existing branch by specifying branch name (without rootEventId)
                    memory_session.add_turns(
                        messages=[ConversationalMessage(content_text, message_role)],
                        branch={"name": self.branch_name},
                    )
                else:
                    # This shouldn't happen if _initialize_branch worked, but handle it
                    logger.warning(f"Branch {self.branch_name} not found after initialization")
                    self._initialize_branch(actor_id, session_id)

            logger.debug(f"✅ Stored message in branch '{self.branch_name}': {role_str}")

        except Exception as e:
            logger.error(f"Failed to store message: {e}", exc_info=True)

    def create_branch(
        self,
        actor_id: str,
        session_id: str,
        root_event_id: str,
        branch_name: str,
        messages: list,
    ):
        """Create a new conversation branch.

        Args:
            actor_id: The actor identifier
            session_id: The session identifier
            root_event_id: Event ID to branch from
            branch_name: Name for the new branch
            messages: List of ConversationalMessage objects to add to the branch
        """
        memory_session = self._get_or_create_session(actor_id, session_id)
        return memory_session.fork_conversation(root_event_id=root_event_id, branch_name=branch_name, messages=messages)

    def list_branches(self, actor_id: str, session_id: str):
        """List all branches for a session.

        Args:
            actor_id: The actor identifier
            session_id: The session identifier

        Returns:
            List of branch information
        """
        memory_session = self._get_or_create_session(actor_id, session_id)
        return memory_session.list_branches()

    def get_session(self, actor_id: str, session_id: str):
        """Get the memory session object for direct access.

        Args:
            actor_id: The actor identifier
            session_id: The session identifier

        Returns:
            MemorySession object
        """
        return self._get_or_create_session(actor_id, session_id)

    def register_hooks(self, registry: HookRegistry) -> None:
        """Register memory hooks with the registry.

        Args:
            registry: The HookRegistry to register callbacks with
        """
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)


# ## Step 4: Create Multi-Agent Architecture with Strands Agent Graph
#
# Now we'll build our multi-agent system using **Strands Agent Graph** - a framework that supports parallel agent execution. Each agent will be configured with its own memory branch, enabling safe concurrent operation.
#
# **Architecture Overview:**
# - **Coordinator Agent** → Uses `main` branch
# - **Flight Agent** → Uses `flight_agent_memory` branch
# - **Hotel Agent** → Uses `hotel_agent_memory` branch
#
# All agents share the same `session_id` but maintain isolated conversation contexts through branching.


# Import the necessary components
from strands import Agent  # noqa: E402


# Create unique actor IDs for each specialized agent but share the session ID
actor_id = f"travel-user-{datetime.now().strftime('%Y%m%d%H%M%S')}"
session_id = f"travel-session-{datetime.now().strftime('%Y%m%d%H%M%S')}"
namespace = f"travel/{actor_id}/preferences/"


# ### Creating Specialized Agents with Branch-Specific Memory
#
# We'll define system prompts for our specialized agents. Each agent will be configured with its own memory branch, ensuring conversation isolation and enabling parallel execution.


# System prompt for the hotel booking specialist
HOTEL_BOOKING_PROMPT = """You are a hotel booking assistant. Help customers find hotels, make reservations, and answer questions about accommodations and amenities. 
Provide clear information about availability, pricing, and booking procedures in a friendly, helpful manner."""

# System prompt for the flight booking specialist
FLIGHT_BOOKING_PROMPT = """You are a flight booking assistant. Help customers find flights, make reservations, and answer questions about airlines, routes, and travel policies. 
Provide clear information about flight availability, pricing, schedules, and booking procedures in a friendly, helpful manner."""


flight_memory_hooks = None
hotel_memory_hooks = None


# ### Implementing Agents with Memory Branching
#
# Each specialized agent is configured with:
# - A unique `branch_name` for isolated memory context
# - The same `memory_id` and `session_id` for shared session management
# - A `ShortTermMemoryHook` that handles branch-specific operations
#
# **Key Implementation Details:**
# - `flight_booking_agent()` uses branch: `flight_agent_memory`
# - `hotel_booking_agent()` uses branch: `hotel_agent_memory`


def flight_booking_agent() -> Agent:
    global flight_memory_hooks
    try:
        if flight_memory_hooks is None:
            # Create hook with branch name "flight_agent_memory"
            flight_memory_hooks = ShortTermMemoryHook(
                memory_id=memory_id,
                region_name=region,
                branch_name="flight_agent_memory",
            )

        flight_agent = Agent(
            hooks=[flight_memory_hooks],
            model=MODEL_ID,
            system_prompt=FLIGHT_BOOKING_PROMPT,
            state={"actor_id": actor_id, "session_id": session_id},
        )

        return flight_agent
    except Exception as e:
        return f"Error in flight booking assistant: {str(e)}"


def hotel_booking_agent() -> Agent:
    global hotel_memory_hooks
    try:
        if hotel_memory_hooks is None:
            # Create hook with branch name "hotel_agent_memory"
            hotel_memory_hooks = ShortTermMemoryHook(
                memory_id=memory_id,
                region_name=region,
                branch_name="hotel_agent_memory",
            )

        hotel_booking_agent = Agent(
            hooks=[hotel_memory_hooks],
            model=MODEL_ID,
            system_prompt=HOTEL_BOOKING_PROMPT,
            state={"actor_id": actor_id, "session_id": session_id},
        )

        return hotel_booking_agent
    except Exception as e:
        return f"Error in hotel booking assistant: {str(e)}"


# ### Creating the Coordinator Agent
#
# The coordinator agent uses the `main` branch (default) and orchestrates the specialized agents. It can delegate tasks to flight and hotel agents, which may execute in parallel when using the Strands Agent Graph.


# System prompt for the coordinator agent
TRAVEL_AGENT_SYSTEM_PROMPT = """
You are a comprehensive travel planning assistant that coordinates between specialized tools:
- For flight-related queries (bookings, schedules, airlines, routes) → Use the flight_booking_agent
- For hotel-related queries (accommodations, amenities, reservations) → Use the hotel_booking_agent
- For complete travel packages → Use both tools as needed to provide comprehensive information
- For general travel advice or simple travel questions → Answer directly

Each agent will have its own memory in case the user asks about historic data.
When handling complex travel requests, coordinate information from both tools to create a cohesive travel plan.
Provide clear organization when presenting information from multiple sources. \
Ask max two questions per turn. Keep the messages short, don't overwhelm the customer.
"""


def travel_booking_agent() -> Agent:
    agent_memory_hooks = ShortTermMemoryHook(
        memory_id=memory_id,
        region_name=region,
    )
    travel_agent = Agent(
        system_prompt=TRAVEL_AGENT_SYSTEM_PROMPT,
        hooks=[agent_memory_hooks],
        model=MODEL_ID,
        state={"actor_id": actor_id, "session_id": session_id},
    )

    return travel_agent


# ### Building the Agent Graph with Parallel Execution Support
#
# Now we'll assemble our agents into a **Strands Agent Graph**. This graph structure enables:
#
# **Parallel Execution:**
# - When the coordinator needs both flight and hotel information, both agents can execute simultaneously
# - Memory branching prevents conflicts during concurrent execution
# - Each agent reads/writes to its own branch independently
#
# **Memory Branch Mapping:**
# ```
# Session: travel-session-xxx
# ├── main branch              → Travel Coordinator
# ├── flight_agent_memory      → Flight Booking Agent
# └── hotel_agent_memory       → Hotel Booking Agent
# ```
#
# **Why This Matters:**
# - Without branching, parallel agents would overwrite each other's memory
# - With branching, each agent maintains its own conversation thread
# - The coordinator can safely delegate to multiple agents at once


import logging  # noqa: E402
from strands import Agent  # noqa: E402
from strands.multiagent import GraphBuilder  # noqa: E402

# Enable debug logs and print them to stderr
logging.getLogger("strands.multiagent").setLevel(logging.DEBUG)
logging.basicConfig(format="%(levelname)s | %(name)s | %(message)s", handlers=[logging.StreamHandler()])

# Build the Strands Agent Graph
# This graph structure enables parallel execution of specialized agents
# Memory branching ensures safe concurrent access without conflicts
builder = GraphBuilder()

# Add nodes - each agent with its own memory branch
builder.add_node(travel_booking_agent(), "travel_agent")  # Uses 'main' branch
builder.add_node(flight_booking_agent(), "flight_booking_agent")  # Uses 'flight_agent_memory' branch
builder.add_node(hotel_booking_agent(), "hotel_booking_agent")  # Uses 'hotel_agent_memory' branch

# Add edges - define which agents the coordinator can delegate to
# The graph can execute flight and hotel agents in parallel when both are needed
builder.add_edge("travel_agent", "flight_booking_agent")
builder.add_edge("travel_agent", "hotel_booking_agent")

# Set entry point - the coordinator agent receives user input first
builder.set_entry_point("travel_agent")

# Configure execution limits for safety
builder.set_execution_timeout(600)  # 10 minute timeout

# Build the graph - ready for parallel execution with isolated memory contexts
graph = builder.build()


# ### Multi-Agent System with Memory Branching is Ready!
#
# Our agent graph is now configured with:
# - ✅ Three agents with isolated memory branches
# - ✅ Parallel execution capability through Strands Agent Graph
# - ✅ Safe concurrent memory access via AgentCore Memory Branching
# - ✅ Automatic branch creation and management
#
# ## Testing the Multi-Agent System
#
# Let's test with a travel planning scenario that will trigger multiple agents:


response = graph("Hello, I would like to book a trip from LA to Madrid. From July 1 to August 2.")


# ## Inspecting Memory Branches
#
# One of the key advantages of AgentCore Memory Branching is the ability to inspect each agent's conversation history independently. This is crucial for:
#
# **Debugging Multi-Agent Systems:**
# - See exactly what each agent discussed
# - Identify which agent handled which part of the conversation
# - Trace the flow of information through the system
#
# **Understanding Parallel Execution:**
# - Verify that agents maintained separate contexts
# - Confirm no memory conflicts occurred during concurrent execution
# - Audit the timeline of agent interactions
#
# Let's explore the branches that were created during our conversation:


print("\n=== Viewing Memory Branches ===")

if flight_memory_hooks or hotel_memory_hooks:
    # Get any memory session to list branches (they all point to the same session)
    hook = flight_memory_hooks if flight_memory_hooks else hotel_memory_hooks
    if hook:
        memory_session = hook.get_session(actor_id, session_id)

        # List all branches in the session
        branches = memory_session.list_branches()
        print(f"\n📊 Session has {len(branches)} branches total:")
        for branch in branches:
            print(f"  - Branch: {branch.name}")
            print(f"    └─ Events: {len(memory_session.list_events(branch_name=branch.name))}")
            print(f"    └─ Created: {branch.created}")

        print("\n💡 Each branch represents a different agent's memory:")
        print("  • 'main' = Travel coordinator conversations")
        print("  • 'flight_agent_memory' = Flight assistant conversations")
        print("  • 'hotel_agent_memory' = Hotel assistant conversations")


# ### Accessing Branch-Specific Conversation History
#
# Now let's dive deeper and examine the actual conversations stored in each branch. This demonstrates how memory branching provides complete isolation between agents while maintaining a shared session.


print("\n=== Accessing Branch-Specific Events ===")

if flight_memory_hooks or hotel_memory_hooks:
    hook = flight_memory_hooks if flight_memory_hooks else hotel_memory_hooks
    if hook:
        memory_session = hook.get_session(actor_id, session_id)

        # Get events from the main branch (coordinator)
        main_events = memory_session.list_events(branch_name="main")
        print(f"\n🌳 Main Branch - Coordinator ({len(main_events)} events):")
        if main_events:
            for event in main_events[-3:]:  # Show last 3 events
                for payload in event.payload:
                    if "conversational" in payload:
                        role = payload["conversational"]["role"]
                        text = payload["conversational"]["content"]["text"]
                        print(f"  {role}: {text[:100]}...")
        else:
            print("  No events found in main branch")

        # Get events from the flight agent branch
        try:
            flight_branch_events = memory_session.list_events(branch_name="flight_agent_memory")
            print(f"\n✈️  Flight Agent Branch ({len(flight_branch_events)} events):")
            if flight_branch_events:
                print("All flight-related conversations are stored here:")
                for event in flight_branch_events[-3:]:  # Show last 3 events
                    for payload in event.payload:
                        if "conversational" in payload:
                            role = payload["conversational"]["role"]
                            text = payload["conversational"]["content"]["text"]
                            print(f"  {role}: {text[:100]}...")
            else:
                print("  No events found - flight assistant wasn't called yet")
        except Exception as e:
            print(f"  Flight branch not created yet: {e}")

        # Get events from the hotel agent branch
        try:
            hotel_branch_events = memory_session.list_events(branch_name="hotel_agent_memory")
            print(f"\n🏨 Hotel Agent Branch ({len(hotel_branch_events)} events):")
            if hotel_branch_events:
                print("All hotel-related conversations are stored here:")
                for event in hotel_branch_events[-3:]:  # Show last 3 events
                    for payload in event.payload:
                        if "conversational" in payload:
                            role = payload["conversational"]["role"]
                            text = payload["conversational"]["content"]["text"]
                            print(f"  {role}: {text[:100]}...")
            else:
                print("  No events found - hotel assistant wasn't called yet")
        except Exception as e:
            print(f"  Hotel branch not created yet: {e}")


# ## Summary
#
# In this notebook, we've demonstrated **AgentCore Memory Branching** - a critical capability for building robust multi-agent systems with parallel execution:
#
# ### Key Takeaways:
#
# 1. **Memory Branching Enables Parallel Execution**
#    - Multiple agents can execute simultaneously without memory conflicts
#    - Each agent maintains its own conversation context through branches
#    - Essential for Strands Agent Graphs and other parallel agent frameworks
#
# 2. **Context Isolation Improves Agent Performance**
#    - Specialized agents focus on their domain without interference
#    - No context pollution between agents
#    - Cleaner, more relevant conversations
#
# 3. **Shared Session with Isolated Contexts**
#    - Single memory resource and session ID
#    - Multiple branches for different agents
#    - Efficient resource utilization
#
#
# ### Architecture Pattern:
#
# ```
# Memory Resource (memory_id)
#   └── Session (session_id)
#       ├── main branch → Coordinator Agent
#       ├── flight_agent_memory → Flight Agent (can run in parallel)
#       └── hotel_agent_memory → Hotel Agent (can run in parallel)
# ```
#
# AgentCore Memory Branching makes it safe and efficient to build sophisticated multi-agent systems that can scale with your application needs.

# ## Clean up
# Let's delete the memory to clean up the resources used in this notebook.


# client.delete_memory_and_wait(
#        memory_id = memory_id,
#        max_wait = 300,
#        poll_interval =10
# )
