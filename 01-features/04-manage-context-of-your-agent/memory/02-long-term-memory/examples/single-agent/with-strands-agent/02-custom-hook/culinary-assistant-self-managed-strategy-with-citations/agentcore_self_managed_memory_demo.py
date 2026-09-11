#!/usr/bin/env python

# # AgentCore Self-Managed Memory Strategy Demo
#
# This notebook demonstrates how to set up and use Amazon Bedrock AgentCore self-managed memory strategies with boto3. The self-managed memory strategy allows you to create a custom pipeline for memory extraction and consolidation, triggered by conversation events.
#
# ## How it works
#
# 1. Configure triggers: Define trigger conditions (message count, idle timeout, token count) that invoke your pipeline based on short-term memory events
# 2. Receive notifications: AgentCore publishes notifications to your SNS topic when trigger conditions are met
# 3. Process payload: AgentCore delivers conversation data to your S3 bucket
# 4. Extract & store memory records: Your custom pipeline retrieves the payload and processes memories
#
# For detailed information about self-managed memory strategies, see the [official AWS documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-self-managed-strategies.html#use-self-managed-strategy).
#
# ## Setup Overview
#
# This demo will:
# 1. Create required AWS infrastructure (S3, SNS, SQS, Lambda, IAM roles)
# 2. Create an AgentCore memory with self-managed strategy
# 3. Create test events to demonstrate the memory processing pipeline
# 4. Create an Agent to demonstrate retrieval and usage of stored memories
# 5. Clean up resources when finished

#

# ## Setup and Imports


# Run: pip install -r requirements.txt --quiet


import time
import uuid
from pathlib import Path
from aws_utils import AWSUtils

# Configure AWS region
region_name = "us-west-2"  # Change to your preferred region
aws_utils = AWSUtils(region_name=region_name)

# Read Lambda function code
with open(Path(__file__).parent / "lambda_function.py", "r") as f:
    lambda_code = f.read()


print(lambda_code)


# ## Step 1: Create S3 Bucket for Payload Delivery
#
# Create an S3 bucket where AgentCore will deliver conversation payloads when trigger conditions are met.


# Create S3 bucket with a unique name
bucket_name = aws_utils.create_s3_bucket("agentcore-memory-payloads")
print(f"S3 bucket created: {bucket_name}")


# ## Step 2: Create SNS Topic for Memory Job Notifications
#
# Create an SNS topic that will receive notifications when AgentCore triggers the memory processing pipeline.


# Create SNS topic
sns_topic_name = f"agentcore-memory-notifications-{int(time.time())}"
sns_topic_arn = aws_utils.create_sns_topic(sns_topic_name)
print(f"SNS topic created: {sns_topic_arn}")


# ## Step 3: Create SQS Queue with SNS Subscription
#
# Create an SQS queue that subscribes to the SNS topic. This queue will receive memory job notifications that will trigger our Lambda function.


# Create SQS queue and subscribe to SNS topic
queue_name = f"agentcore-memory-queue-{int(time.time())}"
queue_url, queue_arn = aws_utils.create_sqs_queue_with_sns_subscription(queue_name, sns_topic_arn)
print(f"SQS queue created: {queue_url}")


# ## Step 4: Create IAM Roles
#
# Create two IAM roles:
# 1. For AgentCore to access S3 and SNS
# 2. For Lambda to access S3, SQS, and AgentCore APIs


# Create IAM role for AgentCore
agentcore_role_name = f"AgentCoreMemoryExecutionRole-{int(time.time())}"
agentcore_role_arn = aws_utils.create_iam_role_for_agentcore(agentcore_role_name, bucket_name, sns_topic_arn)
print(f"AgentCore IAM role created: {agentcore_role_arn}")

# Create IAM role for Lambda
lambda_role_name = f"LambdaMemoryProcessingRole-{int(time.time())}"
lambda_role_arn = aws_utils.create_iam_role_for_lambda(lambda_role_name, bucket_name, queue_arn)
print(f"Lambda IAM role created: {lambda_role_arn}")


# ## Step 5: Create Lambda Function for Memory Processing
#
# Create a Lambda function that will be triggered by SQS messages. This function will:
# 1. Download the conversation payload from S3
# 2. Extract memories using a Bedrock model
# 3. Store the extracted memories back into AgentCore


# Create Lambda function
function_name = f"agentcore-memory-processor-{int(time.time())}"
function_arn = aws_utils.create_lambda_function(function_name, lambda_role_arn, lambda_code)
print(f"Lambda function created: {function_arn}")

# Add SQS trigger to Lambda
event_source_uuid = aws_utils.add_sqs_trigger_to_lambda(function_name, queue_arn)
print(f"SQS trigger added to Lambda: {event_source_uuid}")


# ## Step 6: Create AgentCore Memory with Self-Managed Strategy
#
# Create an AgentCore memory with a self-managed strategy configuration that uses the infrastructure we've set up.


import importlib  # noqa: E402
import aws_utils  # noqa: E402

importlib.reload(aws_utils)

# # Create a new instance of AWSUtils with the updated code
aws_utils = aws_utils.AWSUtils(region_name=region_name)

# Create memory with self-managed strategy
memory_name = f"SelfManageMemory{int(time.time())}"
memory_description = "Demo memory using self-managed strategy"

memory_id = aws_utils.create_memory_with_self_managed_strategy(
    memory_name=memory_name,
    memory_description=memory_description,
    role_arn=agentcore_role_arn,
    sns_topic_arn=sns_topic_arn,
    s3_bucket_name=bucket_name,
    message_trigger_count=3,  # Trigger after 3 messages
    token_trigger_count=500,  # Trigger after ~500 tokens
    idle_timeout=300,  # Trigger after 5 minutes of idle time
    historical_window_size=5,  # Include 5 previous messages in context
)

print(f"Memory created: {memory_id}")
# print(f"Strategy ID: {strategy_id}")


def wait_for_memory_to_get_active(memory_id):
    response = aws_utils.agentcore_client_control.get_memory(memoryId=memory_id)

    while response["memory"]["status"] != "ACTIVE":
        time.sleep(10)
        response = aws_utils.agentcore_client_control.get_memory(memoryId=memory_id)
        print(f"Memory creation status: {response['memory']['status']}")
    return response["memory"]["status"]


wait_for_memory_to_get_active(memory_id=memory_id)


# ## Step 7: Create Test Events to Trigger Memory Pipeline
#
# Now let's create some test events to trigger the self-managed memory pipeline. We'll create enough events to exceed the message trigger count.


actor_id = "test-user-123"


# Create test events
session_id = aws_utils.create_test_events(
    memory_id=memory_id,
    actor_id=actor_id,
    num_events=6,  # This will exceed our message_trigger_count of 3
)

print(f"Created test events with session ID: {session_id}")


aws_utils.agentcore_client.list_events(memoryId=memory_id, actorId=actor_id, sessionId=session_id)


# ## Step 8: Wait for Memory Processing
#
# Now we need to wait for the memory processing pipeline to execute. This involves:
# 1. AgentCore detecting the trigger condition (message count exceeded)
# 2. AgentCore publishing a notification to SNS
# 3. SNS delivering the message to SQS
# 4. SQS triggering our Lambda function
# 5. Lambda processing the conversation and storing memories
#
# Let's wait a bit and then check if memories were created.


print("Waiting 15 seconds for memory processing to complete...")
time.sleep(15)


# ## Step 9: Verify Memory Records
#
# Let's check if our memory pipeline created memory records by searching the memory.


session_id


# List memory records
namespace = f"/interests/actor/{actor_id}/session/{session_id}/"


def list_memory_records(memory_id, namespace):
    try:
        response = aws_utils.agentcore_client.list_memory_records(memoryId=memory_id, namespace=namespace)
        print(f"Found {len(response.get('memoryRecordSummaries'))} memory records")

        # Display the search results
        for idx, result in enumerate(response.get("memoryRecordSummaries")):
            print(f"Memory: {idx}")
            print(f"Content: {result['content']['text']}")
    except Exception as e:
        print(f"Error searching memory: {e}")


list_memory_records(memory_id, namespace)


# Note that above records shows repitition of user interests as I have not added any consolidation logic. Therefore, there is repition, with the ability to provide self managed strategy I can define if I want only extraction and ingestion. It will be dependent on your business use case.


# Search memory records
def retrieve_memory_records(memory_id, query, topK, namespace):
    try:
        response = aws_utils.agentcore_client.retrieve_memory_records(
            memoryId=memory_id,
            searchCriteria={"searchQuery": query, "topK": topK},
            namespace=namespace,
        )
        print(f"Found {len(response.get('memoryRecordSummaries'))} memory records")

        # Display the search results
        for idx, result in enumerate(response.get("memoryRecordSummaries")):
            print(f"\nMemory Record {idx + 1}:")
            print(f"Content: {result['content']['text']}")
    except Exception as e:
        print(f"Error searching memory: {e}")


retrieve_memory_records(memory_id=memory_id, query="food choices for dinner", topK=5, namespace=namespace)


# ## Step 10: Create Additional Test Events with Different Content
#
# Let's create some more test events with different content to trigger another memory processing cycle.


# Create custom test events
session_id = str(uuid.uuid4())
actor_id = "test-user-456"

# Custom events with more specific information
test_events = [
    {
        "user": "I'm trying to eat healthier and have been exploring Mediterranean cuisine lately.",
        "assistant": "That's wonderful! Mediterranean food is both delicious and nutritious. What Mediterranean dishes have you tried so far?",
    },
    {
        "user": "I love Greek salads with feta cheese and olives, and I've been making homemade hummus.",
        "assistant": "Homemade hummus is fantastic! Do you prefer it with tahini or without? And what's your favorite way to serve it?",
    },
    {
        "user": "I always use tahini and like to serve it with fresh vegetables and pita bread. I'm also vegetarian, so I avoid meat.",
        "assistant": "Being vegetarian opens up so many Mediterranean options! Have you tried making stuffed grape leaves or lentil-based dishes?",
    },
    {
        "user": "Not yet, but I'd love to learn. I'm also allergic to shellfish, so I have to be careful with seafood dishes.",
        "assistant": "Good to know about the shellfish allergy. For vegetarian Mediterranean cooking, you might enjoy making moussaka with eggplant or trying some traditional Greek bean dishes. Would you like some recipe suggestions?",
    },
]

# Create events
for idx, event in enumerate(test_events):
    try:
        event_payload = [
            {"conversational": {"content": {"text": event["user"]}, "role": "USER"}},
            {
                "conversational": {
                    "content": {"text": event["assistant"]},
                    "role": "ASSISTANT",
                }
            },
        ]

        aws_utils.agentcore_client.create_event(
            memoryId=memory_id,
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=int(time.time()),
            payload=event_payload,
            clientToken=str(uuid.uuid4()),
        )

        print(f"Created event {idx + 1}/{len(test_events)}")
        time.sleep(1)

    except Exception as e:
        print(f"Error creating test event: {e}")

print("\nWaiting 30 seconds for memory processing to complete...")
time.sleep(30)


# ## Step 11: Search for New Memories
#
# Now let's search for the new memories related to hiking and the user's dog.


# Search memory records for outdoor activities
namespace = f"/interests/actor/{actor_id}/session/{session_id}/"
retrieve_memory_records(memory_id=memory_id, query="dog pets golden retriever", topK=5, namespace=namespace)


# ## Step 12: Creating the agent
#
# In this section, how to build an intelligent culinary assistant using Strands agents integrated with AgentCore Self-Managed Memory via hooks. We'll focus on long-term memory for user food preferences, dietary restrictions, and dining history to provide personalized restaurant recommendations based on previous conversations and individual tastes
#
#


import logging  # noqa: E402
from typing import Dict  # noqa: E402

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("customer-support")

# Import required modules
from strands import Agent  # noqa: E402
from strands.hooks import (  # noqa: E402
    AfterInvocationEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)
from bedrock_agentcore.memory import MemoryClient  # noqa: E402

# Initialize MemoryClient
client = MemoryClient(region_name=region_name)


# ## Step 13: Create Memory Hook Provider for Culinary Assistant with Self-Managed Memory
#
# Hooks are special functions that run at specific points in an agent's execution lifecycle. Our custom hook provider leverages the self-managed memory strategy to automatically manage culinary context by:
#
# - **Retrieving relevant food preferences** from self-managed memory records
# - **Injecting contextual information** about dietary restrictions, cuisine preferences, and dining history into new queries
# - **Saving dining interactions** for future reference using batch operations
#
# This creates a seamless memory experience that:
# - Automatically retrieves your stored food preferences before processing each query
# - Provides context-aware restaurant recommendations based on your dining history
#
# The self-managed approach gives us full control over how food preferences are stored, retrieved, and used to enhance the dining recommendation experience.
#


# Helper function to get namespaces from memory strategies list
def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Get namespace mapping for memory strategies."""
    strategies = mem_client.get_memory_strategies(memory_id)
    return {i["type"]: i["namespaces"][0] for i in strategies}


class CulinaryAssistantMemoryHooks(HookProvider):
    """Memory hooks for culinary assistant agent"""

    def __init__(self, memory_id: str, namespace: str):
        self.memory_id = memory_id
        self.namespace = namespace

    def retrieve_food_preferences(self, event: MessageAddedEvent):
        """Retrieve user food preferences before processing dining query"""
        messages = event.agent.messages
        if messages[-1]["role"] == "user" and "toolResult" not in messages[-1]["content"][0]:
            user_query = messages[-1]["content"][0]["text"]

            try:
                # Retrieve food preferences using direct API
                response = aws_utils.agentcore_client.retrieve_memory_records(
                    memoryId=self.memory_id,
                    searchCriteria={"searchQuery": user_query, "topK": 5},
                    namespace=self.namespace,
                )

                memory_records = response.get("memoryRecordSummaries", [])

                if memory_records:
                    # Format retrieved preferences
                    preferences_context = []
                    for record in memory_records:
                        content = record.get("content", {}).get("text", "").strip()
                        if content:
                            preferences_context.append(content)

                    # Inject food preferences into the query
                    if preferences_context:
                        context_text = "\n".join(preferences_context)
                        original_text = messages[-1]["content"][0]["text"]
                        messages[-1]["content"][0]["text"] = (
                            f"User Food Preferences:\n{context_text}\n\n{original_text}"
                        )
                        logger.info(f"Retrieved {len(preferences_context)} food preference records")

            except Exception as e:
                logger.error(f"Failed to retrieve food preferences: {e}")

    def save_dining_interaction(self, event: AfterInvocationEvent):
        """Save dining recommendation interaction after agent response"""
        try:
            messages = event.agent.messages
            if len(messages) >= 2 and messages[-1]["role"] == "assistant":
                # Get last user query and agent response
                user_query = None
                agent_response = None

                for msg in reversed(messages):
                    if msg["role"] == "assistant" and not agent_response:
                        agent_response = msg["content"][0]["text"]
                    elif msg["role"] == "user" and not user_query and "toolResult" not in msg["content"][0]:
                        user_query = msg["content"][0]["text"]
                        break

                if user_query and agent_response:
                    # Save the interaction using direct API
                    interaction_content = (  # noqa: F841
                        f"Query: {user_query}\nRecommendation: {agent_response}"
                    )

                    # You would use create_memory_record API here
                    # aws_utils.agentcore_client.create_memory_record(...)

                    logger.info("Saved dining interaction to memory")

        except Exception as e:
            logger.error(f"Failed to save dining interaction: {e}")

    def register_hooks(self, registry: HookRegistry) -> None:
        """Register culinary assistant memory hooks"""
        registry.add_callback(MessageAddedEvent, self.retrieve_food_preferences)
        registry.add_callback(AfterInvocationEvent, self.save_dining_interaction)
        logger.info("Culinary assistant memory hooks registered")


# ## Step 14: Create Culinary assistant Agent


# Create memory hooks for culinary assistant
print(memory_id)
culinary_hooks = CulinaryAssistantMemoryHooks(memory_id, namespace)

# Create culinary assistant agent
culinary_agent = Agent(
    hooks=[culinary_hooks],
    model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
    tools=[],  # Update these tools as needed
    state={"actor_id": actor_id, "session_id": session_id},
    system_prompt="""You are the Culinary Assistant, a sophisticated restaurant recommendation assistant.

PURPOSE:
- Help users discover restaurants based on their preferences
- Remember user preferences throughout the conversation
- Provide personalized dining recommendations

You have access to a Memory tool that enables you to:
- Store user preferences (dietary restrictions, favorite cuisines, budget preferences, etc.)
- Retrieve previously stored information to personalize recommendations""",
)

print("✅ Culinary assistant agent created with memory capabilities")


# #### Agent is ready to go.
#
# ### Lets test Culinary Assistant Scenarios


response1 = culinary_agent("what are the food choices for Dinner?")
print(f"Support Agent: {response1}")


# ## Step 15: Clean Up Resources
#
# Now let's clean up all the resources we created to avoid incurring unnecessary costs.


# Clean up all resources
import importlib  # noqa: E402
import aws_utils  # noqa: E402

importlib.reload(aws_utils)

# # Create a new instance of AWSUtils with the updated code
aws_utils = aws_utils.AWSUtils(region_name=region_name)

# # Clean up resources with auto-discovery
aws_utils.cleanup_resources(discover_resources=True)
print("All resources have been cleaned up!")


# ## Summary
#
# In this notebook, we've demonstrated how to:
#
# 1. Set up the AWS infrastructure needed for self-managed memory
# 2. Create an AgentCore memory with a self-managed strategy
# 3. Configure trigger conditions for memory processing
# 4. Implement a Lambda-based memory processing pipeline
# 5. Test the memory system with sample conversations
# 6. Search for extracted memories
# 7. Created culinary agent to test self managed memory
# 8. Clean up all resources
#
# The self-managed memory strategy gives you complete control over memory extraction, allowing you to build custom pipelines that fit your specific use case.
