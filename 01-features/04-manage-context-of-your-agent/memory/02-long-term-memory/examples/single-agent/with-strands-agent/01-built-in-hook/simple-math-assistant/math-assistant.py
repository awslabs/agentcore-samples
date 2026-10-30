#!/usr/bin/env python

# # Strands Agent with AgentCore Memory Tutorial using Hooks
#
# ## Overview
#
# This tutorial demonstrates how to build an intelligent personal assistant using Strands agents integrated with AgentCore Memory through hooks. The agent maintains conversation context and learns from interactions to provide personalized responses.
#
# ## Tutorial Details
#
# **Use Case**: Math Assistant
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long term Conversational                                                         |
# | Agent type          | Math Assistant                                                                   |
# | Agentic Framework   | Strands Agents                                                                   |
# | LLM model           | Anthropic Claude Haiku 4.5                                                      |
# | Tutorial components | AgentCore Summary Strategy for Memory, Hooks for storing and retrieving Memory   |
# | Example complexity  | Intermediate                                                                     |
#
#
# You'll learn to:
# - Set up AgentCore Memory with conversation summaries
# - Create memory hooks for automatic storage and retrieval
# - Build a Strands agent with persistent memory
# - Test memory functionality across conversations
# - Use conversation branching for alternative learning paths
# - Apply metadata for tracking student progress and performance
#
# ### Scenario Context
#
# In this example you'll create a Math Assistant example where you'd store summaries of the previous conversations.
# Key features of this example:
# - **Automatic Memory Storage**: Conversations are automatically saved
# - **Context Retrieval**: Previous conversations inform current responses
# - **Summary Generation**: Key information is extracted and summarized
# - **Tool Integration**: Calculator tool for mathematical operations
# - **Conversation Branching**: Explore alternative difficulty levels and teaching approaches
# - **Metadata Tracking**: Tag events with difficulty, performance, and learning milestones
#
# ## Architecture
# <div style="text-align:left">
#     <img src="architecture.png" width="65%" />
# </div>
#
#
# ## Prerequisites
#
# To execute this tutorial you will need:
# - Python 3.10+
# - AWS credentials with Amazon Bedrock AgentCore Memory permissions
# - Amazon Bedrock AgentCore SDK

# ## Step 1: Environment set up
# Let's begin importing all the necessary libraries and defining the clients to make this notebook work.


# Run: pip install -qr requirements.txt


from bedrock_agentcore.memory import MemoryClient, MemorySessionManager
from bedrock_agentcore.memory.constants import StrategyType
from bedrock_agentcore.memory.constants import (
    ConversationalMessage,
    MessageRole,
    RetrievalConfig,
)
from bedrock_agentcore.memory.models import StringValue


import os
import logging
from strands import Agent
from datetime import datetime
from strands_tools import calculator
from strands.hooks import (
    AfterInvocationEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("memory-tutorial")

# Configuration - replace with your values
REGION = os.getenv("AWS_REGION", "us-west-2")
ACTOR_ID = f"student-{datetime.now().strftime('%Y%m%d%H%M%S')}"
SESSION_ID = f"math-session-{datetime.now().strftime('%Y%m%d%H%M%S')}"

# Define message role constants for cleaner code
USER = MessageRole.USER
ASSISTANT = MessageRole.ASSISTANT


# ## Step 2: Create Memory Resource
#
# In this step, we're creating our memory resource with a semantic strategy. This resource will store and organize our conversation data. The built-in SemanticStrategy automatically captures facts from conversations without requiring an IAM execution role.
#


from botocore.exceptions import ClientError  # noqa: E402

# Initialize Memory Client
memory_client = MemoryClient(region_name=REGION)
memory_name = "MathAssistant"

# Define memory strategy using native SDK format
strategies = [
    {
        StrategyType.SEMANTIC.value: {
            "name": "MathLearningMemory",
            "description": "Captures facts from math learning conversations",
            "namespaces": ["/students/math/{actorId}/"],
        }
    }
]

# Create memory resource
memory_id = None  # Initialize to avoid NameError in exception handler
try:
    memory = memory_client.create_memory_and_wait(
        name=memory_name,
        strategies=strategies,
        description="Memory for tutorial agent",
        event_expiry_days=30,
    )
    memory_id = memory["id"]
    logger.info(f"✅ Created memory: {memory_id}")
except ClientError as e:
    if e.response["Error"]["Code"] == "ValidationException" and "already exists" in str(e):
        logger.info(f"Memory '{memory_name}' already exists, retrieving ID...")
        memories = memory_client.list_memories()
        memory_id = next((m["id"] for m in memories if m["id"].startswith(memory_name)), None)
        if not memory_id:
            raise RuntimeError(f"Memory '{memory_name}' not found after conflict")
        logger.info(f"✅ Retrieved existing memory: {memory_id}")
    else:
        logger.error(f"❌ ERROR: {e}")
        raise

# Verify memory_id was successfully obtained
if memory_id is None:
    raise RuntimeError("Failed to create or retrieve memory ID")


# ## Step 3: Initialize Session Manager
#
# Now we'll create a MemorySessionManager and MemorySession for our student. The session manager provides a cleaner API by automatically handling memory_id, actor_id, and session_id parameters in all operations.
#
# This session-based approach simplifies memory operations and makes the code more maintainable.


# Initialize the session manager
session_manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)

# Create a memory session for the specific student
student_session = session_manager.create_memory_session(actor_id=ACTOR_ID, session_id=SESSION_ID)

logger.info(f"✅ Session manager initialized for memory: {memory_id}")
logger.info(f"✅ Student session created for actor: {ACTOR_ID}")
logger.info(f"   Session ID: {SESSION_ID}")


# ## Step 4: Create Memory Hook Provider with Session Support
#
# This step defines our custom `MemoryHookProvider` class that automates memory operations using the MemorySession. Hooks are special functions that run at specific points in an agent's execution lifecycle. The memory hook we're creating serves two primary functions:
#
# 1. **Retrieve Memories**: Automatically fetches relevant past conversations when a user sends a message using `search_long_term_memories()`
# 2. **Save Memories**: Stores new conversations after the agent responds using `add_turns()` with ConversationalMessage objects
#
# This creates a seamless memory experience without manual management, and the session-based API eliminates the need to pass memory_id, actor_id, and session_id repeatedly.


class MemoryHookProvider(HookProvider):
    """Hook provider for automatic memory management using MemorySession"""

    def __init__(self, student_session):
        """Initialize with a MemorySession instance

        Args:
            student_session: MemorySession instance for the student
        """
        self.student_session = student_session

        # Define retrieval configuration for math learning context
        self.retrieval_config = RetrievalConfig(top_k=5, relevance_score=0.3)

    def retrieve_memories(self, event: MessageAddedEvent):
        """Retrieve relevant memories before processing user message using MemorySession"""
        messages = event.agent.messages
        if messages[-1]["role"] == "user" and "toolResult" not in messages[-1]["content"][0]:
            user_message = messages[-1]["content"][0].get("text", "")

            try:
                # Use MemorySession for context retrieval (no need to pass actor_id)
                namespace_prefix = f"/students/math/{self.student_session._actor_id}/"

                # Search long-term memories using session API
                memories = self.student_session.search_long_term_memories(
                    query=user_message,
                    namespace_prefix=namespace_prefix,
                    top_k=self.retrieval_config.top_k,
                )

                # Filter by relevance score
                filtered_memories = [
                    memory for memory in memories if memory.get("score", 0) >= self.retrieval_config.relevance_score
                ]

                # Extract memory content
                memory_context = []
                for memory in filtered_memories:
                    if isinstance(memory, dict):
                        content = memory.get("content", {})
                        if isinstance(content, dict):
                            text = content.get("text", "").strip()
                            score = memory.get("score", 0)
                            if text:
                                memory_context.append(f"[Score: {score:.2f}] {text}")

                # Inject memories into user message
                if memory_context:
                    context_text = "\n".join(memory_context)
                    original_text = messages[-1]["content"][0].get("text", "")
                    messages[-1]["content"][0]["text"] = f"{original_text}\n\nStudent Learning Context:\n{context_text}"
                    logger.info(
                        f"✅ Retrieved {len(memory_context)} relevant memories (filtered from {len(memories)} total)"
                    )

            except Exception as e:
                logger.error(f"Failed to retrieve memories: {e}")

    def save_memories(self, event: AfterInvocationEvent):
        """Save conversation after agent response using MemorySession"""
        try:
            messages = event.agent.messages
            if len(messages) >= 2 and messages[-1]["role"] == "assistant":
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
                    # Use MemorySession with ConversationalMessage objects
                    interaction_messages = [
                        ConversationalMessage(user_msg, USER),
                        ConversationalMessage(assistant_msg, ASSISTANT),
                    ]

                    result = self.student_session.add_turns(interaction_messages)
                    logger.info(f"✅ Saved conversation using MemorySession - Event ID: {result['eventId']}")

        except Exception as e:
            logger.error(f"Failed to save memories: {e}")

    def register_hooks(self, registry: HookRegistry) -> None:
        """Register memory hooks"""
        registry.add_callback(MessageAddedEvent, self.retrieve_memories)
        registry.add_callback(AfterInvocationEvent, self.save_memories)
        logger.info("✅ Memory hooks registered with MemorySession support")


# ## Step 5: Create Agent with Memory
#
# Now we're creating our Strands agent and connecting it with our memory hook provider that uses MemorySession. This agent will have two key capabilities:
#
# 1. **Memory Integration**: The memory hooks we created will enable automatic context retrieval using session-based operations
# 2. **Calculator Tool**: The agent can perform mathematical operations when needed
#
# This combination creates a math tutor that both remembers student progress and can perform useful calculations.


# Create memory hook provider with MemorySession
memory_hooks = MemoryHookProvider(student_session)

# Create agent with memory hooks and calculator tool
agent = Agent(
    hooks=[memory_hooks],
    model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
    tools=[calculator],
    system_prompt="You are a helpful personal math tutor. You assist users in solving math problems and provide personalized assistance based on their learning progress and preferences.",
)

logger.info("✅ Agent created with MemorySession-based hooks")
logger.info(f"   Student: {ACTOR_ID}")
logger.info(f"   Session: {SESSION_ID}")


# **We have our agent set up ! Let's test it now.**
#
# ## Test Memory Functionality
#
# In this section, we'll test the agent's memory capabilities through a series of interactions. We'll observe how the agent builds context over time and recalls previous interactions.
#
# First, let's introduce ourselves to the agent and ask a math question:


# First interaction - introduce yourself
response1 = agent(
    "Hi, I'm John and I just enrolled in Discrete Math course. Help me solve this: How many ways can I arrange 5 books on a shelf?"
)
print(f"Agent: {response1}")


# Let's give the agent another calculation task:


# Second interaction - another calculation
response2 = agent(
    "I learn better with step-by-step explanation with example questions. Can you explain modular arithmetic? What's 17 mod 5?"
)
print(f"Agent: {response2}")


# Now, let's see if the agent remembers who we are.
#
# **Note:** Give a ~20 sec pause here to allow some time for the memory to be extracted, consolidated and stored.


# Third interaction - test memory recall
response3 = agent("I got that right! What's the immediate next step that I should study after modular arithmetic?")
print(f"Agent: {response3}")


# Finally, let's check if the agent remembers our calculation history:


# Fourth interaction - test context awareness
response4 = agent("This is too hard, can we try something easier?")
print(f"Agent: {response4}")


# ### Verify Memory Storage
#
# As a final step, we'll verify that our conversations have been properly stored in AgentCore Memory. This demonstrates that the memory hooks are working correctly and the agent can access this information in future interactions.


# Check stored memories using MemorySession
try:
    namespace_prefix = f"/students/math/{ACTOR_ID}/"

    memories = student_session.search_long_term_memories(
        query="mathematics calculations learning progress",
        namespace_prefix=namespace_prefix,
        top_k=5,
    )

    print(f"\n📚 Found {len(memories)} memories for student {ACTOR_ID}:")
    print("=" * 60)
    for i, memory in enumerate(memories, 1):
        if isinstance(memory, dict):
            content = memory.get("content", {})
            score = memory.get("score", 0)
            if isinstance(content, dict):
                text = content.get("text", "")[:200] + "..."
                print(f"\n{i}. [Relevance: {score:.2f}]")
                print(f"   {text}")
    print("\n" + "=" * 60)

except Exception as e:
    logger.error(f"Error retrieving memories: {e}")


# ## Advanced Features: Branching and Metadata
#
# ### Conversation Branching
#
# Branching allows you to explore alternative conversation paths from any point. This is useful for:
# - Testing different difficulty levels
# - Exploring alternative explanations
# - A/B testing teaching approaches
#
# Let's create a branch to explore a harder problem path:


# Get the last event ID from our conversation
events = student_session.list_events()
if events:
    last_event_id = events[-1].eventId

    # Fork conversation to explore advanced topics
    branch_event = student_session.fork_conversation(
        root_event_id=last_event_id,
        branch_name="advanced-path",
        messages=[
            ConversationalMessage(
                "Actually, I'm ready for a challenge! Can you give me a harder problem involving modular arithmetic and combinatorics?",
                USER,
            ),
            ConversationalMessage(
                "Great! Here's a challenging problem: How many 4-digit numbers are there where the sum of digits is congruent to 3 (mod 5)? This combines modular arithmetic with counting principles.",
                ASSISTANT,
            ),
        ],
    )

    logger.info(f"✅ Created branch 'advanced-path' from event {last_event_id}")
    logger.info(f"   Branch event ID: {branch_event['eventId']}")

    # List all branches
    branches = student_session.list_branches()
    print(f"\n🌳 Session has {len(branches)} branch(es):")
    for branch in branches:
        print(f"   - {branch.name}: {branch.event_count} events")

    # Get events from the advanced branch
    advanced_events = student_session.list_events(branch_name="advanced-path")
    print(f"\n📋 Advanced branch has {len(advanced_events)} events")
else:
    print("No events found to branch from")


# ### Creative Metadata Usage
#
# Metadata allows you to tag events with custom information for better organization and retrieval. Let's use metadata to track:
# - Problem difficulty levels
# - Student performance
# - Topic categories
# - Learning milestones


# Add a new interaction with rich metadata
metadata_event = student_session.add_turns(
    messages=[
        ConversationalMessage(
            "Let me try: If I choose 3 books from 5, that's C(5,3) = 10 ways, right?",
            USER,
        ),
        ConversationalMessage(
            "Excellent! You correctly applied the combination formula. That's exactly right: C(5,3) = 5!/(3!×2!) = 10.",
            ASSISTANT,
        ),
    ],
    metadata={
        "difficulty": StringValue.build("intermediate"),
        "topic": StringValue.build("combinatorics"),
        "subtopic": StringValue.build("combinations"),
        "performance": StringValue.build("correct"),
        "milestone": StringValue.build("first_correct_combination"),
        "learning_stage": StringValue.build("applying_formulas"),
    },
)

logger.info(f"✅ Added event with metadata - Event ID: {metadata_event['eventId']}")
print("\n📊 Event tagged with:")
print("   - Difficulty: intermediate")
print("   - Topic: combinatorics")
print("   - Performance: correct")
print("   - Milestone: first_correct_combination")


# ### Querying Events by Metadata
#
# Now we can filter events based on metadata to analyze student progress:


# Query events where student got the answer correct
try:
    correct_events = student_session.list_events(
        eventMetadata=[
            {
                "left": {"metadataKey": "performance"},
                "operator": "EQUALS_TO",
                "right": {"metadataValue": {"stringValue": "correct"}},
            }
        ]
    )

    print(f"\n✅ Found {len(correct_events)} event(s) where student answered correctly")

    # Query intermediate difficulty problems
    intermediate_events = student_session.list_events(
        eventMetadata=[
            {
                "left": {"metadataKey": "difficulty"},
                "operator": "EQUALS_TO",
                "right": {"metadataValue": {"stringValue": "intermediate"}},
            }
        ]
    )

    print(f"📈 Found {len(intermediate_events)} intermediate difficulty problem(s)")

    # Query combinatorics topics
    combinatorics_events = student_session.list_events(
        eventMetadata=[
            {
                "left": {"metadataKey": "topic"},
                "operator": "EQUALS_TO",
                "right": {"metadataValue": {"stringValue": "combinatorics"}},
            }
        ]
    )

    print(f"🎯 Found {len(combinatorics_events)} combinatorics-related event(s)")

    print("\n💡 Use cases for metadata:")
    print("   - Track student progress by difficulty level")
    print("   - Identify topics needing more practice")
    print("   - Generate performance reports")
    print("   - Personalize learning paths based on history")

except Exception as e:
    logger.error(f"Error querying metadata: {e}")
    print("Note: Metadata filtering requires events with metadata tags")


# Tutorial completed! 🎉
#
# Key takeaways:
# - Memory hooks automatically store and retrieve conversation context
# - Agents can maintain state across multiple interactions
# - AgentCore Memory provides semantic search for relevant context
# - Tools can be combined with memory for enhanced functionality
# - **Branching enables exploring alternative conversation paths**
# - **Metadata provides powerful filtering and analytics capabilities**

# ## Clean Up
#
# ### Optional: Delete Memory Resource
#
# After completing the tutorial, you may want to delete the memory resource to avoid incurring unnecessary costs. The following code is provided for cleanup but is commented out by default.


# Uncomment to delete the memory resource
# try:
#     memory_client.delete_memory_and_wait(memory_id=memory_id)
#     print(f"✅ Deleted memory resource: {memory_id}")
# except Exception as e:
#     print(f"Error deleting memory: {e}")
