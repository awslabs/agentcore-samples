# # LangGraph with AgentCore Memory Hooks (Long-term Memory)
#
# ## Introduction
#
# This notebook demonstrates how to integrate Amazon Bedrock AgentCore Memory capabilities with a conversational AI agent using LangGraph framework. We'll focus on **long-term memory** retention across multiple conversation sessions - allowing an agent to extract and recall user preferences, dietary restrictions, and contextual information from past interactions.
#
# ## Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long-term Conversational                                                        |
# | Agent usecase       | Nutrition Assistant                                                              |
# | Agentic Framework   | LangGraph                                                                        |
# | LLM model           | Anthropic Claude Haiku 4.5                                                     |
# | Tutorial components | AgentCore Long-term Memory, Custom Memory Strategies, Pre/Post Model Hooks     |
# | Example complexity  | Intermediate                                                                     |
#
# You'll learn to:
# - Create AgentCore Memory with UserPreference custom-override strategy
# - Implement pre/post model hooks for automatic memory storage and retrieval
# - Build a nutrition assistant that remembers user preferences across sessions
# - Use semantic search to retrieve relevant user context
# - Configure custom memory extraction and consolidation prompts
#
# ### Scenario Context
#
# In this example, we'll create a **Nutrition Assistant** that can remember user context across multiple conversations, including dietary restrictions, favorite foods, cooking preferences, and health goals. The agent will automatically extract and store user preferences from conversations, then retrieve relevant context for future interactions to provide personalized nutrition advice.
#
# ## Architecture
#
# <div style="text-align:left">
#     <img src="architecture.png" width="65%" />
# </div>
#
# ## Prerequisites
#
# - Python 3.10+
# - AWS account with appropriate permissions
# - AWS IAM role with appropriate permissions for AgentCore Memory
# - Access to Amazon Bedrock models
#
# Let's get started by setting up our environment!


# Install necessary libraries from https://github.com/langchain-ai/langchain-aws


import json as json_module
import logging
import os
import uuid

import boto3
from botocore.exceptions import ClientError

# Import LangGraph and LangChain components
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent
from langgraph.store.base import BaseStore

region = os.getenv("AWS_REGION", "us-east-1")
logger = logging.getLogger("nutrition-assistant")
logger.setLevel(logging.DEBUG)


# `langgraph-checkpoint-aws` gives you two classes, one per LangGraph argument. This script
# wires BOTH, over a single AgentCore memory resource:
#
#   AgentCoreMemoryStore  -> store=         recalls facts about THIS USER across conversations
#   AgentCoreMemorySaver  -> checkpointer=  resumes THIS CONVERSATION
#
# They coexist safely on one memory_id because they write different payload types. The store
# writes `conversational` events, which the user-preference strategy extracts. The saver
# writes opaque `blob` events, which no strategy ever reads — so checkpoint data never
# pollutes your extracted preferences.
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType
from custom_memory_prompts import consolidation_prompt, extraction_prompt
from langgraph_checkpoint_aws import AgentCoreMemorySaver, AgentCoreMemoryStore

memory_name = "NutritionAssistant"
client = MemoryClient(region_name=region)
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


def create_memory_execution_role():
    """Create IAM role for AgentCore Memory custom strategies with required permissions."""
    iam_client = boto3.client("iam", region_name=region)
    sts_client = boto3.client("sts", region_name=region)
    account_id = sts_client.get_caller_identity()["Account"]
    role_name = "AgentCoreMemoryExecutionRole"
    role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "",
                "Effect": "Allow",
                "Principal": {"Service": ["bedrock-agentcore.amazonaws.com"]},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }
    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    "arn:aws:bedrock:*:*:inference-profile/*",
                ],
                "Condition": {"StringEquals": {"aws:ResourceAccount": account_id}},
            }
        ],
    }
    try:
        iam_client.get_role(RoleName=role_name)
        logger.info(f"IAM role already exists: {role_arn}")
        return role_arn
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
    iam_client.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json_module.dumps(trust_policy),
        Description="Execution role for AgentCore Memory custom strategies",
    )
    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName="AgentCoreMemoryBedrockAccess",
        PolicyDocument=json_module.dumps(permissions_policy),
    )
    logger.info(f"Created IAM role: {role_arn}")
    return role_arn


MEMORY_EXECUTION_ROLE_ARN = create_memory_execution_role()

memory = client.create_or_get_memory(
    name=memory_name,
    description="Nutrition assistant",
    memory_execution_role_arn=MEMORY_EXECUTION_ROLE_ARN,
    strategies=[
        {
            StrategyType.CUSTOM.value: {
                "name": "NutritionPreferences",
                "description": "Captures customer food preferences and behavior",
                "namespaces": ["/{actorId}/preferences/"],
                "configuration": {
                    "userPreferenceOverride": {
                        "extraction": {
                            "appendToPrompt": extraction_prompt,
                            "modelId": MODEL_ID,
                        },
                        "consolidation": {
                            "appendToPrompt": consolidation_prompt,
                            "modelId": MODEL_ID,
                        },
                    }
                },
            }
        },
    ],
)
memory_id = memory["id"]


# ### Memory Configuration Overview
#
# Our AgentCore Memory setup includes:
#
# - **Custom Strategy**: Extracts nutrition preferences from conversations
# - **Namespaces**: Organizes memories by user (`{actorId}/preferences/`)
# - **Custom Prompts**: Specialized extraction and consolidation logic for food preferences
# - **Model Integration**: uses `MODEL_ID` (Claude Haiku 4.5) for extraction and consolidation
#
# The memory system will automatically process conversations to extract lasting user preferences while filtering out temporary or irrelevant information.
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
# We'll create pre and post model hooks to automatically handle memory storage and retrieval:
#
# - **Pre-model hook**: Retrieves relevant user preferences (based on semantic search) and adds context before LLM invocation
# - **Post-model hook**: Saves the conversation messages for long-term memory extraction
#
# ### How Memory Processing Works
#
# 1. Messages are saved to AgentCore Memory with actor_id and session_id
# 2. The custom strategy processes conversations to extract nutrition preferences
# 3. Extracted preferences are stored in the `{actorId}/preferences/` namespace
# 4. Future conversations can search and retrieve relevant preferences for context
#
# **Note**: LangChain message types are converted under the hood by the store to AgentCore Memory message types so that they can be properly extracted to long term memories.


def pre_model_hook(state, config: RunnableConfig, *, store: BaseStore):
    """Hook that runs pre-LLM invocation to save the latest human message"""
    actor_id = config["configurable"]["actor_id"]
    thread_id = config["configurable"]["thread_id"]
    # Saving the message to the actor and session combination that we get at runtime
    namespace = (actor_id, thread_id)

    messages = state.get("messages", [])
    # Save the last human message we see before LLM invocation
    last_human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
    if last_human is None:
        return {"llm_input_messages": messages}
    store.put(namespace, str(uuid.uuid4()), {"message": last_human})

    # Retrieve user preferences based on that message. The store turns this tuple into
    # the string "/user-1/preferences/" and calls retrieve_memories for us — it matches
    # the strategy's namespace template "/{actorId}/preferences/".
    user_preferences_namespace = (actor_id, "preferences/")
    preferences = store.search(user_preferences_namespace, query=last_human.text, limit=5)

    # Add the recalled preferences to the conversation as context for the model.
    if preferences:
        context_items = [pref.value for pref in preferences]
        context_message = AIMessage(content=f"[User Context: {', '.join(str(item) for item in context_items)}]")
        # Returning "messages" writes to the state permanently (the context message stays
        # in the thread's history). Note that `add_messages` merges by message id rather
        # than by position, so the messages already in state keep their order and the new
        # context message lands at the END of the list, not before the human turn. For an
        # ephemeral, prompt-only injection, return {"llm_input_messages": [...]} instead.
        return {"messages": [context_message]}

    return {"llm_input_messages": messages}


def post_model_hook(state, config: RunnableConfig, *, store: BaseStore):
    """Hook that runs post-LLM invocation to save the latest human message"""
    actor_id = config["configurable"]["actor_id"]
    thread_id = config["configurable"]["thread_id"]

    # Saving the message to the actor and session combination that we get at runtime
    namespace = (actor_id, thread_id)

    messages = state.get("messages", [])
    # Save the LLMs response to AgentCore Memory
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
    store=store,  # LONG-TERM: preferences about this user
    tools=[],  # No additional tools needed for this example
    checkpointer=checkpointer,  # SHORT-TERM: this conversation's state
    pre_model_hook=pre_model_hook,  # Retrieves user preferences before LLM call
    post_model_hook=post_model_hook,  # Saves conversation after LLM response
)


# ## Step 6: Configure Agent Runtime
#
# We need to configure the agent with unique identifiers for the user and session. These IDs are crucial for memory organization and retrieval.
#
# ### Graph Invoke Input
# We only need to pass the newest user message in as an argument `inputs`. This could include other state variables as well but for the simple `create_react_agent`, we only need messages.
#
# ### LangGraph RuntimeConfig
# In LangGraph, config is a `RuntimeConfig` that contains attributes that are necessary at invocation time, for example user IDs or session IDs. For instance, your AgentCore invocation endpoint could assign these based on the identity or user ID of the caller. You can read additional [documentation here](https://langchain-ai.github.io/langgraphjs/how-tos/configuration/)
#
# Three things read this config, and all of them need it:
#
# - **The hooks** in this script use `actor_id` and `thread_id` as the store namespace
#   `(actor_id, thread_id)`. That pair becomes the AgentCore `actorId` and `sessionId` on
#   every event the store writes.
# - **The store** resolves its search namespace from `actor_id`.
# - **The checkpointer.** `AgentCoreMemorySaver` needs BOTH `thread_id` and `actor_id` and
#   raises `InvalidConfigError` without them. (LangGraph's `InMemorySaver` needs only
#   `thread_id` — another reason the two are not drop-in swaps.)


# `actor_id` is stable — it is the user, and long-term preferences are keyed on it.
# Session ids get a per-run suffix on purpose: now that the checkpointer is durable, a
# hardcoded "session-1" would make every re-run RESUME the previous run's conversation
# instead of starting the fresh one this tutorial narrates. Real applications get this for
# free — a new conversation is a new session id.
RUN = uuid.uuid4().hex[:8]

actor_id = "user-1"
config = {
    "configurable": {
        "thread_id": f"session-1-{RUN}",  # REQUIRED: the hooks use it as the AgentCore session_id
        "actor_id": actor_id,  # REQUIRED: the hooks use it as the AgentCore actor_id
    }
}


# ## Step 7: Test the Agent
#
# Let's test our nutrition assistant by having a conversation about food preferences. The agent will automatically extract and store user preferences for future use.


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
# As you can see, the model does not yet have any insight into our preferences or dietary restrictions.
#
# For this implementation with pre/post model hooks, two messages were stored here. The first message from the user and the response from the AI model were both stored as conversational events in AgentCore Memory. It may take a few moments for the long term memories to be extracted, so retry after a few seconds if nothing is found the first try.
#
# These messages were then extracted to AgentCore long term memory in our fact and user preferences namespaces. In fact, we can check the store ourselves to verify what has been stored there so far:


# Search our user preferences namespace
search_namespace = (actor_id, "preferences/")
result = store.search(search_namespace, query="food", limit=3)
print(f"Preferences namespace result: {result}")


# ### Agent access to the store
#
# **Note** - since AgentCore memory processes these events in the background, it may take a few seconds for the memory to be extracted and embedded to long term memory retrieval.
#
# Great! Now we have seen that long term memories were extracted to our namespaces based on the earlier messages in the conversation.
#
# Now, let's start a new session and ask about recommendations for what to cook for dinner. The agent can use the store to access the long term memories that were extracted to make a recommendation that the user will be sure to like.


# A NEW thread_id, the SAME actor_id. This is the split between the two halves of memory:
#   - New thread_id  -> the checkpointer has no state for it, so the graph starts empty.
#                       Nothing from the previous conversation is in the message history.
#   - Same actor_id  -> the store still finds the preferences extracted a moment ago.
# So the agent has forgotten the conversation but not the user.
config = {
    "configurable": {
        "thread_id": f"session-2-{RUN}",  # New session ID -> fresh checkpoint
        "actor_id": actor_id,  # Same actor ID  -> same long-term preferences
    }
}

run_agent("Today's a new day, what should I make for dinner tonight?", config)


# ### Proving the checkpointer works
#
# The store gave us cross-session recall above. The checkpointer gives us something
# different: WITHIN this second session, the agent remembers what was just said. Re-invoking
# with the SAME thread_id resumes the conversation rather than starting over — and because
# the checkpoint lives in AgentCore Memory rather than this process's heap, a different
# process or host could pick it up.


run_agent("Actually, what did I just ask you?", config)


# ### Wrapping up
#
# This script wired both halves of memory over ONE memory resource:
#
# | Argument | Class | Remembers | Proven by |
# |---|---|---|---|
# | `store=` | `AgentCoreMemoryStore` | this USER, across sessions | session-2 recalling session-1's preferences |
# | `checkpointer=` | `AgentCoreMemorySaver` | this CONVERSATION | the follow-up turn above resolving "what did I just ask you?" |
#
# The store needs a strategy (the user-preference custom override configured at the top)
# because AgentCore has to extract records from the conversation. The checkpointer needs no
# strategy: it writes opaque blobs that no strategy reads, which is why both can share one
# memory_id.
#
# The AgentCoreMemoryStore is also flexible about what drives it — pre/post model hooks as
# used here, v1.0 middleware (`@dynamic_prompt` / `@after_model`, see ../../01-built-in-callback/),
# or tools the model calls itself (see ../../03-memory-as-tool/).
