# # LangGraph with AgentCore Memory using Episodic Strategy
#
# ## Introduction
#
# This notebook demonstrates how to integrate Amazon Bedrock AgentCore Memory with **episodic memory strategy** in a conversational AI agent using LangGraph framework. We'll focus on the episodic strategy that captures complete conversation sessions, enabling the agent to recall specific meal planning episodes and track how dietary patterns evolve over time.
#
# ## Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long-term Conversational                                                        |
# | Agent usecase       | Nutrition Assistant with Episodic Memory Strategy                               |
# | Agentic Framework   | LangGraph                                                                        |
# | LLM model           | Anthropic Claude Haiku 4.5                                                      |
# | Tutorial components | AgentCore Memory, Episodic Strategy, LangGraph Hooks, Session-based Episodes  |
# | Example complexity  | Intermediate                                                                     |
#
# You'll learn to:
# - Create AgentCore Memory with episodic memory strategy
# - Implement pre/post model hooks for automatic memory storage
# - Build a nutrition assistant that remembers meal planning sessions
# - Retrieve and reflect on past conversations
# - Track dietary patterns over time
#
# ### Scenario Context
#
# In this example, we'll create a **Nutrition Assistant** that remembers complete meal planning sessions using episodic memory strategy. The agent will capture full conversation episodes including recipe discussions, ingredient substitutions, and meal feedback. This enables temporal queries like "What did I plan last week?" and pattern analysis of dietary habits.
#
# ## Architecture
#
# <div style="text-align:left">
#     <img src="architecture_episodic.png" width="65%" />
# </div>
#
# ### Why Episodic Memory Strategy for Nutrition?
#
# - **Session-based**: Each meal planning conversation is an episode
# - **Temporal context**: Meals are tied to specific times/occasions
# - **Pattern learning**: Track how preferences evolve
# - **Rich recall**: Remember full context of past recommendations
#
# ### How Episodic Memory Strategy Works
#
# The episodic strategy is designed to capture interactions as structured episodes and reflect across these episodes to generate meaningful insights. This strategy records not only what happened, but also the intent, thoughts, and outcome for each episode.
#
# #### Three Steps in Episodic Strategy:
#
# 1. **Extraction** – Identifies useful insights from short-term memory to place into long-term memory as memory records
# 2. **Consolidation** – Determines whether to write useful information to a new record or an existing record
# 3. **Reflection** – Insights are generated across episodes from agent interactions
#
# #### Strategy Output:
#
# **Episodes** (XML-formatted):
# - Broken down into: situation, intent, assessment, justification, and episode-level reflection
# - Analyzed turn-by-turn as the interaction proceeds
# - Helps understand order of operations and tool use
#
# **Reflections** (generated in background):
# - Consolidate across multiple episodes
# - Extract broader insights identifying:
#   - Successful strategies and patterns
#   - Potential improvements
#   - Common failure modes
#   - Lessons learned spanning multiple interactions
#
# #### For Nutrition Assistant:
#
# - **Episodes**: Each meal planning session (recipes discussed, ingredients, decisions)
# - **Reflections**: Dietary patterns, favorite cuisines, cooking skill progression
# - **Turn-by-turn**: Recipe exploration → ingredient questions → substitutions → final choice
#
# ## Prerequisites
#
# - Python 3.10+
# - AWS account with appropriate permissions
# - AWS IAM role with appropriate permissions for AgentCore Memory
# - Access to Amazon Bedrock models
#
# Let's get started by setting up our environment!
#


# Install necessary libraries from https://github.com/langchain-ai/langchain-aws


import logging
import os
import uuid

# Import LangGraph and LangChain components
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent
from langgraph.store.base import BaseStore

region = os.getenv("AWS_REGION", "us-east-1")
logging.getLogger("nutrition-agent").setLevel(logging.DEBUG)


import json
import time

import boto3
from bedrock_agentcore.memory import MemoryClient

# `langgraph-checkpoint-aws` gives you two classes, one per LangGraph argument. This script
# wires BOTH, over a single AgentCore memory resource:
#
#   AgentCoreMemoryStore  -> store=         recalls episodes from THIS USER's past conversations
#   AgentCoreMemorySaver  -> checkpointer=  resumes THIS CONVERSATION
#
# They coexist safely on one memory_id because they write different payload types. The store
# writes `conversational` events, which the episodic strategy extracts. The saver writes
# opaque `blob` events, which no strategy ever reads — so checkpoint data never pollutes your
# extracted episodes.
from langgraph_checkpoint_aws import AgentCoreMemorySaver, AgentCoreMemoryStore

# Create IAM role for memory execution
iam_client = boto3.client("iam")
sts_client = boto3.client("sts")
account_id = sts_client.get_caller_identity()["Account"]

ROLE_NAME = "AgentCoreMemoryExecutionRole"

# Trust policy for AgentCore Memory (production)
trust_policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
        }
    ],
}

# Permissions for Bedrock model invocation
permissions_policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            "Resource": [
                "arn:aws:bedrock:*::foundation-model/*",
                "arn:aws:bedrock:*:*:inference-profile/*",
            ],
        }
    ],
}

try:
    # Try to get existing role
    role = iam_client.get_role(RoleName=ROLE_NAME)
    MEMORY_EXECUTION_ROLE_ARN = role["Role"]["Arn"]
    # Update trust policy to ensure it uses the correct production principal
    iam_client.update_assume_role_policy(
        RoleName=ROLE_NAME,
        PolicyDocument=json.dumps(trust_policy),
    )
    print(f"✅ Using existing role (trust policy updated): {MEMORY_EXECUTION_ROLE_ARN}")
    # A trust-policy update is not immediately consistent. Without this wait, a
    # reused role can fail CreateMemory with "Please provide a role with a valid
    # trust policy" — the same propagation delay the create branch waits for below.
    print("⏳ Waiting 10 seconds for IAM propagation...")
    time.sleep(10)
except iam_client.exceptions.NoSuchEntityException:
    # Create role
    print(f"Creating IAM role: {ROLE_NAME}")
    role = iam_client.create_role(
        RoleName=ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(trust_policy),
        Description="Execution role for AgentCore Memory with custom strategies",
    )
    MEMORY_EXECUTION_ROLE_ARN = role["Role"]["Arn"]

    # Attach inline policy
    iam_client.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="BedrockModelAccess",
        PolicyDocument=json.dumps(permissions_policy),
    )
    print(f"✅ Created role: {MEMORY_EXECUTION_ROLE_ARN}")
    print("⏳ Waiting 10 seconds for IAM propagation...")
    time.sleep(10)

print(f"\nRole ARN: {MEMORY_EXECUTION_ROLE_ARN}")


memory_name = "NutritionAssistantEpisodic"
client = MemoryClient(region_name=region)
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# NOTE: this tutorial implements episodic-style memory via a CUSTOM strategy with a
# `semanticOverride` and custom extraction/consolidation prompts (see
# custom_memory_prompts.py) — NOT the built-in `episodicMemoryStrategy`. Records are
# extracted into the episode namespace, but the built-in reflection step is not run.
# For the built-in episodic strategy + reflectionConfiguration, see
# 02-long-term-memory/01-built-in-strategies/episodic.py.
override_strategy = {
    "customMemoryStrategy": {
        "name": "NutritionEpisodicExtractor",
        "description": "Nutrition assistant with episodic memory for meal planning insights",
        "namespaces": ["/nutrition/{actorId}/{sessionId}/"],
        "configuration": {
            "semanticOverride": {
                "extraction": {
                    "modelId": MODEL_ID,
                    "appendToPrompt": "Extract meal planning conversations including recipes discussed, ingredients mentioned, dietary considerations, and user feedback.",
                },
                "consolidation": {
                    "modelId": MODEL_ID,
                    "appendToPrompt": "Consolidate meal planning sessions into episodes, capturing the flow of recipe exploration and decision-making.",
                },
            }
        },
    }
}

memory = client.create_or_get_memory(
    name=memory_name,
    description="Nutrition assistant with episodic memory for meal planning sessions",
    memory_execution_role_arn=MEMORY_EXECUTION_ROLE_ARN,
    strategies=[override_strategy],
)
memory_id = memory["id"]

print(f"✅ Created episodic memory: {memory_id}")


# ### Memory Configuration Overview
#
# Our AgentCore Episodic Memory setup includes:
#
# - **Extraction**: Captures meal planning conversations with recipes, ingredients, and feedback
# - **Consolidation**: Groups conversations into meal planning episodes
# - **Reflection**: Generates insights about dietary patterns and preferences over time
# - **Namespaces**: Organizes episodes by user (`nutrition/{actorId}/`)
#
# Each conversation session becomes an episode that can be recalled and analyzed.
#
# ## Step 3: Initialize Memory Store and LLM
#
# Now we'll initialize the AgentCore Memory Store and our language model.


# Initialize the store to enable long term memory saving and retrieval
store = AgentCoreMemoryStore(memory_id=memory_id, region_name=region)

# Initialize Bedrock LLM
llm = init_chat_model(MODEL_ID, model_provider="bedrock_converse", region_name=region)


# ## Step 4: Implement Memory Hooks
#
# We'll create pre and post model hooks to automatically handle memory storage:
#
# - **Pre-model hook**: Saves the user message before LLM invocation
# - **Post-model hook**: Saves the assistant response after LLM invocation
#
# ### How Memory Processing Works
#
# 1. Messages are saved to AgentCore Memory with actor_id and session_id
# 2. The episodic strategy processes conversations to create structured episodes
# 3. Episodes are stored in the `nutrition/{actorId}/{sessionId}/` namespace with turn-by-turn analysis
# 4. Reflections are generated across episodes and stored in the `nutrition/{actorId}/` namespace
# 5. Each episode captures situation, intent, assessment, and conversation flow
#
# **Note**: LangChain message types are converted under the hood by the store to AgentCore Memory message types so that they can be properly processed into episodes and reflections.
#


def pre_model_hook(state, config: RunnableConfig, *, store: BaseStore):
    """Hook that runs pre-LLM invocation to save the latest human message"""
    actor_id = config["configurable"]["actor_id"]
    thread_id = config["configurable"]["thread_id"]
    # Saving the message to the actor and session combination that we get at runtime
    namespace = (actor_id, thread_id)

    messages = state.get("messages", [])
    # Save the last human message we see before LLM invocation
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            store.put(namespace, str(uuid.uuid4()), {"message": msg})
            break

    # For episodic strategy, we just save messages - no retrieval needed
    # Episodes and reflections are generated automatically in the background
    return {"messages": messages}


def post_model_hook(state, config: RunnableConfig, *, store: BaseStore):
    """Hook that runs post-LLM invocation to save the assistant response"""
    actor_id = config["configurable"]["actor_id"]
    thread_id = config["configurable"]["thread_id"]

    # Saving the message to the actor and session combination that we get at runtime
    namespace = (actor_id, thread_id)

    messages = state.get("messages", [])
    # Save the LLM's response to AgentCore Memory
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            store.put(namespace, str(uuid.uuid4()), {"message": msg})
            break

    return {"messages": messages}


# ## Step 5: Create the LangGraph Agent
#
# Now we'll create our nutrition assistant agent using LangGraph's `create_react_agent` with our memory hooks integrated. The tool node will contain just our long term memory retrieval tool and the pre and post model hooks are specified as arguments.
#
# **Note**: for custom agent implementations the Store and tools can be configured to run as needed for any workflow following this pattern. Pre/post model hooks can be used, the whole conversation could be saved at the end, etc.


# Unlike LangGraph's in-process InMemorySaver, this checkpoint outlives the process:
# re-invoke with the same thread_id from anywhere and the graph picks up mid-conversation.
checkpointer = AgentCoreMemorySaver(memory_id, region_name=region)

graph = create_react_agent(
    llm,
    store=store,  # LONG-TERM: episodes about this user
    tools=[],  # No additional tools needed for this example
    checkpointer=checkpointer,  # SHORT-TERM: this conversation's state
    pre_model_hook=pre_model_hook,  # Saves user message before LLM call
    post_model_hook=post_model_hook,  # Saves assistant response for episodic processing after LLM call
)


# ## Step 6: Configure Agent Runtime
#
# We need to configure the agent with unique identifiers for the user and session. These IDs are crucial for memory organization and retrieval.
#
# ### Graph Invoke Input
# We only need to pass the newest user message in as an argument `inputs`. This could include other state variables as well but for the simple `create_react_agent`, we only need messages.
#
# ### LangGraph RuntimeConfig
# In LangGraph, config is a `RuntimeConfig` that contains attributes that are necessary at invocation time, for example user IDs or session IDs. For the `AgentCoreMemorySaver`, `thread_id` and `actor_id` must be set in the config. For instance, your AgentCore invocation endpoint could assign this based on the identity or user ID of the caller. You can read additional [documentation here](https://langchain-ai.github.io/langgraphjs/how-tos/configuration/)
#
#


actor_id = "user-1"
config = {
    "configurable": {
        "thread_id": "session-1",  # REQUIRED: This maps to Bedrock AgentCore session_id under the hood
        "actor_id": actor_id,  # REQUIRED: This maps to Bedrock AgentCore actor_id under the hood
    }
}


# ## Step 7: Test the Agent
#
# Let's test our nutrition assistant by having a conversation about food preferences. The agent will automatically capture the conversation as episodes for future recall and pattern analysis.


# Helper function to pretty print agent output while running
def run_agent(query: str, config: RunnableConfig):
    printed_ids = set()
    events = graph.stream(
        {"messages": [{"role": "user", "content": query}]},
        config,
        stream_mode="values",
    )
    for event in events:
        if "messages" in event:
            for msg in event["messages"]:
                # Check if we've already printed this message
                if id(msg) not in printed_ids:
                    msg.pretty_print()
                    printed_ids.add(id(msg))


prompt = """
Hey there! Im cooking one of my favorite meals tonight, salmon with rice and veggies (healthy). Has
great macros for my weightlifting competition that is coming up. What can I add to this dish to make it taste better
and also improve the protein and vitamins I get?
"""

run_agent(prompt, config)


# ### What was stored?
# As you can see, the model does not yet have any insights from previous meal planning sessions.
#
# For this implementation with pre/post model hooks, two messages were stored here. The first message from the user and the response from the AI model were both stored as conversational events in AgentCore Memory. It may take a few moments for the episodes and reflections to be generated, so retry after a few mins if nothing is found the first try.
#
# These messages were then processed by the episodic strategy to create structured episodes and reflections in AgentCore long term memory. In fact, we can check the store ourselves to verify what has been stored there so far:


# Search our conversation messages
search_namespace = ("nutrition", actor_id, "session-1/")
result = store.search(search_namespace, query="meal", limit=3)
print(f"Conversation messages result: {result}")


# The correct way to search episodic long-term memories in LangGraph
from bedrock_agentcore.memory import MemoryClient

# Use the memory client directly (not the store)
memory_client = MemoryClient(region_name=region)

print("=== Searching Long-Term Episodic Memories ===")
print(f"Memory ID: {memory_id}")
print()

# Search episodic memories (episodes)
print("1. Episodic namespace: nutrition/user-1/session-1/")
try:
    episodes = memory_client.retrieve_memories(
        memory_id=memory_id,
        namespace="nutrition/user-1/session-1/",
        query="meal",
        top_k=3,
    )
    print(f"   Found {len(episodes)} episode memories")
    for mem in episodes:
        content = mem.get("content", {})
        text = content.get("text", str(content))
        print(f"   - {text[:300]}...")
# Retrieval can fail while extraction is still in flight — report and keep going.
except Exception as e:  # noqa: BLE001
    print(f"   Error: {e}")
print()

# Search reflection memories
print("2. Reflection namespace: nutrition/user-1/")
try:
    reflections = memory_client.retrieve_memories(
        memory_id=memory_id, namespace="nutrition/user-1/", query="meal", top_k=3
    )
    print(f"   Found {len(reflections)} reflection memories")
    for mem in reflections:
        content = mem.get("content", {})
        text = content.get("text", str(content))
        print(f"   - {text[:300]}...")
# Retrieval can fail while extraction is still in flight — report and keep going.
except Exception as e:  # noqa: BLE001
    print(f"   Error: {e}")


# ### Agent access to the store
#
# **Note** - since AgentCore memory processes these events in the background, it may take a few mins for the memory to be extracted and embedded to long term memory retrieval.
#
# Great! Now we have seen that long term memories were extracted to our namespaces based on the earlier messages in the conversation.
#
# Now, let's start a new session and ask about recommendations for what to cook for dinner. The agent can use the store to access the long term memories that were extracted to make a recommendation that the user will be sure to like.


config = {
    "configurable": {
        "thread_id": "session-2",  # New session ID
        "actor_id": actor_id,  # Same actor ID
    }
}

run_agent("Today's a new day, what should I make for dinner tonight?", config)


# ### Wrapping up
#
# As you can see, the agent's conversations are automatically captured and processed into structured episodes with turn-by-turn analysis. The episodic strategy generates insights across multiple meal planning sessions to identify patterns and track how preferences evolve over time.
#
# The AgentCoreMemoryStore is very flexible and can be implemented in a variety of ways, including pre/post model hooks or just tools themselves with store operations. Used alongside the AgentCoreMemorySaver for checkpointing, both full conversational state and episodic reflections can be combined to form a complex and intelligent agent system.
