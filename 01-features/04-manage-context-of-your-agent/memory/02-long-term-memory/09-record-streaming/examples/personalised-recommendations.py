#!/usr/bin/env python

# # Amazon Bedrock AgentCore Memory for Personalised Recommendations
#
# ## Overview
#
# This tutorial demonstrates how to use [AgentCore Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html) namespaces to enable personalised customer experiences. You'll build an agent that dynamically queries across multiple namespace types — recommendations, transactions, preferences, and life events — and reasons across them to deliver contextual, personalised responses. Memory with namespaces gives agents native contextual data access for real-time personalisation
#
# ### Why Memory with Namespaces?
#
# You might ask: why use AgentCore Memory namespaces instead of a traditional database, RAG system, or key-value store like DynamoDB?
#
# **Namespace paths are agent-native:**
# - Hierarchical, semantic, and composable — `/bank/customers/{id}/recommendations/pending` is self-describing
# - Agents can dynamically construct paths from conversation context without SQL knowledge
# - No schema design, joins, connection pooling, or migrations required
#
# **Deterministic retrieval vs fuzzy search:**
# - Exact namespace match returns precisely what you need — no relevance scoring or semantic drift
# - RAG is great for unstructured knowledge, but business process data needs precision
#
# **Built-in actor isolation:**
# - Customer data is naturally partitioned by namespace path
# - No risk of cross-customer data leakage
#
# **Dynamic multi-source reasoning:**
# - The agent decides which namespaces to query and in what order based on the question
# - There's no fixed query plan — the LLM composes context from multiple namespace types
# - Adding a new data source (e.g., `/complaints`) requires zero schema changes — just write to it
#
#
# ### Tutorial Details
#
# | Information         | Details                                                    |
# |:--------------------|:-----------------------------------------------------------|
# | Tutorial type       | Memory for Personalised Recommendations                    |
# | Feature             | Long-Term Memory Namespaces + Strands Agent                |
# | Key features        | Multi-Namespace Reasoning, Dynamic Code Generation, Agent State |
# | Example complexity  | Intermediate                                               |
# | SDK used            | boto3, bedrock-agentcore, strands-agents, strands-agents-tools |
#
# ### What You'll Learn
#
# 1. Create a memory instance and populate it with multiple data types (recommendations, transactions, preferences, life events)
# 2. Build a tool that dynamically queries any namespace based on agent reasoning
# 3. Pass customer context deterministically via agent state (not hardcoded)
# 4. Let the agent reason across multiple namespace types to answer complex questions
# 5. Use `python_repl` for dynamic calculations (e.g., comparing spending to card benefits)
#
# ### Architecture
#
# ![Architecture](architecture.png)
#
# ### How It Works
#
# The agent has access to multiple namespace types per customer:
#
# ```
# /bank/customers/{id}/recommendations/{stage}  → pending, shown, declined recommendations
# /bank/customers/{id}/transactions/summary     → monthly spending by category
# /bank/customers/{id}/preferences              → stated preferences from conversations
# /bank/customers/{id}/life_events              → detected life events (travel plans, etc.)
# ```
#
# When a customer asks "What card should I get?", the agent:
# 1. Queries `recommendations/pending` to see what's available
# 2. Queries `transactions/summary` to validate against actual spending
# 3. Queries `preferences` to check for conflicts (e.g., "no annual fees")
# 4. Uses `python_repl` to calculate potential savings
#
# This multi-namespace hierarchies and LLM reasoning allow the agent to answer complex queries by *dynamically* populating context according to each query.
#
# **→ Continue to Part 2** for cross-customer analytics and marketing insights.

# ## 0. Prerequisites
#
# To execute this tutorial you will need:
# * Python 3.10+
# * AWS credentials configured with access to AgentCore Memory and Amazon Bedrock
# * Amazon Bedrock model access (Claude Sonnet)
#
# First, let's install the required libraries:


# Run: pip install "boto3>=1.42.63" "bedrock-agentcore[strands-agents]" strands-agents strands-agents-tools


# ### Setting Up Environment
#
# Let's import the required libraries and configure our environment:


import json
import boto3
import time
import uuid
from datetime import datetime
from bedrock_agentcore.memory.session import MemorySessionManager
from bedrock_agentcore.memory.client import MemoryClient
from strands import Agent, tool
from strands.models.bedrock import BedrockModel
from strands_tools import python_repl

import os

os.environ["BYPASS_TOOL_CONSENT"] = "True"
os.environ["PYTHON_REPL_INTERACTIVE"] = "false"


# Configuration
REGION = "us-west-2"
MODEL_ID = "global.anthropic.claude-opus-4-6-v1"

# Initialize clients
agentcore_client = boto3.client("bedrock-agentcore", region_name=REGION)
memory_client = MemoryClient(region_name=REGION)

print(f"✅ Initialized clients for region: {REGION}")


# ## 1. Create Memory Instance
#
# We create a memory instance without strategies — we'll populate it externally with data from multiple sources rather than extracting from conversations.


unique_name = f"personalisation_demo_{uuid.uuid4().hex[:8]}"
memory = memory_client.create_memory_and_wait(
    name=unique_name,
    strategies=[],
    description="Demo memory for personalised credit card recommendations",
)

MEMORY_ID = memory["memoryId"]
print(f"✅ Created memory: {MEMORY_ID}")


# ## 2. Populate with Multi-Source Sample Data
#
# In production, this data would come from multiple systems:
# - **Recommendations** from your recommendation engine or CRM
# - **Transactions** from your core banking or card processing system
# - **Preferences** extracted from past conversations or explicit settings
# - **Life events** detected from transaction patterns or customer interactions
#
# The key insight: AgentCore Memory provides a unified namespace structure to incorporate all this *external data* into agent state. The agent can query any of these seamlessly without knowing the underlying source systems.


# Customer 001: Frequent traveler, price-sensitive
customer_001_data = [
    # Recommendations
    {
        "namespace": "/bank/customers/customer_001/recommendations/pending",
        "content": {
            "product": "Travel Rewards Card",
            "annual_fee": 95,
            "benefits": "3x points on travel, 2x dining",
            "reason": "High travel spending detected",
        },
    },
    {
        "namespace": "/bank/customers/customer_001/recommendations/declined",
        "content": {
            "product": "Premium Platinum Card",
            "annual_fee": 495,
            "declined_reason": "Customer objected to annual fee",
            "declined_date": "2024-01-15",
        },
    },
    # Transaction summary
    {
        "namespace": "/bank/customers/customer_001/transactions/summary",
        "content": {
            "monthly_avg": {
                "travel": 2400,
                "dining": 800,
                "groceries": 600,
                "gas": 200,
                "other": 500,
            },
            "total_monthly": 4500,
            "period": "last_6_months",
        },
    },
    # Preferences
    {
        "namespace": "/bank/customers/customer_001/preferences",
        "content": {
            "max_annual_fee": 100,
            "priority_categories": ["travel", "dining"],
            "stated": "Prefers cards with low annual fees but good travel benefits",
        },
    },
    # Life events
    {
        "namespace": "/bank/customers/customer_001/life_events",
        "content": {
            "events": [
                {
                    "type": "upcoming_travel",
                    "details": "Japan trip booked for next month",
                    "detected": "2024-02-01",
                },
                {
                    "type": "spending_increase",
                    "category": "travel",
                    "change": "+40%",
                    "detected": "2024-01-15",
                },
            ]
        },
    },
]

# Customer 002: High income, values premium benefits
customer_002_data = [
    {
        "namespace": "/bank/customers/customer_002/recommendations/pending",
        "content": {
            "product": "Premium Platinum Card",
            "annual_fee": 495,
            "benefits": "5x all travel, lounge access, $300 travel credit",
            "reason": "High income segment, luxury spending patterns",
        },
    },
    {
        "namespace": "/bank/customers/customer_002/recommendations/accepted",
        "content": {
            "product": "Business Rewards",
            "annual_fee": 150,
            "accepted_date": "2024-01-20",
            "reason": "Business expenses detected",
        },
    },
    {
        "namespace": "/bank/customers/customer_002/transactions/summary",
        "content": {
            "monthly_avg": {
                "travel": 5000,
                "dining": 2000,
                "luxury": 3000,
                "business": 4000,
                "other": 1000,
            },
            "total_monthly": 15000,
            "period": "last_6_months",
        },
    },
    {
        "namespace": "/bank/customers/customer_002/preferences",
        "content": {
            "max_annual_fee": "no_limit",
            "priority_categories": ["travel", "luxury", "business"],
            "stated": "Values premium benefits and status, fee is not a concern",
        },
    },
    {
        "namespace": "/bank/customers/customer_002/life_events",
        "content": {
            "events": [
                {
                    "type": "business_growth",
                    "details": "Business expenses up 60%",
                    "detected": "2024-02-01",
                }
            ]
        },
    },
]

# Customer 003: Student, very price-sensitive
customer_003_data = [
    {
        "namespace": "/bank/customers/customer_003/recommendations/pending",
        "content": {
            "product": "No-Fee Starter Card",
            "annual_fee": 0,
            "benefits": "1% cashback on everything",
            "reason": "Alternative after fee objection",
        },
    },
    {
        "namespace": "/bank/customers/customer_003/recommendations/declined",
        "content": {
            "product": "Student Card",
            "annual_fee": 95,
            "declined_reason": "Annual fee too high",
            "declined_date": "2024-02-01",
        },
    },
    {
        "namespace": "/bank/customers/customer_003/transactions/summary",
        "content": {
            "monthly_avg": {
                "groceries": 300,
                "dining": 150,
                "entertainment": 100,
                "transport": 80,
                "other": 70,
            },
            "total_monthly": 700,
            "period": "last_6_months",
        },
    },
    {
        "namespace": "/bank/customers/customer_003/preferences",
        "content": {
            "max_annual_fee": 0,
            "priority_categories": ["groceries", "dining"],
            "stated": "Student budget, absolutely no annual fees",
        },
    },
    {
        "namespace": "/bank/customers/customer_003/life_events",
        "content": {
            "events": [
                {
                    "type": "student",
                    "details": "University student",
                    "detected": "2023-09-01",
                }
            ]
        },
    },
]

all_data = customer_001_data + customer_002_data + customer_003_data
print(f"📊 Prepared {len(all_data)} records across 3 customers and 4 namespace types")


records = []
current_time = datetime.now().timestamp()

for idx, item in enumerate(all_data):
    records.append(
        {
            "requestIdentifier": f"record_{idx:03d}",
            "namespaces": [item["namespace"]],
            "content": {"text": json.dumps(item["content"])},
            "timestamp": current_time + idx,
        }
    )

response = agentcore_client.batch_create_memory_records(memoryId=MEMORY_ID, records=records)

print(f"✅ Created {len(response['successfulRecords'])} records")
print("   Namespace types: recommendations, transactions/summary, preferences, life_events")


# ## 3. Define Agent Tools
#
# We create a single flexible tool that can query any namespace path. The agent decides:
# - Which namespace type to query (recommendations, transactions, preferences, life_events)
# - What sub-path to use (e.g., `recommendations/pending` vs `recommendations/declined`)
#
# This is fundamentally different from SQL — there's no fixed schema or query plan. The agent constructs the namespace path dynamically based on the conversation.


time.sleep(30)
session_manager = MemorySessionManager(memory_id=MEMORY_ID, region_name=REGION)

from strands.types.tools import ToolContext  # noqa: E402


@tool(context=True)
def query_customer_memory(namespace_type: str, tool_context: ToolContext) -> str:
    """Query customer data from a specific namespace type.

    Args:
        namespace_type: The type of data to query. Options:
            - 'recommendations/pending' - pending card recommendations
            - 'recommendations/declined' - previously declined recommendations
            - 'recommendations/accepted' - accepted recommendations
            - 'transactions/summary' - monthly spending by category
            - 'preferences' - customer stated preferences
            - 'life_events' - detected life events and changes
            - 'all' - query all data for the customer
        tool_context: Provides access to customer_id via invocation_state.

    Returns:
        JSON string containing the matching data.
    """
    customer_id = tool_context.invocation_state.get("customer_id")
    if not customer_id:
        return json.dumps({"error": "No customer_id in context"})

    if namespace_type == "all":
        namespace_prefix = f"/bank/customers/{customer_id}/"
    else:
        ns = namespace_type.rstrip("/")
        namespace_prefix = f"/bank/customers/{customer_id}/{ns}"

    records = session_manager.list_long_term_memory_records(namespace_prefix=namespace_prefix, max_results=100)

    results = [json.loads(r["content"]["text"]) for r in records]
    return json.dumps(
        {
            "customer_id": customer_id,
            "namespace_queried": namespace_type,
            "count": len(results),
            "data": results,
        },
        indent=2,
    )


print("✅ Defined query_customer_memory tool")


# ## 4. Create the Personalisation Agent
#
# The agent has:
# - `query_customer_memory` — to fetch data from any namespace type
# - `python_repl` — to perform calculations (e.g., potential savings from card benefits)
#
# The system prompt guides the agent to reason across multiple data sources.


SYSTEM_PROMPT = """You are a personalised banking assistant. You help customers understand their credit card options and make informed decisions.

You have access to multiple types of customer data via query_customer_memory:
- recommendations/pending, recommendations/declined, recommendations/accepted
- transactions/summary (monthly spending by category)
- preferences (stated preferences like max annual fee)
- life_events (detected events like upcoming travel)

When answering questions:
1. Query the relevant namespace(s) to gather context
2. Cross-reference data sources (e.g., check if a recommendation aligns with preferences)
3. Use python_repl for calculations when needed (e.g., potential rewards based on spending)
4. Explain your reasoning in customer-friendly terms

The customer's identity is already known - you don't need to ask for it.
"""

model = BedrockModel(model_id=MODEL_ID, region_name=REGION)


def create_agent():
    """Create a fresh agent instance (no conversation history)."""
    return Agent(
        model=model,
        tools=[query_customer_memory, python_repl],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
    )


agent = create_agent()
print("\u2705 Created personalisation agent")


# ## 5. Multi-Namespace Reasoning Demo
#
# Now let's see the agent reason across multiple data sources. These queries require the agent to:
# 1. Decide which namespaces to query
# 2. Cross-reference information from different sources
# 3. Generate code for calculations
#
# This is impossible with a single SQL query — the agent dynamically composes context.


# Customer 001: Frequent traveler, price-sensitive
current_customer = "customer_001"
print(f"🧑 Chatting as: {current_customer}\n")
print("=" * 60)

# Query 1: Requires cross-referencing recommendations with preferences
response = agent(
    "What credit card do you recommend for me? Make sure it fits my preferences.",
    customer_id=current_customer,
)
print(f"\n💬 Response:\n{response}")


# Query 2: Requires transactions + recommendations + calculation
response = agent(
    "How much would I earn in rewards with the Travel Rewards Card based on my actual spending?",
    customer_id=current_customer,
)
print(f"\n💬 Response:\n{response}")


# Query 4: Requires declined history + preferences to explain
response = agent(
    "Why was I recommended the Travel Rewards Card instead of the Premium Platinum?",
    customer_id=current_customer,
)
print(f"\n💬 Response:\n{response}")


# ## 6. Switch Customer Context
#
# Each customer gets a fresh agent instance to avoid conversation history from previous customers bleeding into the context.


# Customer 003: Student, very price-sensitive
agent = create_agent()
current_customer = "customer_003"
print(f"\n\U0001f9d1 Now chatting as: {current_customer}\n")
print("=" * 60)

response = agent(
    "I'm a student on a tight budget. What card options do I have? I really can't afford any annual fees.",
    customer_id=current_customer,
)
print(f"\n\U0001f4ac Response:\n{response}")


# Customer 002: High income, values premium
agent = create_agent()
current_customer = "customer_002"
print(f"\n\U0001f9d1 Now chatting as: {current_customer}\n")
print("=" * 60)

response = agent(
    "I travel a lot for business. What's your best premium card? I don't care about the fee if the benefits are worth it.",
    customer_id=current_customer,
)
print(f"\n\U0001f4ac Response:\n{response}")


# ## Summary
#
# ### What We Demonstrated
#
# 1. **Multi-namespace reasoning** — The agent queries recommendations, transactions, preferences, and life events, cross-referencing them to provide contextual answers
#
# 2. **Dynamic namespace selection** — The agent decides which namespaces to query based on the question (not a fixed query plan)
#
# 3. **Code generation for calculations** — The agent uses `python_repl` to calculate potential rewards based on actual spending data
#
# 4. **Deterministic customer context** — Customer ID flows through `invocation_state`, enabling easy context switching
#
# ### Why This Can't Be Done with SQL
#
# - **No fixed query plan** — The agent decides what to fetch based on the question
# - **Cross-source reasoning** — Joining recommendations with preferences with transactions requires LLM reasoning, not SQL joins
# - **Semantic namespace paths** — The agent understands `/preferences` vs `/life_events` from the path name
# - **Zero schema changes** — Adding `/complaints` tomorrow requires no migrations
#
# ### What This Enables
#
# - Real-time personalised experiences that adapt to each customer's full context
# - Natural language queries over multi-source business data
# - Easy integration of new data sources without schema redesign
# - Agent-native data access patterns
#
# **→ Continue to Part 2** for cross-customer analytics, product-level insights, and marketing use cases.

# ## 7. Cleanup (Optional)
#
# When you're done experimenting, clean up the resources created in this tutorial:


try:
    memory_client.delete_memory_and_wait(memory_id=MEMORY_ID)
    print(f"✅ Deleted memory resource: {MEMORY_ID}")
except Exception as e:
    print(f"❌ Error deleting memory: {e}")
