#!/usr/bin/env python

# # LangGraph with AgentCore Memory Tool (Short term memory)

# ## Introduction
# This notebook demonstrates how to integrate Amazon Bedrock AgentCore Memory capabilities with a conversational AI agent using LangGraph framework. We'll focus on **short-term memory** retention within a single conversation session - allowing an agent to recall information from earlier in the conversation without explicit context management.
#
#
# ## Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Short Term Conversational                                                        |
# | Agent usecase       | Personal Fitness                                                                 |
# | Agentic Framework   | Langgraph                                                                        |
# | LLM model           | Anthropic Claude Haiku 4.5                                                      |
# | Tutorial components | AgentCore Short-term Memory, Langgraph, Memory retrieval via Tool                |
# | Example complexity  | Beginner                                                                         |
#
# You'll learn to:
# - Create a memory store with AgentCore Memory for short-term memory
# - Use LangGraph to create an agent with structured memory workflows
# - Implement memory tools for conversation history retrieval
# - Access and utilize contextual information within a single session
# - Enhance conversational experiences through effective memory recall
#
#
# ### Scenario Context
#
# In this example, we'll create a "**Personal Fitness Coach**" that can remember workout details, fitness goals, physical limitations, and exercise preferences as they are mentioned throughout the conversation. This assistant will demonstrate how effective short-term memory management enables a more natural and personalized fitness coaching experience without requiring users to repeatedly state their information.
#
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
# Let's get started by setting up our environment!

# ## Step 1: Environment Setup
# Let's begin importing all the necessary libraries and defining the clients to make this notebook work.


# Run: pip install -qr requirements.txt


import logging
from datetime import datetime


# Define the region and the role with the appropiate permissions for Amazon Bedrock models and AgentCore


import os

region = os.getenv("AWS_REGION", "us-west-2")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("agentcore-memory")


# ### How the Integration Works
#
# The integration between LangGraph and AgentCore Memory involves:
#
# 1. Using AgentCore Memory to store conversations in the short term memory
# 2. Structured workflows in LangGraph to manage memory operations
#
# This approach separates memory management from reasoning, creating a cleaner and more maintainable agent architecture.

# ## Step 2: Memory Creation
# In this section, we'll create a memory store using the AgentCore Memory SDK. This memory store will allow our agent to retain information from the conversation.


from bedrock_agentcore.memory import MemoryClient  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402


client = MemoryClient(region_name=region)
memory_name = "FitnessCoach"
memory_id = None


try:
    print("Creating Memory...")
    # Create the memory resource
    memory = client.create_memory_and_wait(
        name=memory_name,  # This name is unique across all memories in this account
        description="Fitness Coach Agent",  # Human-readable description
        strategies=[],  # No memory strategies for short-term memory
        event_expiry_days=7,  # Memories expire after 7 days
        max_wait=300,  # Maximum time to wait for memory creation (5 minutes)
        poll_interval=10,  # Check status every 10 seconds
    )

    # Extract and print the memory ID
    memory_id = memory["id"]
    logger.info(f"Memory created successfully with ID: {memory_id}")
except ClientError as e:
    if e.response["Error"]["Code"] == "ValidationException" and "already exists" in str(e):
        # If memory already exists, retrieve its ID
        memories = client.list_memories()
        memory_id = next((m["id"] for m in memories if m["id"].startswith(memory_name)), None)
        logger.info(f"Memory already exists. Using existing memory ID: {memory_id}")
except Exception as e:
    # Handle any errors during memory creation
    logger.info(f"❌ ERROR: {e}")
    import traceback

    traceback.print_exc()
    # Cleanup on error - delete the memory if it was partially created
    if memory_id:
        try:
            client.delete_memory_and_wait(memory_id=memory_id)
            logger.info(f"Cleaned up memory: {memory_id}")
        except Exception as cleanup_error:
            logger.info(f"Failed to clean up memory: {cleanup_error}")


# ## Step 3: LangGraph Agent Creation
# Let's import all the libraries we need to create the agent with LangGraph.


from langgraph.graph import StateGraph, MessagesState  # noqa: E402
from langgraph.prebuilt import ToolNode, tools_condition  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_aws import ChatBedrock  # noqa: E402


# ### LangGraph Agent Implementation
#
# Now let's create the agent with LangGraph, incorporating our memory tools:


def create_agent(client, memory_id, actor_id, session_id):
    """Create and configure the LangGraph agent"""

    # Initialize your LLM (adjust model and parameters as needed)
    llm = ChatBedrock(
        model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",  # or your preferred model
        model_kwargs={"temperature": 0.1},
    )

    @tool
    def list_events():
        """Tool used when needed to retrieve recent information"""
        events = client.list_events(
            memory_id=memory_id,
            actor_id=actor_id,
            session_id=session_id,
            max_results=10,
        )
        return events

    # Bind tools to the LLM
    tools = [list_events]
    llm_with_tools = llm.bind_tools(tools)

    # System message
    system_message = """You are the Personal Fitness Coach, a sophisticated fitness guidance assistant.
                        PURPOSE:
                        - Help users develop workout routines based on their fitness goals
                        - Remember user's exercise preferences, limitations, and progress
                        - Provide personalized fitness recommendations and training plans
                        MEMORY CAPABILITIES:
                        - You have access to recent events with the list_events tool
                        """

    # Define the chatbot node
    def chatbot(state: MessagesState):
        raw_messages = state["messages"]

        # Remove any existing system messages to avoid duplicates or misplacement
        non_system_messages = [msg for msg in raw_messages if not isinstance(msg, SystemMessage)]

        # Always ensure SystemMessage is first
        messages = [SystemMessage(content=system_message)] + non_system_messages

        latest_user_message = next(
            (msg.content for msg in reversed(messages) if isinstance(msg, HumanMessage)),
            None,
        )

        # Get response from model with tools bound
        response = llm_with_tools.invoke(messages)

        # Save conversation if applicable
        if latest_user_message and response.content.strip():  # Check that response has content
            conversation = [
                (latest_user_message, "USER"),
                (response.content, "ASSISTANT"),
            ]

            # Validate that all message texts are non-empty
            if all(msg[0].strip() for msg in conversation):  # Ensure no empty messages
                try:
                    client.create_event(
                        memory_id=memory_id,
                        actor_id=actor_id,
                        session_id=session_id,
                        messages=conversation,
                    )
                except Exception as e:
                    print(f"Error saving conversation: {str(e)}")

        # Append response to full message history
        return {"messages": raw_messages + [response]}

    # Create the graph
    graph_builder = StateGraph(MessagesState)

    # Add nodes
    graph_builder.add_node("chatbot", chatbot)
    graph_builder.add_node("tools", ToolNode(tools))

    # Add edges
    graph_builder.add_conditional_edges(
        "chatbot",
        tools_condition,
    )
    graph_builder.add_edge("tools", "chatbot")

    # Set entry point
    graph_builder.set_entry_point("chatbot")

    # Compile the graph
    return graph_builder.compile()


# ### Creating a Wrapper for Agent Invocation
#
# Let's create a simple wrapper to invoke our agent:


def langgraph_bedrock(payload, agent):
    """
    Invoke the agent with a payload
    """
    user_input = payload.get("prompt")

    # Create the input in the format expected by LangGraph
    response = agent.invoke({"messages": [HumanMessage(content=user_input)]})

    # Extract the final message content
    return response["messages"][-1].content


# ## Step 4: Run the LangGraph Agent
# We can now run the agent with our AgentCore Memory integration.


# Create unique actor and session IDs for this conversation
actor_id = f"user-{datetime.now().strftime('%Y%m%d%H%M%S')}"
session_id = f"workout-{datetime.now().strftime('%Y%m%d%H%M%S')}"


# Create the agent with AgentCore Memory integration
agent = create_agent(client, memory_id, actor_id, session_id)


# #### Congratulations ! Your Agent is ready !!
#
# ### Let's test the Agent
#
# Let's interact with our agent to test its memory capabilities:


response = langgraph_bedrock({"prompt": "Hello! This is my first day, I need a workout routine."}, agent)
print(f"Agent: {response}\n")


response = langgraph_bedrock(
    {"prompt": "I want to build muscle, looking for a biceps routine. I have some lower back problems."},
    agent,
)
print(f"Agent: {response}\n")


response = langgraph_bedrock({"prompt": "Can you give me three exercises with number of reps?"}, agent)
print(f"Agent: {response}\n")


# ### Testing Memory Persistence
#
# To truly demonstrate the power of the AgentCore Memory integration, let's create a new agent instance and see if it can recall our previous conversation:


# Create a new agent instance (simulating a new session)
new_agent = create_agent(client, memory_id, actor_id, session_id)

# Test if the new agent remembers our preferences
response = langgraph_bedrock(
    {"prompt": "Hello again! Can you remind me about my last workout session?"},
    new_agent,
)

print("New Agent Session:\n")
print(f"Agent: {response}")


# ## Summary
#
# In this notebook, we've demonstrated:
#
# 1. How to create a AgentCore Memory resource for an AI agent
# 2. Building a LangGraph workflow with memory integration
# 3. Implementing memory tools for conversation history retrieval
# 4. Creating an agent that intelligently uses memory when needed
# 5. Testing memory persistence across agent instances
#
# This integration showcases the power of combining structured workflows (LangGraph) with robust memory systems (AgentCore Memory) to create more intelligent and context-aware AI agents.
#
# The approach we've demonstrated can be extended to more complex use cases, including multi-agent systems, long-term memory with extraction strategies, and specialized memory retrieval based on conversation context.

# ## Clean up
# Let's delete the memory to clean up the resources used in this notebook.


# client.delete_memory_and_wait(memory_id = memory_id, max_wait = 300, poll_interval =10)
