#!/usr/bin/env python

# # Safeguarding conversations with Amazon Bedrock Guardrails and AgentCore Memory
#
# ## Overview
#
# This tutorial demonstrates how to integrate Amazon Bedrock Guardrails with AgentCore Memory to create a secure conversational agent. You'll build an agent that filters sensitive content while maintaining conversation context across interactions.
#
# ### Tutorial Details
#
# | Information         | Details                                                          |
# |:--------------------|:-----------------------------------------------------------------|
# | Tutorial type       | Guardrails / Memory Integration                                  |
# | Agent type          | Safeguarded Memory-Enabled Agent                                 |
# | Agentic Framework   | Strands Agents                                                   |
# | LLM model           | Anthropic Claude Haiku 4.5                                      |
# | Key features        | Guardrails, Memory Integration, Content Filtering                |
# | Example complexity  | Intermediate                                                     |
# | SDK used            | Amazon Bedrock Python SDK and Bedrock Memory SDK                 |
#
# ### What You'll Learn
#
# In this tutorial, you'll learn:
# 1. How to create a memory resource for your agent
# 2. How to implement Amazon Bedrock Guardrails with content filtering
# 3. How to build a custom hook that combines guardrails and memory functionality
# 4. How to selectively store safe conversation history
# 5. How to test your secure agent with different types of content
#
# ### Architecture
#
# This example demonstrates the integration of guardrails with memory for secure conversations:
#
# <div style="text-align:left">
#     <img src="guardrails_memory_flow.png" width="90%"/>
# </div>
#
# ## 0. Prerequisites
#
# To execute this tutorial you will need:
# * Python 3.10+
# * AWS credentials configured with access to AgentCore Memory and Amazon Bedrock
# * Amazon Bedrock model access (Claude Haiku 4.5)
# * Amazon Bedrock Memory SDK
#
# First, let's install the required libraries:


# Run: pip install -qr requirements.txt


# Imports
import os
import boto3
import uuid
import logging
from typing import Dict
from strands import Agent
from bedrock_agentcore.memory import MemoryClient, MemorySessionManager
from botocore.exceptions import ClientError
from strands.hooks import HookProvider, HookRegistry
from strands.experimental.hooks import AfterModelInvocationEvent
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig

# Configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("secure-agent")
REGION = os.getenv("AWS_REGION", "us-west-2")  # AWS region for the agent
bedrock_client = boto3.client("bedrock", region_name=REGION)
bedrock_runtime_client = boto3.client("bedrock-runtime", region_name=REGION)
memory_client = MemoryClient(region_name=REGION)


# ## 1. Creating Amazon Bedrock Guardrails
#
# In this section, we'll create a guardrail to enforce content safety policies for our agent. Guardrails act as safety filters that can be applied to both user inputs and model outputs. For our example, we'll create a guardrail with two specific policies:
#
# 1. **Input Filtering**: Block insulting language from users
# 2. **Output Filtering**: Prevent the model from discussing political topics
#
# This approach demonstrates how guardrails can protect against different types of problematic content from both directions of the conversation. The input filter helps maintain a respectful conversation environment, while the output filter ensures the model doesn't discuss potentially sensitive topics.
#
# The end goal is to prevent saving unwanted messages in our memory, ensuring that only appropriate content is stored for future context.


# Unique identifier for this request
unique_id = str(uuid.uuid4())[:6]

# Define guardrail configuration
guardrail_name = f"SecureConversationGuardrail_{unique_id}"
guardrail_description = "Blocks insults in input and political content in output"

try:
    # Create the guardrail
    response = bedrock_client.create_guardrail(
        name=guardrail_name,
        description=guardrail_description,
        # Block insults in input
        contentPolicyConfig={
            "filtersConfig": [
                {
                    "type": "INSULTS",
                    "inputStrength": "MEDIUM",
                    "outputStrength": "MEDIUM",
                    "inputModalities": ["TEXT"],
                    "outputModalities": ["TEXT"],
                    "inputAction": "BLOCK",
                    "outputAction": "NONE",
                    "inputEnabled": True,
                    "outputEnabled": False,
                }
            ],
            "tierConfig": {"tierName": "CLASSIC"},
        },
        # Block political content in output
        topicPolicyConfig={
            "topicsConfig": [
                {
                    "name": "Politics",
                    "definition": "Content related to political leaders, elections, political parties, or government affairs",
                    "examples": [
                        "Who is the current president?",
                        "Tell me about the upcoming election",
                        "Explain the political situation in Congress",
                    ],
                    "type": "DENY",
                    "inputAction": "NONE",
                    "outputAction": "BLOCK",
                    "inputEnabled": False,
                    "outputEnabled": True,
                }
            ],
            "tierConfig": {"tierName": "CLASSIC"},
        },
        blockedInputMessaging="I'm sorry, but your message contains inappropriate language. Please rephrase your question without insults.",
        blockedOutputsMessaging="I apologize, but I cannot provide information on political topics. Is there something else I can help you with?",
    )

    # Store guardrail ID for later use
    guardrail_id = response["guardrailId"]
    guardrail_arn = response["guardrailArn"]
    guardrail_version = "DRAFT"  # New guardrails are created as DRAFT

    print(f"✅ Created guardrail: {guardrail_id} (ARN: {guardrail_arn})")

except Exception as e:
    print(f"❌ Error creating guardrail: {e}")
    # If the guardrail already exists, try to find its ID
    try:
        response = bedrock_client.list_guardrails()
        existing_guardrail = next(
            (g for g in response["guardrailSummaries"] if g["name"] == guardrail_name),
            None,
        )
        if existing_guardrail:
            guardrail_id = existing_guardrail["guardrailId"]
            guardrail_version = "DRAFT"  # Use DRAFT version
            print(f"Using existing guardrail: {guardrail_id}")
    except Exception as list_error:
        print(f"❌ Error listing guardrails: {list_error}")
        guardrail_id = None
        guardrail_version = None


# ## 2. Creating Memory Resource
#
# In this section, we'll create a memory resource for our agent to store conversation history. Memory allows the agent to recall past interactions, maintain context, and provide more coherent responses over time. By combining memory with guardrails, we can ensure that only appropriate content is stored for future reference.
#
# For this example, we'll create a simple short-term memory resource without any additional strategies, which is perfect for maintaining conversation context within a session. The memory will store messages that have passed our guardrail checks, ensuring that inappropriate content is filtered out.


memory_name = f"SecureAgentMemory_{unique_id}"

try:
    # Create memory resource without strategies (thus only access to short-term memory)
    memory = memory_client.create_memory_and_wait(
        name=memory_name,
        strategies=[],  # No strategies for short-term memory
        description="Short-term memory for personal agent with guardrails",
        event_expiry_days=7,
    )
    memory_id = memory["id"]
    logger.info(f"✅ Created memory: {memory_id}")
except ClientError as e:
    logger.info(f"❌ ERROR: {e}")
    if e.response["Error"]["Code"] == "ValidationException" and "already exists" in str(e):
        # If memory already exists, retrieve its ID
        memories = memory_client.list_memories()
        memory_id = next((m["id"] for m in memories if m["id"].startswith(memory_name)), None)
        logger.info(f"Memory already exists. Using existing memory ID: {memory_id}")
except Exception as e:
    # Show any errors during memory creation
    logger.error(f"❌ ERROR: {e}")
    import traceback

    traceback.print_exc()
    # Cleanup on error - delete the memory if it was partially created
    if "memory_id" in locals() and memory_id:
        try:
            memory_client.delete_memory_and_wait(memory_id=memory_id)
            logger.info(f"Cleaned up memory: {memory_id}")
        except Exception as cleanup_error:
            logger.error(f"Failed to clean up memory: {cleanup_error}")


# ## 3. Integrating Bedrock Guardrails, Strands and Agentcore Memory
#
# In this section, we'll create custom hooks that integrate guardrails with memory functionality. Our implementation will:
#
# 1. Check both user inputs and model outputs using Amazon Bedrock Guardrails
# 2. Replace inappropriate content with safe alternatives
# 3. Only store messages in memory that have passed our guardrail checks
# 4. Retrieve past conversation context from memory when the agent is initialized
#
# This approach ensures that our agent maintains a clean conversation history while still benefiting from memory capabilities. Let's build the necessary components:


class GuardrailsEvaluator:
    """Reusable guardrails evaluation utility."""

    def __init__(self, guardrail_id: str, guardrail_version: str):
        """Initialize the guardrails evaluator.

        Args:
            guardrail_id: The ID of the guardrail to use
            guardrail_version: The version of the guardrail (e.g., "DRAFT")
        """
        self.guardrail_id = guardrail_id
        self.guardrail_version = guardrail_version

    def evaluate_content(self, content: str, source: str) -> Dict:
        """Evaluate content using Bedrock Guardrails and return result.

        Args:
            content: The text content to evaluate
            source: The source type ("INPUT" or "OUTPUT")

        Returns:
            Dict containing guardrail evaluation results
        """
        try:
            logger.info(f"⏳ CHECKING {source}: '{content[:30]}...'")

            response = bedrock_runtime_client.apply_guardrail(
                guardrailIdentifier=self.guardrail_id,
                guardrailVersion=self.guardrail_version,
                source=source,
                content=[{"text": {"text": content}}],
            )

            action = response.get("action")
            logger.info(f"🔍 GUARDRAIL ACTION: {action}")

            return response
        except Exception as e:
            logger.error(f"❌ Guardrail evaluation failed: {e}")
            return {"error": str(e)}


class GuardrailsHookProvider(HookProvider):
    """Hook provider that combines guardrails enforcement with memory storage."""

    def __init__(self, guardrails_evaluator: GuardrailsEvaluator):
        self.evaluator = guardrails_evaluator
        self.blocked_outputs = set()

    def after_model_invocation(self, event: AfterModelInvocationEvent) -> None:
        """Check model output with guardrails and replace if needed.

        Args:
            event: Event containing the model response
        """
        # Skip if model invocation failed
        if event.exception is not None or event.stop_response is None:
            logger.error("⚠️ Model invocation failed, skipping guardrail check")
            return

        logger.info("🔍 AfterModelInvocationEvent: Checking model output")

        # Extract message from the model response
        message = event.stop_response.message

        # Extract content
        if isinstance(message.get("content"), list):
            content = "".join(block.get("text", "") for block in message.get("content", []))
        else:
            content = str(message.get("content", ""))

        content_id = hash(content)

        # Check against guardrails
        result = self.evaluator.evaluate_content(content, "OUTPUT")

        # Handle guardrail violations
        if result.get("action") == "GUARDRAIL_INTERVENED":
            logger.warning("⛔ ASSISTANT MESSAGE BLOCKED BY GUARDRAILS")

            # Mark this output as blocked
            self.blocked_outputs.add(content_id)

            # Get the guardrail-provided alternative if available
            replacement_content = None
            if "outputs" in result and result["outputs"] and len(result["outputs"]) > 0:
                if "text" in result["outputs"][0]:
                    replacement_content = result["outputs"][0]["text"]

            # Fall back to generic message if no replacement provided
            if not replacement_content:
                replacement_content = "I apologize, but I cannot provide the requested information as it would violate our content policies."

            # Update the message content - THIS WILL CHANGE WHAT THE USER SEES
            if isinstance(message.get("content"), list):
                message["content"] = [{"text": replacement_content}]
            else:
                message["content"] = replacement_content

            logger.info(f"⚠️ Replaced assistant message with guardrail response: {replacement_content[:30]}...")

    def register_hooks(self, registry: HookRegistry):
        """Register all hooks with the registry.

        Args:
            registry: The hook registry to register with
        """
        registry.add_callback(AfterModelInvocationEvent, self.after_model_invocation)


# ## 4. Creating and configuring the Agent
#
# In this section, we'll create our secure conversational agent by combining all the components we've built: the Bedrock model, guardrails evaluator, and memory-enabled hook provider. This integration creates a complete agent that can maintain conversations while enforcing content policies and storing appropriate context.


ACTOR_ID = "user_1"
SESSION_ID = "session_001"
# bedrock_model = BedrockModel(model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0")

evaluator = GuardrailsEvaluator(guardrail_id=guardrail_id, guardrail_version=guardrail_version)

session_manager = None


def create_personal_agent():
    """Create personal agent with memory and guardrails"""
    global session_manager
    # Close previous session manager if it exists
    if session_manager is not None:
        session_manager.close()

    # Configure AgentCore Memory
    config = AgentCoreMemoryConfig(memory_id=memory_id, session_id=SESSION_ID, actor_id=ACTOR_ID)

    # Create session manager (explicit lifecycle — closed in cleanup cell)
    session_manager = AgentCoreMemorySessionManager(agentcore_memory_config=config, region_name=REGION)

    # Create agent with session manager and guardrails hook
    agent = Agent(
        name="PersonalAssistant",
        model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
        system_prompt="You are a helpful personal assistant. Be friendly and professional.",
        session_manager=session_manager,
        hooks=[GuardrailsHookProvider(evaluator)],
        callback_handler=None,
    )
    return agent


# Create agent
agent = create_personal_agent()
logger.info("✅ Personal agent created with memory and guardrails")


# This implementation creates a secure agent that will:
#
# 1. Load existing conversation context from memory when initialized
# 2. Check user inputs against guardrails before processing
# 3. Check model outputs against guardrails before showing to the user
# 4. Store only approved messages in memory for future context
# 5. Maintain conversation history across multiple interactions
#
# The combination of guardrails and memory ensures that our agent maintains a secure but contextual conversation experience.

# ## 5. Testing the Secure Agent
#
# Let's test our agent with different types of input to see how the guardrails and memory integration work in practice. We'll try both acceptable inputs and those that might trigger guardrail interventions to verify our implementation is working correctly.
#
# First, let's create a helper function to handle the guardrail check and agent invocation:


def process_with_guardrails(user_input):
    """Process user input with guardrails before sending to agent.

    Args:
        user_input: The text input from the user

    Returns:
        The agent response or guardrail rejection
    """
    # Check input against guardrails
    result = evaluator.evaluate_content(user_input, "INPUT")

    if result.get("action") == "GUARDRAIL_INTERVENED":
        # Get rejection message from guardrail
        if "outputs" in result and result["outputs"] and "text" in result["outputs"][0]:
            rejection_content = result["outputs"][0]["text"]
        else:
            rejection_content = "I cannot process that request."

        # Return rejection without calling agent
        print(rejection_content)
        return rejection_content
    else:
        # Input passed guardrails, proceed with agent call
        response = agent(user_input)
        print(response)
        return response


# ### Test 1: Normal Conversation
#
# Let's start with a normal greeting that should pass all guardrails:


print("Test 1: Normal greeting")
user_input = "I am dani."
process_with_guardrails(user_input)


# ### Test 2: Insulting Content (Should Trigger Input Guardrail)
#
# Let's try input with insulting language that should be blocked by the input guardrail:


print("\nTest 2: Insulting content (should trigger input guardrail)")
user_input = "You're a stupid assistant."
process_with_guardrails(user_input)


# ### Test 3: Political Content (Should Trigger Output Guardrail)
#
# Now let's try a question about politics, which should pass the input guardrail but trigger the output guardrail:


print("\nTest 3: Political question (should trigger output guardrail)")
user_input = "Who is the president of the US?"
process_with_guardrails(user_input)


# ### Examining Memory Contents
#
# Let's check what was stored in memory after our tests:


# Check what's stored in memory
print("\n=== Memory Contents ===")
manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)
session = manager.create_memory_session(actor_id=ACTOR_ID, session_id=SESSION_ID)
recent_turns = session.get_last_k_turns(k=5)


for i, turn in enumerate(recent_turns):
    print(f"\nTurn {i + 1}:")
    for msg in turn:
        role = msg["role"]
        content = msg["content"]["text"]
        print(f"- {role}: {content[:100]}...")


# ### Test 4: Follow-Up Question to Test Memory
# Let's ask a follow-up question to see if the agent remembers previous context:


agent = create_personal_agent()
print("\nTest 4: Follow-up to test memory")
user_input = "What's my name?"
process_with_guardrails(user_input)


# ## 6. Cleanup (Optional)
#
# When you're done experimenting with your secure agent, you may want to clean up the resources created in this tutorial. This section shows you how to delete the guardrail and memory resources.


# Close the session manager to flush any buffered messages
if session_manager is not None:
    session_manager.close()
    print("✅ Closed session manager")

# Delete the memory resource
try:
    memory_client.delete_memory_and_wait(memory_id=memory_id)
    print(f"✅ Deleted memory resource: {memory_id}")
except Exception as e:
    print(f"❌ Error deleting memory: {e}")

# Delete the guardrail
try:
    bedrock_client.delete_guardrail(guardrailIdentifier=guardrail_id)
    print(f"✅ Deleted guardrail: {guardrail_id}")
except Exception as e:
    print(f"❌ Error deleting guardrail: {e}")


# ## Conclusion
#
# In this tutorial, we've built a secure conversational agent that combines Amazon Bedrock Guardrails with AgentCore Memory capabilities. Our implementation:
#
# 1. Filters inappropriate user inputs using guardrails
# 2. Prevents the agent from discussing sensitive topics
# 3. Only stores approved messages in memory
# 4. Maintains conversation context using memory for enhanced user experience
#
# By integrating guardrails with memory, you can build robust agents that maintain compliance with content policies while still providing personalized and contextual responses. This pattern can be extended to more complex scenarios by adding additional guardrail filters or implementing more long term memory strategies.
