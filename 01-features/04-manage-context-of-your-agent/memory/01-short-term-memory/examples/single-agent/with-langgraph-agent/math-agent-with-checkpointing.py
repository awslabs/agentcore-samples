# # LangGraph with AgentCore Memory Checkpointer (Short term memory)
#
# ## Introduction
# This notebook demonstrates how to integrate Amazon Bedrock AgentCore Memory capabilities with LangGraph using the **AgentCoreMemorySaver** checkpointer. We'll focus on **short-term memory** persistence across conversation turns - allowing an agent to maintain running context and build upon previous calculations through automatic state checkpointing.
#
# ## Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Short Term Conversational                                                        |
# | Agent usecase       | Multi-step Math Calculations                                                     |
# | Agentic Framework   | Langgraph                                                                        |
# | LLM model           | Anthropic Claude Haiku 4.5                                                      |
# | Tutorial components | AgentCore Short-term Memory, Langgraph Checkpointer, Math Tools                |
# | Example complexity  | Beginner                                                                         |
#
# You'll learn to:
# - Create a memory checkpointer with AgentCore Memory for automatic state persistence
# - Use LangGraph's built-in checkpointing system with AgentCore Memory backend
# - Maintain conversation context across multiple interactions
# - Inspect and manage conversation state and history
#
# ### Scenario Context
#
# In this example, we'll create a "**Math Agent**" that can perform multi-step mathematical calculations. Unlike simple one-off interactions, this agent uses AgentCore Memory's checkpointing capabilities to maintain running context, allowing it to build upon previous calculations and remember the conversation flow across multiple turns.
#
# ## Architecture
# <div style="text-align:left">
#     <img src="images/architecture.png" width="65%" />
# </div>
#
# ## Prerequisites
#
# - Python 3.10+
# - AWS account with appropriate permissions
# - AWS IAM role with appropriate permissions for AgentCore Memory
# - Access to Amazon Bedrock models
#
# ### How the Integration Works
#
# The integration between LangGraph and AgentCore Memory involves:
#
# 1. Using AgentCore Memory as a checkpointer backend for LangGraph state persistence
# 2. Automatic saving and loading of conversation state at each step
# 3. Support for multiple concurrent sessions and actors
#
# This approach provides seamless state management without requiring manual memory operations, creating a more maintainable and scalable agent architecture.


# Install necessary libraries
# Run: pip install -qr requirements.txt


import logging
import os

from bedrock_agentcore.memory import MemoryClient

# Import LangGraph and LangChain components
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langgraph.prebuilt import create_react_agent

# Import the AgentCoreMemorySaver that we will use as a checkpointer
from langgraph_checkpoint_aws import AgentCoreMemorySaver

region = os.getenv("AWS_REGION", "us-west-2")
logging.getLogger("math-agent").setLevel(logging.DEBUG)

# Create or get the memory resource
memory_name = "MathLanggraphAgent"
client = MemoryClient(region_name=region)
memory = client.create_or_get_memory(name=memory_name)
memory_id = memory["id"]  # Keep this memory ID for later use


# ### AgentCore Memory Configuration
#
# Now let's configure our AgentCore Memory checkpointer and initialize the LLM:
#
# - `memory_id` corresponds to our AgentCore Memory resource where checkpoints will be stored
# - `region` specifies the AWS region for our resources
# - `MODEL_ID` defines the Bedrock model that will power our LangGraph agent


MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

# Initialize checkpointer for state persistence
checkpointer = AgentCoreMemorySaver(memory_id, region_name=region)

# Initialize LLM
llm = init_chat_model(MODEL_ID, model_provider="bedrock_converse", region_name=region)


# ### Mathematical Tools
#
# Let's define the mathematical tools our agent will use. For this demonstration, we'll provide two simple operations:


@tool
def add(a: int, b: int):
    """Add two integers and return the result"""
    return a + b


@tool
def multiply(a: int, b: int):
    """Multiply two integers and return the result"""
    return a * b


tools = [add, multiply]


# ### LangGraph Agent Implementation
#
# Now let's create our agent using LangGraph's `create_react_agent` builder with our AgentCore Memory checkpointer:


graph = create_react_agent(
    model=llm,
    tools=tools,
    prompt="You are a helpful assistant",
    checkpointer=checkpointer,
)


# ## Step 4: Run the LangGraph Agent
# We can now run the agent with our AgentCore Memory checkpointer integration.
#
# ### Configuration Setup
# In LangGraph, config is a `RuntimeConfig` that contains attributes that are necessary at invocation time, for example user IDs or session IDs. You can read additional documentation here: [https://langchain-ai.github.io/langgraphjs/how-tos/configuration/](https://langchain-ai.github.io/langgraphjs/how-tos/configuration/)
#
# For the AgentCore Memory checkpointer (`AgentCoreMemorySaver`), we NEED to specify:
# - `thread_id`: Maps to AgentCore session_id (unique conversation thread)
# - `actor_id`: Maps to AgentCore actor_id (user, agent, or any other identifier)


config = {
    "configurable": {
        "thread_id": "session-1",  # REQUIRED: This maps to Bedrock AgentCore session_id under the hood
        "actor_id": "react-agent-1",  # REQUIRED: This maps to Bedrock AgentCore actor_id under the hood
    }
}

inputs = {
    "messages": [
        {
            "role": "user",
            "content": "What is 1337 times 515321? Then add 412 and return the value to me.",
        }
    ]
}


# #### Congratulations! Your Agent is ready!!
#
# ### Let's test the Agent
#
# Let's run our first calculation to see the agent in action:


for chunk in graph.stream(inputs, stream_mode="updates", config=config):
    print(chunk)


# ### Inspecting Agent State
#
# Let's examine the current conversation state stored in AgentCore Memory. The checkpointer automatically saves and retrieves state for our actor and session:


for message in graph.get_state(config).values.get("messages"):
    print(f"{message.type}: {message.text}")
    print("=========================================")


# ### Viewing Checkpoint History
#
# Let's explore the checkpoint history to see how the agent's state evolved during execution.  Checkpoints are listed in reverse chronological order (most recent appear first).


for checkpoint in graph.get_state_history(config):
    print(
        f"(Checkpoint ID: {checkpoint.config['configurable']['checkpoint_id']}) # of messages in state: {len(checkpoint.values.get('messages'))}"
    )


# ### Testing Memory Persistence
#
# Now let's test the power of our checkpointer by continuing the conversation. The agent should remember our previous calculations:


inputs = {
    "messages": [
        {
            "role": "user",
            "content": "What were the first calculations I asked you to do?",
        }
    ]
}

for chunk in graph.stream(inputs, stream_mode="updates", config=config):
    print(chunk)


# ### Starting a New Session
#
# Let's demonstrate session isolation by creating a new conversation thread. The agent won't remember the previous calculations in this new session:


config = {
    "configurable": {
        "thread_id": "session-2",  # New session ID
        "actor_id": "react-agent-1",  # Same Actor ID
    }
}

inputs = {"messages": [{"role": "user", "content": "What values did I ask you to multiply and add?"}]}
for chunk in graph.stream(inputs, stream_mode="updates", config=config):
    print(chunk)


# ## Summary
#
# In this notebook, we've demonstrated:
#
# 1. How to create an AgentCore Memory resource for checkpointing
# 2. Building a LangGraph agent with automatic state persistence
# 3. Implementing mathematical tools for multi-step calculations
# 4. Using the AgentCoreMemorySaver as a checkpointer backend
# 5. Testing memory persistence and session isolation
#
# This integration showcases the power of combining LangGraph's structured workflows with AgentCore Memory's robust checkpointing capabilities to create stateful, persistent AI agents that can maintain context across multiple interactions.
#
# The approach we've demonstrated can be extended to more complex use cases, including multi-agent systems, long-running workflows, and specialized state management based on conversation context.

# ### Clean up
# Let's delete the memory to clean up the resources used in this notebook.


# client.delete_memory_and_wait(memory_id = memory_id, max_wait = 300, poll_interval =10)
