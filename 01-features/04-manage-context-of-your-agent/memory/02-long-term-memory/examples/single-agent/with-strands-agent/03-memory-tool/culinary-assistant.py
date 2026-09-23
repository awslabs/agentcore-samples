#!/usr/bin/env python

# # Strands Agents with AgentCore Memory (Long-term) using tools

# ## Overview
# This notebook demonstrates how to implement long-term memory capabilities for conversational AI agents using Strands and AgentCore Memory. You'll learn how to extract and consolidate important information from short-term interactions, enabling an agent to recall key details across multiple conversation sessions over time.
#
# ## Tutorial Details
# **Use Case:** Culinary Assistant with Persistent Memory
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long term Conversational                                                         |
# | Agent type          | Culinary Assistant                                                               |
# | Agentic Framework   | Strands Agents                                                                   |
# | LLM model           | Anthropic Claude Haiku 4.5                                                      |
# | Tutorial components | AgentCore 'User Preferences' Memory Extraction, Memory Tool for storing and retrieving Memory              |
# | Example complexity  | Beginner                                                                     |
#
# You'll learn to:
# - Configure AgentCore Memory with extraction strategies for long-term retention
# - Hydrate memory with previous conversation history
# - Use long-term memory to deliver personalized experiences across conversation sessions
# - Integrate Strands Agent Framework with the AgentCore Memory tool
#
# ## Scenario Context
#
# In this tutorial, you'll step into the role of a Culinary Assistant designed to deliver highly personalized restaurant recommendations. By leveraging AgentCore Memory's long-term retention and automatic information extraction, the agent can remember user preferences—such as dietary choices and favorite cuisines—across multiple conversations. This persistent memory enables the agent to provide tailored suggestions and a seamless user experience, even as conversations span days or weeks. The scenario demonstrates how structured memory organization and configurable strategies empower conversational AI to move beyond short-term recall, creating truly engaging and context-aware interactions.
#
#
# ## Architecture
#
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
# Let's get started by setting up our environment and creating our long-term memory resource with the appropriate extraction strategy!

# ## Step 1: Environment set up
# Let's begin importing all the necessary libraries and defining the clients to make this notebook work.


# Run: pip install -qr requirements.txt


import time
import logging
from datetime import datetime


# Define the region and the role with the appropiate permissions for Amazon Bedrock models and AgentCore


import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("culinary-memory")

region = os.getenv("AWS_REGION", "us-west-2")


# ## Step 2: Creating Memory with Long-Term Strategies
#
# In this section, we'll create a memory resource configured with long-term memory capabilities. Unlike our previous short-term memory example, this implementation includes specific memory strategies that enable consolidated information retention.


from bedrock_agentcore.memory import MemoryClient  # noqa: E402
from bedrock_agentcore.memory.constants import StrategyType  # noqa: E402

client = MemoryClient(region_name=region)

memory_name = "CulinaryAssistant"
memory_id = None


print("Creating or retrieving Long-Term Memory...")
memory = client.create_or_get_memory(
    name=memory_name,
    description="Culinary Assistant Agent with long term memory",
    strategies=[
        {
            StrategyType.USER_PREFERENCE.value: {
                "name": "UserPreferences",
                "description": "Captures user preferences",
                "namespaces": ["user/{actorId}/preferences/"],
            }
        }
    ],
    event_expiry_days=7,
)
memory_id = memory["id"]
if not memory_id:
    raise RuntimeError("Memory ID is None after creation — aborting")
print(f"Memory ready with ID: {memory_id}")


# ### Understanding Long-Term Memory Strategies
#
# The key difference in this memory creation is the addition of a **memory strategy**. Let's break down the components:
#
# #### 1. User Preference Memory Strategy
#
# This strategy automatically identifies and extracts user preferences from conversations:
#
# ```python
# "userPreferenceMemoryStrategy": {
#     "name": "UserPreferences",
#     "description": "Captures user preferences",
#     "namespaceTemplates": ["user/{actorId}/preferences/"]
# }
# ```
#
# #### 2. Memory Namespaces
#
# The `namespaceTemplates` parameter defines where extracted information is stored:
#
# ```python
# "namespaceTemplates": ["user/{actorId}/preferences/"]
# ```

# This memory strategy creates a more sophisticated memory system that doesn't just remember conversations, but actually understands and organizes the important information within those conversations for future use.

# ## Step 3: Saving Previous Conversations to Memory
#
# In this section, we'll demonstrate how to hydrate the short-term memory, which automatically triggers the long-term memory extraction process behind the scenes.
#
# ### Hydrating Short-Term Memory
#
# When we save conversations to a memory resource configured with extraction strategies, the system automatically processes this information for long-term retention without requiring additional code.


actor_id = f"user-{datetime.now().strftime('%Y%m%d%H%M%S')}"
session_id = f"foodie-{datetime.now().strftime('%Y%m%d%H%M%S')}"
namespace = f"user/{actor_id}/preferences/"


previous_messages = [
    ("Hi, I'm John", "USER"),
    ("Hi John, how can I help you with food recommendations today?", "ASSISTANT"),
    ("I'm looking for some vegetarian dishes to try this weekend.", "USER"),
    (
        "That sounds great! I'd be happy to help with vegetarian recommendations. Do you have any specific ingredients or cuisine types you prefer?",
        "ASSISTANT",
    ),
    ("Yes, I really like tofu and fresh vegetables in my dishes", "USER"),
    (
        "Perfect! Tofu and fresh vegetables make for excellent vegetarian meals. I can suggest some stir-fries, Buddha bowls, or tofu curries. Do you have any other preferences?",
        "ASSISTANT",
    ),
    (
        "I also really enjoy Italian cuisine. I love pasta dishes and would like them to be vegetarian-friendly.",
        "USER",
    ),
    (
        "Excellent! Italian cuisine has wonderful vegetarian options. I can recommend pasta primavera, mushroom risotto, eggplant parmesan, or penne arrabbiata. The combination of Italian flavors with vegetarian ingredients creates delicious meals!",
        "ASSISTANT",
    ),
    (
        "I spent 2 hours looking through cookbooks but couldn't find inspiring vegetarian Italian recipes",
        "USER",
    ),
    (
        "I'm sorry you had trouble finding inspiring recipes! Let me help you with some creative vegetarian Italian dishes. How about stuffed bell peppers with Italian herbs and rice, spinach and ricotta cannelloni, or a Mediterranean vegetable lasagna?",
        "ASSISTANT",
    ),
    ("Hey, I appreciate food assistants with good taste", "USER"),
    (
        "Ha! I definitely try to bring good taste to the table! Speaking of which, shall we explore some more vegetarian Italian recipes that might inspire you?",
        "ASSISTANT",
    ),
]


print("\nHydrating short term memory with previous conversations...")

# Save the conversation history to short-term memory
initial = client.create_event(
    memory_id=memory_id,
    actor_id=actor_id,
    session_id=session_id,
    messages=previous_messages,
)
print("✓ Conversation saved in short term memory")


# Let's make sure the event containing the conversation messages was stored correctly.


events = client.list_events(memory_id=memory_id, actor_id=actor_id, session_id=session_id, max_results=5)
events


# This cell configures the logging system to display informative messages during execution, helping us track what's happening as our code runs.

# ### What Happens Behind the Scenes
#
# After the `create_event` call, the following occurs automatically:
#
# 1. **Short-Term Storage**: The complete conversation is saved in raw form
# 2. **Extraction Trigger**: The memory system detects that this memory has the UserPreference strategy configured
# 3. **Background Processing**: Without any additional code, the system:
#    - Analyzes the conversation for preference indicators
#    - Identifies statements like "I'm vegetarian" and "I really enjoy Italian cuisine"
#    - Extracts these preferences into structured data
# 4. **Long-Term Consolidation**: The extracted preferences are saved in the configured namespace (`user/{actorId}/preferences/`)
#
# Extraction and consolidation happen automatically - we only need to mantain a conversation with the agent or hydrate the short-term memory, and the strategies we configured during memory creation take care of the rest.
#
# This automatic process ensures that important information is preserved in long-term memory even after the short-term conversation records expire.

#
# ## Retrieving Long-Term Memories
#
# In this section, we'll explore how to access the extracted preferences that have been stored in long-term memory. Unlike short-term memory retrieval which focuses on conversation turns, long-term memory retrieval focuses on accessing structured information that has been extracted and consolidated.

# ### Accessing User Preferences from Long-Term Memory
#
# To retrieve information from long-term memory, we use the namespace structure defined during memory creation:
#


# Adding a 30s wait to ensure the memory extraction has time to process the event
time.sleep(30)

try:
    # Query the memory system for food preferences
    food_preferences = client.retrieve_memories(
        memory_id=memory_id,
        namespace=namespace,
        query="food preferences",
        top_k=3,  # Return up to 3 most relevant results
    )

    if food_preferences:
        print(f"Retrieved {len(food_preferences)} relevant preference records:")
        for i, record in enumerate(food_preferences):
            print(f"\nMemory {i + 1}:")
            print(f"- Content: {record.get('content', 'Not specified')}")
    else:
        print("No matching preference records found.")

except Exception as e:
    print(f"Error retrieving preference records: {e}")


# This method enables the retrieval of relevant memories when needed. Now we learned the basics let's build up our agent!

# ## Step 4: Creating the agent
# In this section, we'll explore how to integrate AgentCore Memory with a Strands Agent using the native `agent_core_memory` tool.
#
# #### Setting Up the Agent with Long term Memory Capabilities
# To create a memory-enabled agent, we'll use the Strands framework and connect it to our AgentCore Memory resource


from strands import Agent  # noqa: E402
from strands_tools.agent_core_memory import AgentCoreMemoryToolProvider  # noqa: E402


system_prompt = """You are the Culinary Assistant, a sophisticated restaurant recommendation assistant.

PURPOSE:
- Help users discover restaurants based on their preferences
- Remember user preferences throughout the conversation
- Provide personalized dining recommendations

You have access to a Memory tool that enables you to:
- Store user preferences (dietary restrictions, favorite cuisines, budget preferences, etc.)
- Retrieve previously stored information to personalize recommendations

"""


provider = AgentCoreMemoryToolProvider(
    memory_id=memory_id, actor_id=actor_id, session_id=session_id, namespace=namespace
)

agent = Agent(
    tools=provider.tools,
    model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
    system_prompt=system_prompt,
)


# As we have already populated our short term and long term memory, let's directly retrieve the memory from the agent!


agent("Give me restaurant recommendations in Irvine based on my food preferences")


# The agent should have used the retrieve_memory_records method to retrieve the user's memories.

# Great! You know have a working Strands Agent capable of retrieving memories from the AgentCore Long Term Memory!

# ## Clean up
# Let's delete the memory to clean up the resources used in this notebook.


# client.delete_memory_and_wait(
#        memory_id = memory_id,
#        max_wait = 300,
#        poll_interval =10
# )
