# # LangGraph with AgentCore Memory - Human in the Loop (Short term memory)
#
# ## Introduction
# This notebook demonstrates how to integrate Amazon Bedrock AgentCore Memory capabilities with LangGraph to create **human-in-the-loop** workflows. We'll focus on **short-term memory** persistence combined with the ability to interrupt agent execution for human intervention, creating sophisticated customer support scenarios with seamless handoffs.
#
# ## Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Short Term Conversational                                                        |
# | Agent usecase       | Customer Support with Human Escalation                                          |
# | Agentic Framework   | Langgraph                                                                        |
# | LLM model           | Anthropic Claude Haiku 4.5                                                      |
# | Tutorial components | AgentCore Short-term Memory, Langgraph Checkpointer, Human-in-the-Loop        |
# | Example complexity  | Beginner                                                                     |
#
# You'll learn to:
# - Create a memory checkpointer with AgentCore Memory for workflow persistence
# - Use LangGraph's interrupt mechanism for human-in-the-loop workflows
# - Implement tools that can pause execution for human intervention
# - Resume agent workflows after human input using LangGraph Commands
# - Manage complex customer support scenarios with seamless handoffs
#
# ### Scenario Context
#
# In this example, we'll create a "**Customer Support Agent**" that can escalate complex issues to human supervisors. When the agent encounters situations requiring human expertise, it will pause execution, save the current state to AgentCore Memory, and wait for human intervention. The human supervisor can then provide guidance, and the agent will resume with the enhanced context.
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
# The integration between LangGraph and AgentCore Memory for human-in-the-loop workflows involves:
#
# 1. Using AgentCore Memory as a checkpointer backend for persistent state management
# 2. Implementing interrupt mechanisms that pause execution at specific points
# 3. Enabling human supervisors to resume workflows with additional context
# 4. Maintaining conversation history and state across interruptions
#
# This approach creates support workflows where AI agents and human supervisors work together seamlessly.
#
# Let's get started by setting up our environment!


# Install necessary libraries
# Run: pip install -qr requirements.txt


import logging
import os
import uuid

from bedrock_agentcore.memory import MemoryClient

# Import LangGraph and LangChain components
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langgraph.prebuilt import create_react_agent

# Imports that enable human-in-the-loop implementation
from langgraph.types import Command, interrupt

# AgentCoreMemorySaver is the checkpointer: it saves the graph's state to AgentCore
# Memory after every step, including the paused state of an `interrupt()`. On resume it
# restores the graph exactly as it was left, so the human's answer flows back into the
# tool call that was waiting for it.
from langgraph_checkpoint_aws import AgentCoreMemorySaver

logging.getLogger("support-agent").setLevel(logging.INFO)
region = os.getenv("AWS_REGION", "us-west-2")

logger = logging.getLogger("support-agent")


# ## Step 1: Memory Creation
# In this section, we'll create a memory store using the AgentCore Memory SDK. This memory will serve as the backend for our LangGraph checkpointer and enable persistent human-in-the-loop workflows.


memory_name = "SupportAgent"

client = MemoryClient(region_name=region)
memory = client.create_or_get_memory(name=memory_name)
memory_id = memory["id"]


# ### AgentCore Memory Configuration
#
# Now let's configure our AgentCore Memory checkpointer and initialize the LLM:
#
# - `memory_id` corresponds to our AgentCore Memory resource where checkpoints will be stored
# - `region` specifies the AWS region for our resources
# - `MODEL_ID` defines the Bedrock model that will power our LangGraph agent
#
# We will use the `memory_id` and any additional boto3 client keyword args (in our case, `region`) to instantiate our checkpointer.


MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

# The paused state lives in AgentCore Memory, not in this process, so a different
# process or host can pick the conversation up and resume it.
checkpointer = AgentCoreMemorySaver(memory_id, region_name=region)


# ## Step 2: Human-in-the-Loop Tool
# Let's define the tools our support agent will use. Using the LangGraph `interrupt` type, we can interrupt the agent graph execution to give the chance for a human to intervene and respond to the query to continue execution.
#


@tool
def human_assistance(query: str) -> str:
    """Request assistance from a human."""
    human_response = interrupt({"query": query})
    # Resume value may be passed as a plain string or as {"data": "..."}
    if isinstance(human_response, dict):
        return human_response.get("data", str(human_response))
    return str(human_response)


@tool
def add(a: int, b: int):
    """Add two integers and return the result"""
    return a + b


@tool
def multiply(a: int, b: int):
    """Multiply two integers and return the result"""
    return a * b


tools = [add, multiply, human_assistance]


# ## Step 3: LangGraph Agent Implementation
#
# Now let's create our support agent using LangGraph's `create_react_agent` builder with our AgentCore Memory checkpointer and human-in-the-loop capabilities:


# Initialize LLM
llm = init_chat_model(MODEL_ID, model_provider="bedrock_converse", region_name=region)

graph = create_react_agent(
    model=llm,
    tools=tools,
    prompt="You are a helpful assistant",
    checkpointer=checkpointer,
)


# ## Step 4: Run the Support Agent
# We can now run the agent with our AgentCore Memory checkpointer and human-in-the-loop integration. For this example we will ask explicitly for user assistance. In reality, this could be triggered by several conditions, for example a safety flag may route a conversation to a human if certain keywords are used.
#
# ### Configuration Setup
# In LangGraph, config is a `RuntimeConfig` that contains attributes that are necessary at invocation time, for example user IDs or session IDs. You can [read additional information here](https://langchain-ai.github.io/langgraphjs/how-tos/configuration/](https://langchain-ai.github.io/langgraphjs/how-tos/configuration/).
#
# For the AgentCore Memory checkpointer (`AgentCoreMemorySaver`), we need to specify:
# - `thread_id`: Maps to AgentCore session_id (unique conversation thread)
# - `actor_id`: Maps to AgentCore actor_id (user, agent or other identifier)
#
# ### Graph Invoke Input
# We only need to pass the newest user message in as an argument `inputs`. This could include other state variables as well but for the simple `create_react_agent`, only messages are required.
#


user_input = "I would like to work with a customer service human agent."
config = {"configurable": {"thread_id": str(uuid.uuid4()), "actor_id": "demo-notebook"}}

events = graph.stream(
    {"messages": [{"role": "user", "content": user_input}]},
    config,
    stream_mode="values",
)
for event in events:
    if "messages" in event:
        event["messages"][-1].pretty_print()


# ### Workflow Interruption
#
# Notice how execution paused when the human assistance tool was called. Let's inspect the current state to see where the workflow stopped:


snapshot = graph.get_state(config)
print(f"Paused before: {snapshot.next}")


# ### Human Supervisor intervention
#
# Now let's act as the human supervisor and provide assistance to resume the workflow using the LangGraph `Command` to send our response. The AgentCore Memory checkpointer has preserved the entire conversation state, that will allow us to resume the chat.


human_response = "I'm sorry to hear that you are frustrated. Looking at the past conversation history, I can see that you've requested a refund. I've gone ahead and credited it to your account."

human_command = Command(resume={"data": human_response})

events = graph.stream(human_command, config, stream_mode="values")
for event in events:
    if "messages" in event:
        event["messages"][-1].pretty_print()


# ## Summary
#
# In this notebook, we've demonstrated:
#
# 1. How to create an AgentCore Memory resource for human-in-the-loop workflows
# 2. Building a LangGraph agent with interrupt capabilities
# 3. Implementing tools that can pause execution for human intervention
# 4. Using the AgentCoreMemorySaver to persist workflow state during interruptions
# 5. Resuming agent execution with human-provided context
#
# This integration showcases the power of combining LangGraph's human-in-the-loop capabilities with AgentCore Memory's robust state persistence to create sophisticated customer support workflows where AI agents and human supervisors work together seamlessly.
#
# The approach we've demonstrated can be extended to more complex scenarios, including multi-level escalations, specialized human expertise routing, and complex approval workflows.

# ## Clean up
# Let's delete the memory to clean up the resources used in this notebook.
#


# client.delete_memory_and_wait(memory_id = memory_id, max_wait = 300, poll_interval =10)
