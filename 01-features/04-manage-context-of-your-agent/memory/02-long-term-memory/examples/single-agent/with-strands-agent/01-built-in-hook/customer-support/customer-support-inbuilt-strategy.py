#!/usr/bin/env python

# # Strands Agents with AgentCore Memory (Long term memory) - Built-in Strategies
#
# ## Introduction
#
# This tutorial demonstrates how to build an **intelligent customer support agent** using Strands agents integrated with AgentCore Memory via hooks using **MemoryManager** with **built-in strategies** and **MemorySessionManager**. We'll focus on Long term memory for customer interaction history, remembering purchase details, and provides personalized support based on previous conversations and user preferences.
#
# **NOTE: This approach uses built-in strategies (SemanticStrategy, UserPreferenceStrategy) which do NOT require an IAM execution role.**
#
# ### Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long term Conversational                                                         |
# | Agent type          | Customer Support                                                                 |
# | Agentic Framework   | Strands Agents                                                                   |
# | LLM model           | Anthropic Claude Haiku 4.5                                                      |
# | Tutorial components | AgentCore Semantic and User Preferences Memory Extraction (Built-in), Hooks for storing and retrieving Memory              |
# | Example complexity  | Intermediate                                                                     |
#
# You'll learn to:
# - Set up AgentCore Memory with built-in Long term strategies using MemoryManager
# - Create memory hooks for automatic storage and retrieval with MemorySessionManager
# - Build a customer support agent with persistent memory
# - Handle customer issues with context from previous interactions
# - Use built-in strategies without requiring IAM role configuration
#
# ### Scenario Context
# In this example, we will build a **Customer Support Use Case**. The agent will remember customer context, including order history, preferences, and previous issues, enabling more personalized and effective support. Conversations with customers are automatically stored using memory hooks, ensuring that important details are never lost. By employing multiple memory strategies such as semantic, and User Preference — the agent can capture a wide range of relevant information. This setup allows the agent to resolve issues with full awareness of the customer's history and preferences. Additionally, the agent is integrated with web search capabilities, making it easy to provide up-to-date product information and troubleshooting guidance as needed.
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
# - Amazon Bedrock AgentCore SDK with MemoryManager support

# ## 📊 Choosing Between Built-in vs Custom Strategies
#
# AgentCore Memory offers two approaches for memory extraction and consolidation. This notebook demonstrates the **Built-in Strategies** approach.
#
# ### Built-in Strategies (This Notebook)
#
# **When to use:**
# - ✅ Quick setup and prototyping
# - ✅ Standard memory extraction needs
# - ✅ No need for custom model selection
# - ✅ Simplified IAM configuration (no execution role required)
# - ✅ Production workloads with default AgentCore models
#
# **Key characteristics:**
# - Uses `SemanticStrategy` and `UserPreferenceStrategy` classes
# - AgentCore Memory automatically selects and manages models
# - No IAM execution role required
# - Simpler configuration with fewer parameters
# - Ideal for most use cases
#
# **Example:**
# ```python
# strategies = [
#     SemanticStrategy(
#         name="CustomerSupportSemantic",
#         description="Stores facts from conversations",
#         namespaces=["support/customer/{actorId}/semantic/"]
#     )
# ]
#
# memory = memory_manager.get_or_create_memory(
#     name="CustomerSupportMemory",
#     strategies=strategies
#     # No memory_execution_role_arn needed!
# )
# ```
#
# ### Custom Strategy Override (See `customer-support-override-strategy.ipynb`)
#
# **When to use:**
# - ✅ Need to specify custom Bedrock models for extraction/consolidation
# - ✅ Fine-tuned control over model behavior
# - ✅ Custom prompts for extraction and consolidation
# - ✅ Advanced use cases requiring specific model capabilities
# - ✅ Compliance requirements for specific model versions
#
# **Key characteristics:**
# - Uses `CustomSemanticStrategy` and `CustomUserPreferenceStrategy` classes
# - Requires IAM execution role for model invocation
# - Allows specification of `ExtractionConfig` and `ConsolidationConfig`
# - More complex setup but greater control
# - Useful for specialized requirements
#
# **Example:**
# ```python
# strategies = [
#     CustomSemanticStrategy(
#         name="CustomerSupportSemantic",
#         extraction_config=ExtractionConfig(
#             model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
#             append_to_prompt="Extract factual information..."
#         ),
#         consolidation_config=ConsolidationConfig(
#             model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
#             append_to_prompt="Consolidate semantic insights..."
#         ),
#         namespaces=["support/customer/{actorId}/semantic/"]
#     )
# ]
#
# memory = memory_manager.get_or_create_memory(
#     name="CustomerSupportMemory",
#     strategies=strategies,
#     memory_execution_role_arn=MEMORY_EXECUTION_ROLE_ARN  # Required!
# )
# ```
#
# ### Quick Comparison Table
#
# | Feature | Built-in Strategies | Custom Strategy Override |
# |---------|---------------------|-------------------------|
# | **Setup Complexity** | Simple | Advanced |
# | **IAM Role Required** | ❌ No | ✅ Yes |
# | **Model Selection** | Automatic (AgentCore managed) | Manual (you specify) |
# | **Custom Prompts** | ❌ No | ✅ Yes |
# | **Configuration** | Minimal | Detailed |
# | **Use Case** | Standard memory extraction | Custom model requirements |
# | **Recommended For** | Most applications | Specialized needs |
#
# ---
#
# **💡 Recommendation:** Start with built-in strategies (this notebook) for most use cases. Only use custom strategy override if you have specific model requirements or need fine-grained control over extraction/consolidation behavior.

# ## Step 1: Install Dependencies and Setup
# Let's begin importing all the necessary libraries and defining the clients to make this notebook work.


# Run: pip install -qr requirements.txt


import logging
from typing import List
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("customer-support")

# Import required modules for Strands Agent
from strands import Agent, tool  # noqa: E402
from strands.hooks import (  # noqa: E402
    AfterInvocationEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)
from ddgs import DDGS  # noqa: E402

# Import memory management modules
from bedrock_agentcore.memory import MemoryClient  # noqa: E402
from bedrock_agentcore.memory.constants import (  # noqa: E402
    ConversationalMessage,
    MessageRole,
    RetrievalConfig,
    StrategyType,
)
from bedrock_agentcore.memory.models import StringValue, MemoryRecord  # noqa: E402
from bedrock_agentcore.memory.session import MemorySession, MemorySessionManager  # noqa: E402

# Define message role constants
USER = MessageRole.USER
ASSISTANT = MessageRole.ASSISTANT

logger.info("✅ All imports loaded successfully")


# Configuration - Replace with the correct values
REGION = "us-east-1"
CUSTOMER_ID = "customer_001"
SESSION_ID = f"support_{datetime.now().strftime('%Y%m%d%H%M%S')}"

logger.info("✅ Configuration loaded")
logger.info(f"   Region: {REGION}")
logger.info(f"   Customer ID: {CUSTOMER_ID}")
logger.info(f"   Session ID: {SESSION_ID}")


# ## Step 2: Create Memory Resource for Customer Support
#
# For customer support, we'll use multiple built-in memory strategies:
# - **UserPreferenceStrategy**: Captures customer preferences and behavior (built-in)
# - **SemanticStrategy**: Stores order facts and product information (built-in)
#
# **IMPORTANT**: Built-in strategies do NOT require an IAM execution role. AgentCore Memory uses default models for extraction and consolidation.


# Initialize Memory Client
memory_client = MemoryClient(region_name=REGION)
memory_name = "CustomerSupportLongTermMemory"

logger.info(f"✅ MemoryClient initialized for region: {REGION}")

# Define memory strategies using native SDK format
strategies = [
    {
        StrategyType.USER_PREFERENCE.value: {
            "name": "CustomerPreferences",
            "description": "Captures customer preferences and behavior",
            "namespaces": ["support/customer/{actorId}/preferences/"],
        }
    },
    {
        StrategyType.SEMANTIC.value: {
            "name": "CustomerSupportSemantic",
            "description": "Stores facts from conversations",
            "namespaces": ["support/customer/{actorId}/semantic/"],
        }
    },
]

# Create memory resource with built-in strategies
# NOTE: No memory_execution_role_arn needed for built-in strategies
logger.info(f"Creating memory '{memory_name}' with {len(strategies)} built-in strategies...")

# create_or_get_memory is idempotent: it creates the memory (waiting for ACTIVE) or
# returns the existing one on a re-run, replacing the manual create/except/list-and-match.
memory = memory_client.create_or_get_memory(
    name=memory_name,
    strategies=strategies,
    description="Memory for customer support agent with built-in strategies",
    event_expiry_days=90,  # Memories expire after 90 days
)
memory_id = memory["id"]
logger.info("✅ Memory ready:")
logger.info(f"   Memory ID: {memory_id}")
logger.info(f"   Memory Name: {memory_name}")
logger.info("   Using built-in strategies (no IAM role required)")


# Test memory client basic functionality
try:
    # Test listing existing memories
    existing_memories = memory_client.list_memories()
    logger.info(f"✅ Memory client connection successful. Found {len(existing_memories)} existing memories")

except Exception as e:
    logger.error(f"❌ Memory client test failed: {e}")
    raise


# Let's confirm if our memory contains the strategies we assigned


# Display memory information
print(f"Memory ID: {memory_id}")
print(f"Memory Name: {memory_name}")
print(f"Number of strategies: {len(strategies)}")


# ## Step 3: Create Agent Tools


from ddgs.exceptions import DDGSException, RatelimitException  # noqa: E402


@tool
def web_search(query: str, max_results: int = 3) -> str:
    """Search the web for product information, troubleshooting guides, or support articles.

    Args:
        query: Search query for product info or troubleshooting
        max_results: Maximum number of results to return

    Returns:
        Search results with titles and snippets
    """
    try:
        results = DDGS().text(query, region="us-en", max_results=max_results)
        if not results:
            return "No search results found."

        formatted_results = []
        for i, result in enumerate(results, 1):
            formatted_results.append(
                f"{i}. {result.get('title', 'No title')}\n   {result.get('body', 'No description')}"
            )

        return "\n".join(formatted_results)
    except RatelimitException:
        return "Rate limit reached: Please try again after a short delay."
    except DDGSException as d:
        return f"Search Error: {d}"
    except Exception as e:
        return f"Search error: {str(e)}"


logger.info("✅ Web search tool ready")


@tool
def check_order_status(order_number: str) -> str:
    """Check the status of a customer order.

    Args:
        order_number: The order number to check

    Returns:
        Order status information
    """
    # Simulate order lookup
    mock_orders = {
        "123456": "iPhone 15 Pro - Delivered on June 5, 2025",
        "654321": "Sennheiser Headphones - Delivered on June 25, 2025, 1-year warranty active",
        "789012": "Samsung Galaxy S23 - In transit, expected delivery on July 1, 2025",
    }

    return mock_orders.get(order_number, f"Order {order_number} not found. Please verify the order number.")


logger.info("✅ Check Order Status tool ready")


# ## Step 4: Initialize Session Manager
#
# **NEW: This section introduces the MemorySessionManager for session-based Memory operations.**


# Initialize the session memory manager
session_manager: MemorySessionManager = MemorySessionManager(memory_id=memory_id, region_name=REGION)

# Create a memory session for the specific customer
customer_session: MemorySession = session_manager.create_memory_session(actor_id=CUSTOMER_ID, session_id=SESSION_ID)

logger.info(f"✅ Session manager initialized for memory: {memory_id}")
logger.info(f"✅ Customer session created for actor: {CUSTOMER_ID}")
logger.info(f"   Session type: {type(customer_session)}")
logger.info(f"   Actor object: {customer_session.get_actor()}")


# ## Step 5: Create Memory Hook Provider for Customer Support
# Hooks are special functions that run at specific points in an agent's execution lifecycle. Our custom hook provider will automatically manage customer support context by:
# - **Saving support interactions** after each response using session-based methods
# - **Retrieving and Injecting relevant context** from previous orders and preferences when processing new queries.
#


class CustomerSupportMemoryHooks(HookProvider):
    """Memory hooks for customer support agent - ENHANCED with MemorySession"""

    def __init__(self, customer_session: MemorySession):
        # Accept MemorySession directly
        self.customer_session = customer_session

        # Define retrieval configuration for different memory types
        self.retrieval_config = {
            "support/customer/{actorId}/preferences/": RetrievalConfig(top_k=3, relevance_score=0.3),
            "support/customer/{actorId}/semantic/": RetrievalConfig(top_k=5, relevance_score=0.2),
        }

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve customer context before processing support query using MemorySession"""
        messages = event.agent.messages
        if messages[-1]["role"] == "user" and "toolResult" not in messages[-1]["content"][0]:
            user_query = messages[-1]["content"][0]["text"]

            try:
                # Use MemorySession for context retrieval
                relevant_memories = []

                # Search across different memory namespaces using MemorySession
                for namespace_template, config in self.retrieval_config.items():
                    # Resolve namespace template with actual actor ID from session
                    resolved_namespace = namespace_template.format(actorId=self.customer_session._actor_id)

                    # Use MemorySession API (no need to pass actor_id/session_id).
                    memories = self.customer_session.search_long_term_memories(
                        query=user_query,
                        namespace_prefix=resolved_namespace,
                        top_k=config.top_k,
                    )

                    # Filter by relevance score
                    filtered_memories = [
                        memory for memory in memories if memory.get("score", 0) >= config.relevance_score
                    ]

                    relevant_memories.extend(filtered_memories)
                    logger.info(
                        f"Found {len(filtered_memories)} relevant memories in {resolved_namespace} (filtered from {len(memories)} total)"
                    )

                # Inject context into agent's system prompt if memories found
                if relevant_memories:
                    context_text = self._format_context(relevant_memories)
                    original_prompt = event.agent.system_prompt
                    enhanced_prompt = f"{original_prompt}\n\nCustomer Context:\n{context_text}"
                    event.agent.system_prompt = enhanced_prompt
                    logger.info(f"✅ Injected {len(relevant_memories)} memories into agent context")

            except Exception as e:
                logger.error(f"Failed to retrieve customer context: {e}")

    def _format_context(self, memories: List[MemoryRecord]) -> str:
        """Format retrieved memories for agent context"""
        context_lines = []
        for i, memory in enumerate(memories[:5], 1):  # Limit to top 5
            content = memory.get("content", {}).get("text", "No content available")
            score = memory.get("score", 0)
            context_lines.append(f"{i}. (Score: {score:.2f}) {content[:200]}...")

        return "\n".join(context_lines)

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save support interaction using MemorySession (cleaner API)"""
        try:
            messages = event.agent.messages
            if len(messages) >= 2 and messages[-1]["role"] == "assistant":
                # Get last customer query and agent response
                customer_query = None
                agent_response = None

                for msg in reversed(messages):
                    if msg["role"] == "assistant" and not agent_response:
                        agent_response = msg["content"][0]["text"]
                    elif msg["role"] == "user" and not customer_query and "toolResult" not in msg["content"][0]:
                        customer_query = msg["content"][0]["text"]
                        break

                if customer_query and agent_response:
                    # Use MemorySession (no need to pass actor_id/session_id)
                    interaction_messages = [
                        ConversationalMessage(customer_query, USER),
                        ConversationalMessage(agent_response, ASSISTANT),
                    ]

                    result = self.customer_session.add_turns(interaction_messages)
                    logger.info(f"✅ Saved interaction using MemorySession - Event ID: {result['eventId']}")

        except Exception as e:
            logger.error(f"Failed to save support interaction: {e}")

    def register_hooks(self, registry: HookRegistry) -> None:
        """Register customer support memory hooks"""
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)  # Re-added!
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)
        logger.info("✅ Customer support memory hooks registered with MemorySession")


print("Executed!")


# ### Step 6: Create Customer Support Agent


# Create memory hooks using MemorySession
support_hooks = CustomerSupportMemoryHooks(customer_session)

# Create customer support agent
support_agent = Agent(
    hooks=[support_hooks],
    model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
    tools=[web_search, check_order_status],
    system_prompt="""You are a helpful customer support agent with access to customer history and order information. 
    
    Your role:
    - Help customers with their orders, returns, and product issues
    - Use customer context to provide personalized support
    - Search for product information when needed
    - Be empathetic and solution-focused
    - Reference previous orders and preferences when relevant
    
    Always be professional, helpful, and aim to resolve customer issues efficiently.""",
)

print("✅ Customer support agent created with MemorySession integration")


# ### Step 7: Seed Customer History
#
# Let's add some previous customer interactions to demonstrate memory functionality.
#
# **NOTE: This section uses ConversationalMessage format and session-based storage.**


# Seed with previous customer interactions using MemorySession
previous_interactions = [
    ConversationalMessage("I bought a new iPhone 15 Pro on June 1st, 2025. Order number is 123456.", USER),
    ConversationalMessage(
        "Thank you for your purchase! I can see your iPhone 15 Pro order #123456 was delivered successfully. How can I help you today?",
        ASSISTANT,
    ),
    ConversationalMessage(
        "I also ordered Sennheiser headphones on June 20th. Order number 654321. They came with 1-year warranty.",
        USER,
    ),
    ConversationalMessage(
        "Perfect! I have your Sennheiser headphones order #654321 on file with the 1-year warranty. Both your iPhone and headphones should work great together.",
        ASSISTANT,
    ),
    ConversationalMessage("I'm looking for a good laptop. I prefer ThinkPad models.", USER),
    ConversationalMessage(
        "Great choice! ThinkPads are excellent for their durability and performance. Let me help you find the right model for your needs.",
        ASSISTANT,
    ),
]

# Save using MemorySession
try:
    event_response = customer_session.add_turns(previous_interactions)
    logger.info("✅ Seeded customer history using MemorySession")
    logger.info(f"   Event ID: {event_response['eventId']}")
except Exception as e:
    logger.error(f"⚠️ Error seeding history: {e}")


# #### Agent is ready to go.
#
# ### Lets test Customer Support Scenarios


# Test 1: Customer reports iPhone issue
logger.info("🧪 Running Test 1: iPhone performance issue")
test_query_1 = "My iPhone is running very slow and gets hot when charging. Can you help?"
logger.info(f"Query: {test_query_1}")

response1 = support_agent(test_query_1)
logger.info("✅ Test 1 completed successfully")
print(f"\n📱 iPhone Issue Support Response:\n{response1}\n")


# Test 2: Bluetooth connectivity issue
logger.info("🧪 Running Test 2: Bluetooth connectivity issue")
test_query_2 = "My iPhone won't connect to my Sennheiser headphones via Bluetooth. How do I fix this?"
logger.info(f"Query: {test_query_2}")

response2 = support_agent(test_query_2)
logger.info("✅ Test 2 completed successfully")
print(f"\n🎧 Bluetooth Issue Support Response:\n{response2}\n")


# Test 3: Check order status
logger.info("🧪 Running Test 3: Order status check")
test_query_3 = "Can you check the status of my recent orders?"
logger.info(f"Query: {test_query_3}")

response3 = support_agent(test_query_3)
logger.info("✅ Test 3 completed successfully")
print(f"\n📦 Order Status Support Response:\n{response3}\n")


# Test 4: Product recommendation based on preferences
logger.info("🧪 Running Test 4: Product recommendation")
test_query_4 = "I'm still interested in buying a laptop. What ThinkPad models do you recommend?"
logger.info(f"Query: {test_query_4}")

response4 = support_agent(test_query_4)
logger.info("✅ Test 4 completed successfully")
print(f"\n💻 Product Recommendation Support Response:\n{response4}\n")

logger.info("🎉 All customer support scenario tests completed!")


# ## Advanced Features: Branching and Metadata
#
# ### Conversation Branching with SessionManager
#
# Explore alternative support scenarios using branching:


# Get the last event ID from our conversation
events = customer_session.list_events()
if events:
    last_event_id = events[-1].eventId

    # Fork conversation to explore premium support path
    branch_event = customer_session.fork_conversation(
        root_event_id=last_event_id,
        branch_name="premium-support",
        messages=[
            ConversationalMessage("I'd like to upgrade to premium support for faster resolution.", USER),
            ConversationalMessage(
                "Excellent choice! With premium support, you'll get 24/7 priority assistance, dedicated account manager, and same-day resolution guarantee. Let me process your upgrade.",
                ASSISTANT,
            ),
        ],
    )

    logger.info(f"✅ Created premium support branch from event {last_event_id}")

    # List all branches
    branches = customer_session.list_branches()
    print(f"\n🌳 Support session has {len(branches)} branch(es):")
    for branch in branches:
        print(f"   - {branch.name}: {branch.event_count} events")
else:
    print("No events found to branch from")


# ### Metadata for Advanced Support Tracking
#
# Use metadata to track comprehensive support metrics:


# Add a support interaction with comprehensive metadata
metadata_event = customer_session.add_turns(
    messages=[
        ConversationalMessage(
            "The ThinkPad X1 Carbon you recommended is perfect! I'll order it now.",
            USER,
        ),
        ConversationalMessage(
            "Fantastic! The ThinkPad X1 Carbon is an excellent choice for your needs. I'll help you complete the order with your preferred configuration.",
            ASSISTANT,
        ),
    ],
    metadata={
        "interaction_type": StringValue.build("product_recommendation"),
        "outcome": StringValue.build("purchase_intent"),
        "product_category": StringValue.build("laptops"),
        "product_brand": StringValue.build("lenovo"),
        "customer_sentiment": StringValue.build("positive"),
        "support_tier": StringValue.build("standard"),
        "session_duration_minutes": StringValue.build("15"),
    },
)

logger.info(f"✅ Added support event with metadata - Event ID: {metadata_event['eventId']}")
print("\n📊 Support interaction tagged with:")
print("   - Interaction Type: product_recommendation")
print("   - Outcome: purchase_intent")
print("   - Product Category: laptops")
print("   - Customer Sentiment: positive")


# ### Advanced Metadata Queries
#
# Analyze support patterns and customer behavior:


try:
    # Query product recommendation interactions
    recommendation_events = customer_session.list_events(
        eventMetadata=[
            {
                "left": {"metadataKey": "interaction_type"},
                "operator": "EQUALS_TO",
                "right": {"metadataValue": {"stringValue": "product_recommendation"}},
            }
        ]
    )

    print(f"\n🛍️ Found {len(recommendation_events)} product recommendation interaction(s)")

    # Query positive sentiment interactions
    positive_events = customer_session.list_events(
        eventMetadata=[
            {
                "left": {"metadataKey": "customer_sentiment"},
                "operator": "EQUALS_TO",
                "right": {"metadataValue": {"stringValue": "positive"}},
            }
        ]
    )

    print(f"😊 Found {len(positive_events)} positive sentiment interaction(s)")

    print("\n💡 Advanced analytics use cases:")
    print("   - Track conversion rates from recommendations to purchases")
    print("   - Analyze customer sentiment trends over time")
    print("   - Identify most effective support interaction types")
    print("   - Measure support tier performance")
    print("   - Generate detailed customer journey reports")
    print("   - Optimize product recommendation strategies")

except Exception as e:
    logger.error(f"Error querying metadata: {e}")
    print("Note: Metadata filtering requires events with metadata tags")


# #### Customer Support Tutorial completed! 🎉
# Key takeaways:
# - Memory hooks automatically manage customer context across support sessions using MemorySessionManager
# - Built-in strategies (SemanticStrategy, UserPreferenceStrategy) capture orders, preferences, and facts without requiring IAM roles
# - Agents can provide personalized support based on customer history
# - Tools can be integrated for order lookup and web search functionality
# - Customer support becomes more efficient with persistent memory
# - **Branching enables testing alternative support approaches and escalation paths**
# - **Metadata provides comprehensive support analytics and customer journey tracking**
# - **Built-in strategies simplify setup by eliminating IAM role configuration**

# ## Clean Up
#
# ### Optional: Delete Memory Resource


# Uncomment to delete the memory resource
# try:
#     memory_client.delete_memory_and_wait(memory_id=memory_id)
#     print(f"✅ Deleted memory resource: {memory_id}")
# except Exception as e:
#     print(f"Error deleting memory: {e}")


# ## Using the AgentCore CLI
#
# The same memory resources and agent projects demonstrated above can also be
# created and managed with the **AgentCore CLI** (pinned version `0.11.0`).
# This is the recommended developer workflow for iterating quickly.
#
# ### Install the CLI
#
# ```bash
# npm install -g @aws/agentcore@0.11.0
# agentcore --version   # should print 0.11.0
# ```
#
# ### Create a project with memory
#
# ```bash
# # Scaffold a new agent project with short-term + long-term memory
# agentcore create \
#   --name MyMemoryAgent \
#   --framework Strands \
#   --model-provider Bedrock \
#   --memory longAndShortTerm \
#   --defaults
#
# cd MyMemoryAgent
# ```
#
# ### Add memory to an existing project
#
# ```bash
# # Add a memory resource with semantic and user-preference strategies
# agentcore add memory \
#   --name SharedMemory \
#   --strategies SEMANTIC,USER_PREFERENCE \
#   --expiry 30
# ```
#
# ### Deploy to AgentCore Runtime
#
# ```bash
# agentcore deploy
# agentcore status
# ```
#
# ### Invoke the deployed agent
#
# ```bash
# agentcore invoke "Hello, do you remember my name?" --stream
# ```
#
# ### View logs and traces
#
# ```bash
# agentcore logs
# agentcore traces list --limit 10
# ```
#
# ### Clean up
#
# ```bash
# # Remove all deployed resources (runtime + memory)
# agentcore remove all
# ```
#
