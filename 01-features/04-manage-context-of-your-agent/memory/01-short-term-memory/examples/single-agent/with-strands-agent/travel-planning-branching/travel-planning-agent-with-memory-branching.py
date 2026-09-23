#!/usr/bin/env python

# # Strands multi-agent System with AgentCore Memory Branching

# ## Introduction
#
# This notebook demonstrates how to implement a **multi-agent system with memory branching** using AWS AgentCore Memory and the Strands framework. This example showcases an advanced memory feature: **conversation branching**, which allows agents to fork conversation history and explore alternative conversation paths while preserving the original conversation thread.
#
# ## Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Short Term Conversation with Memory Branching                                                         |
# | Agent usecase       | Travel Planning Assistant                                                        |
# | Agentic Framework   | Strands Agents                                                                   |
# | LLM model           | Anthropic Claude Haiku 4.5                                                   |
# | Tutorial components | AgentCore Short-term Memory, Strands Agents, Memory retrieval via Tool           |
# | Example complexity  | Beginner                                                                         |
#
#
# What you will learn:
#
# - How to implement memory branching to fork conversation history
# - Creating specialized agents that work on different conversation branches
# - Managing branch lifecycle (creation, initialization, and switching between branches)
# - Maintaining conversation context across both main and branched conversations
#
# ### Scenario context
#
# In this example, we'll create a **Travel Planning System** that demonstrates memory branching:
# 1. A **main branch** conversation where all the conversations are stored
# 2. A **flight agent branch** that forks from the main conversation to explore flight options
# 3. A **hotel agent branch** that forks from the main conversation to explore hotel options
# 4. Both agents can access the shared conversation history from the main branch
#
# This approach demonstrates how memory branching enables:
# - **Parallel exploration**: Agents can explore "what-if" scenarios without affecting the main conversation
# - **Context preservation**: Branched conversations maintain access to the original conversation history
# - **Specialized workflows**: Each branch can follow its own conversation path while staying grounded in the original context
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


# ### Understanding Memory with Branching Support
#
# The memory resource we've created supports **conversation branching**, which enables:
#
# 1. **Shared Session, Multiple Branches**: All agents use the same `memory_id`, `actor_id`, and `session_id`, but different `branch_name` values
# 2. **Context Inheritance**: When a branch is forked, it inherits the conversation history from the parent branch (usually "main")
# 3. **Isolated Evolution**: After forking, each branch maintains its own independent conversation thread
# 4. **Branch-Specific Retrieval**: Agents can retrieve memories from their specific branch
#
# This branching approach allows specialized agents to maintain their own conversation contexts while staying grounded in the shared session history.

# ## Step 3: Create Memory Hook Provider with Branching Support
#
# This step defines our custom `ShortTermMemoryHook` class that implements **memory branching**. This advanced hook provider extends basic memory operations with branch management capabilities:
#
# ### Key Features:
# 1. **Branch Management**: Automatically creates and initializes conversation branches
# 2. **Retrieve Memories**: Fetches conversation history from the specified branch
# 3. **Save Memories**: Stores new conversations to the appropriate branch
# 4. **Branch Forking**: Creates new branches from the main conversation thread
#
# ### How Branching Works:
# - Each agent can specify a `branch_name` (defaults to "main")
# - Non-main branches are automatically forked from the main conversation
# - Branches inherit the conversation history up to the fork point
# - Each branch maintains its own independent conversation flow after forking
#
#


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

            # Initialize branch if needed (for non-main branches)
            if self.branch_name != "main":
                self._initialize_branch(actor_id, session_id)

            # Get the memory session
            memory_session = self._get_or_create_session(actor_id, session_id)

            # Get last 5 conversation turns from this branch
            recent_turns = memory_session.get_last_k_turns(k=5, branch_name=self.branch_name)

            if recent_turns:
                # Format conversation history for context
                context_messages = []
                for turn in recent_turns:
                    for message in turn:
                        # get_last_k_turns returns EventMessage dicts shaped as
                        # {"role": ..., "content": {"text": ...}} — role/content are
                        # top-level keys. (The old `message.content.get("role")` read
                        # role out of the content dict, so it was always "unknown" and
                        # the text always "", silently dropping all history.)
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


# ## Step 4: Create Multi-Agent Architecture with Memory Branching
#
# In this section, we'll create specialized agents that use **different memory branches** to demonstrate the branching capability:
#
# ### Branching Strategy:
# - **Main Branch**: Stores the coordinator's conversation and serves as the base conversation thread
# - **flight_agent_memory Branch**: A separate branch for flight-specific conversations
# - **hotel_agent_memory Branch**: A separate branch for hotel-specific conversations
#
# Each specialized agent operates on its own branch, which is automatically forked from the main conversation when first used. This allows:
#
# - Independent conversation flows for different specializations
# - Isolation of domain-specific context
# - Preservation of the main conversation thread


# Import the necessary components
from strands import Agent, tool  # noqa: E402


# Create unique actor IDs for each specialized agent but share the session ID
actor_id = f"travel-user-{datetime.now().strftime('%Y%m%d%H%M%S')}"
session_id = f"travel-session-{datetime.now().strftime('%Y%m%d%H%M%S')}"
namespace = f"travel/{actor_id}/preferences/"


# ### Creating Specialized Agents with Branched Memory
#
# Next, we'll define system prompts and create agents that use different memory branches. Notice how we use the same `actor_id` and `session_id` but different `branch_name` values to create isolated conversation contexts:


# System prompt for the hotel booking specialist
HOTEL_BOOKING_PROMPT = """You are a hotel booking assistant. Help customers find hotels, make reservations, and answer questions about accommodations and amenities. 
Provide clear information about availability, pricing, and booking procedures in a friendly, helpful manner."""

# System prompt for the flight booking specialist
FLIGHT_BOOKING_PROMPT = """You are a flight booking assistant. Help customers find flights, make reservations, and answer questions about airlines, routes, and travel policies. 
Provide clear information about flight availability, pricing, schedules, and booking procedures in a friendly, helpful manner."""


flight_memory_hooks = None
hotel_memory_hooks = None


# ### Implementing Agent Tools with Branch-Specific Memory
#
# Now we'll implement our specialized agents as tools. Each agent gets its own memory hook configured with a specific branch name:
# - Flight assistant uses the `flight_agent_memory` branch
# - Hotel assistant uses the `hotel_agent_memory` branch
#
# When these agents are invoked:
# 1. The hook checks if the branch exists
# 2. If not, it forks a new branch from the main conversation
# 3. The agent's conversation is stored on its dedicated branch
# 4. The agent can still access context from the main branch


@tool
def flight_booking_assistant(query: str) -> str:
    """
    Process and respond to flight booking queries.

    Args:
        query: A flight-related question about bookings, schedules, airlines, or travel policies

    Returns:
        Detailed flight information, booking options, or travel advice
    """
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

        response = hotel_booking_agent(query)
        return str(response)
    except Exception as e:
        return f"Error in hotel booking assistant: {str(e)}"


# ### Creating the Coordinator Agent (Main Branch)
#
# The coordinator agent operates on the **main branch** and delegates to specialized agents. Notice that:
#
# - When it calls the flight or hotel assistants, those agents fork their own branches
# - Each specialized agent's branch starts from the current state of the main conversation


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


agent_memory_hooks = ShortTermMemoryHook(
    memory_id=memory_id,
    region_name=region,
)


travel_agent = Agent(
    system_prompt=TRAVEL_AGENT_SYSTEM_PROMPT,
    hooks=[agent_memory_hooks],
    model=MODEL_ID,
    tools=[flight_booking_assistant, hotel_booking_assistant],
    state={"actor_id": actor_id, "session_id": session_id},
)


# #### Your Multi-Agent System is ready !!
#
# ## Let's test the Agent.
#
# Let's test our multi-agent system with a travel planning scenario:


response = travel_agent("Hello, I would like to book a trip from LA to Madrid. From July 1 to August 2.")


response = travel_agent("I would only like to focus on the flight at the moment. Direct flight with British Airways")


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
# In this notebook, we've demonstrated:
#
# 1. **Memory Branching Fundamentals**: How to create and manage conversation branches in AgentCore Memory
# 2. **Branch-Specific Agents**: How to implement specialized agents that operate on different memory branches
# 3. **Automatic Branch Creation**: How branches are automatically forked from the main conversation when first used
# 4. **Branch Persistence**: How each branch maintains its own independent conversation history
# 5. **Context Inheritance**: How branched conversations inherit context from the main branch up to the fork point
#
# ### Key Benefits of Memory Branching:
# - **Isolation**: Each agent maintains its own conversation context without interfering with others
# - **Flexibility**: Explore alternative conversation paths without affecting the main thread
# - **Organization**: Keep domain-specific conversations organized in separate branches
# - **Persistence**: Branch-specific memories persist across agent instances
#
# This memory branching architecture provides a powerful approach for building sophisticated multi-agent systems where different agents need to maintain separate but related conversation contexts.

# ## Clean up
# Let's delete the memory to clean up the resources used in this notebook.


# client.delete_memory_and_wait(
#        memory_id = memory_id,
#        max_wait = 300,
#        poll_interval =10
# )
