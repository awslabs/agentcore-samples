#!/usr/bin/env python

# # Amazon Bedrock AgentCore Memory for Cross-Customer Analytics
#
# ## Overview
#
# This tutorial demonstrates how to use [AgentCore Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html) namespaces for cross-customer analytics. While Part 1 showed customer-facing personalisation (one customer at a time), this notebook flips the perspective — an admin/marketing agent queries across **all customers** to find patterns, measure funnel effectiveness, and recommend new products.
#
# ### Why This Matters
#
# Traditional analytics requires ETL pipelines, data warehouses, and pre-built dashboards. With AgentCore Memory namespaces, an agent can:
# - Query across all customers dynamically — no pre-aggregated tables needed
# - Reason across multiple data dimensions (spending, preferences, declines, life events)
# - Generate insights in natural language — no SQL or BI tool expertise required
# - Answer ad-hoc questions that weren't anticipated when the dashboard was built
#
#
# ### Tutorial Details
#
# | Information         | Details                                                    |
# |:--------------------|:-----------------------------------------------------------|
# | Tutorial type       | Cross-Customer Analytics with Memory                       |
# | Feature             | Long-Term Memory Namespaces + Strands Agent                |
# | Key features        | Cross-Customer Queries, Funnel Analytics, Product Gap Analysis |
# | Example complexity  | Intermediate                                               |
# | SDK used            | boto3, bedrock-agentcore, strands-agents, strands-agents-tools |
#
# ### What You'll Learn
#
# 1. Query across all customers using broad namespace prefixes
# 2. Build a tool that iterates over known customers to aggregate data
# 3. Analyse recommendation funnels (pending → accepted → declined)
# 4. Identify product gaps from decline reasons and unmet preferences
# 5. Use `python_repl` for cross-customer statistical analysis
#
# ### Architecture
#
# ![Architecture](architecture.png)
#
# ### How It Works
#
# The admin agent uses the same namespace structure as Part 1, but queries differently:
#
# ```
# Part 1 (customer-facing):  /bank/customers/customer_001/preferences     → one customer
# Part 2 (admin analytics):  /bank/customers/{each_customer}/preferences  → aggregate all
# ```
#
# The agent iterates over known customers, pulls data from each, and reasons across the combined dataset.

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


import json
import os
import boto3
import time
import uuid
from datetime import datetime
from bedrock_agentcore.memory.session import MemorySessionManager
from bedrock_agentcore.memory.client import MemoryClient
from strands import Agent, tool
from strands.models.bedrock import BedrockModel
from strands_tools import python_repl

# Suppress python_repl confirmation prompts and interactive mode
os.environ["BYPASS_TOOL_CONSENT"] = "true"
os.environ["PYTHON_REPL_INTERACTIVE"] = "false"

REGION = "us-west-2"
MODEL_ID = "global.anthropic.claude-opus-4-6-v1"
KNOWN_CUSTOMERS = ["customer_001", "customer_002", "customer_003"]

agentcore_client = boto3.client("bedrock-agentcore", region_name=REGION)
memory_client = MemoryClient(region_name=REGION)
print(f"✅ Initialized clients for region: {REGION}")


# ## 1. Create Memory Instance and Populate Data
#
# This notebook is self-contained — it creates its own memory instance and populates it with sample data representing 3 customer profiles.


unique_name = f"analytics_demo_{uuid.uuid4().hex[:8]}"
memory = memory_client.create_memory_and_wait(name=unique_name, strategies=[])
MEMORY_ID = memory["memoryId"]
print(f"✅ Created memory: {MEMORY_ID}")


# ### Sample Data
#
# We populate the same multi-source customer data used in Part 1. Each customer has records across 4 namespace types: recommendations, transactions, preferences, and life events.


# Customer 001: Frequent traveler, price-sensitive
customer_001_data = [
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
    {
        "namespace": "/bank/customers/customer_001/preferences",
        "content": {
            "max_annual_fee": 100,
            "priority_categories": ["travel", "dining"],
            "stated": "Prefers cards with low annual fees but good travel benefits",
        },
    },
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
print(f"📊 Prepared {len(all_data)} records across 3 customers")


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
print("   Namespaces: recommendations, transactions, preferences, life_events")
print("   Customers: customer_001, customer_002, customer_003")


# ### Wait for Records to be Indexed
#
# AgentCore Memory needs time to index the records before they're queryable. We poll until records appear:


session_manager = MemorySessionManager(memory_id=MEMORY_ID, region_name=REGION)

print("Waiting for records to be indexed...")
for attempt in range(20):
    records = session_manager.list_long_term_memory_records(
        namespace_prefix="/bank/customers/customer_001/recommendations/pending",
        max_results=10,
    )
    if len(records) > 0:
        print(f"✅ Records indexed after {(attempt + 1) * 15}s")
        break
    print(f"  Attempt {attempt + 1}/20 - not ready yet, waiting 15s...")
    time.sleep(15)
else:
    print("⚠️ Records not yet indexed. Wait a bit longer and re-run this cell.")

# Verify all customers
for cid in KNOWN_CUSTOMERS:
    r = session_manager.list_long_term_memory_records(namespace_prefix=f"/bank/customers/{cid}", max_results=100)
    print(f"  {cid}: {len(r)} records")


# ## 2. Define Admin Tools
#
# The key difference from Part 1: instead of querying one customer's namespace, the admin tool queries across **all customers** for a given namespace type and aggregates the results.


@tool
def query_all_customers(namespace_type: str) -> str:
    """Query a specific namespace type across ALL customers and aggregate results.

    Args:
        namespace_type: The type of data to query across all customers. Options:
            - 'recommendations/pending' - pending recommendations for all customers
            - 'recommendations/declined' - declined recommendations for all customers
            - 'recommendations/accepted' - accepted recommendations for all customers
            - 'transactions/summary' - spending summaries for all customers
            - 'preferences' - stated preferences for all customers
            - 'life_events' - detected life events for all customers
            - 'all' - all data for all customers

    Returns:
        JSON with aggregated data from all customers.
    """
    all_results = {}
    for customer_id in KNOWN_CUSTOMERS:
        if namespace_type == "all":
            prefix = f"/bank/customers/{customer_id}"
        else:
            prefix = f"/bank/customers/{customer_id}/{namespace_type}"

        records = session_manager.list_long_term_memory_records(namespace_prefix=prefix, max_results=100)
        all_results[customer_id] = [json.loads(r["content"]["text"]) for r in records]

    return json.dumps(
        {
            "namespace_queried": namespace_type,
            "customers_queried": len(KNOWN_CUSTOMERS),
            "data": all_results,
        },
        indent=2,
    )


print("✅ Defined query_all_customers tool")


# ## 3. Create the Admin Analytics Agent
#
# The system prompt guides the agent to think like a marketing analyst — looking for patterns, gaps, and opportunities across the customer base.


ADMIN_SYSTEM_PROMPT = """You are a marketing analytics agent for a bank's credit card division. You analyse customer data across the entire customer base to find patterns, measure effectiveness, and recommend business actions.

You have access to aggregated customer data via query_all_customers

When answering questions:
1. Query the relevant namespace(s)
2. Use python_repl for ALL calculations, aggregations, and statistical analysis
3. Identify patterns across customers — don't just list individual data points
4. Provide actionable business recommendations backed by data
5. When recommending new products, base it on gaps between what customers want and what was declined

You are analysing a portfolio of customers, not serving an individual.
"""

model = BedrockModel(model_id=MODEL_ID, region_name=REGION)


def create_admin_agent():
    return Agent(
        model=model,
        tools=[query_all_customers, python_repl],
        system_prompt=ADMIN_SYSTEM_PROMPT,
        callback_handler=None,
    )


# ## 4. Cross-Customer Analytics Queries
#
# These queries demonstrate the agent reasoning across multiple customers simultaneously. Each query creates a fresh agent to avoid context bleed.


# ### Recommendation funnel across all customers
response = create_admin_agent()(
    "For every customer in the base, count the pending, accepted and declined "
    "recommendations, using namespace types 'recommendations/pending', "
    "'recommendations/accepted' and 'recommendations/declined'. Report the totals, "
    "the acceptance rate and the decline rate. Use python_repl for the arithmetic."
)
print(response)


# ### Common decline reasons
response = create_admin_agent()(
    "Fetch declined recommendations across every customer from namespace type "
    "'recommendations/declined'. Cluster the decline reasons into themes and rank "
    "them by frequency. Use python_repl if you need to count."
)
print(response)


# ### Cross-customer spending patterns
response = create_admin_agent()(
    "Fetch the spending summaries for every customer from namespace type "
    "'transactions/summary'. Compute the spend per category across the base and "
    "flag the top 2 categories by total spend."
)
print(response)


# ### Product-gap analysis
response = create_admin_agent()(
    "Combine each customer's stated preferences ('preferences'), their declined "
    "recommendations ('recommendations/declined') and their spending summaries "
    "('transactions/summary') to propose 2-3 product ideas that would plausibly "
    "reduce declines. Reason across the whole base, not one customer at a time."
)
print(response)


# ## Summary
#
# ### What We Demonstrated
#
# 1. **Cross-customer querying** — The admin agent queries the same namespace structure as Part 1, but across all customers simultaneously
# 2. **Funnel analytics** — Measuring recommendation effectiveness (pending → accepted → declined) without pre-built dashboards
# 3. **Pattern detection** — Finding common decline reasons and underserved segments through LLM reasoning
# 4. **Product gap analysis** — Using preferences, declines, and spending data to recommend what to build next
# 5. **Dynamic code generation** — `python_repl` for statistical calculations the agent decides are needed
#
# ### Key Insight
#
# The same namespace structure that serves individual customers in Part 1 powers portfolio-level analytics here. No ETL, no schema changes, no new data pipelines — just a different query pattern and system prompt.
#
# By combining dynamic namespace selection (the agent decides what data to fetch) with dynamic code generation (the agent decides how to analyse it), you get ad-hoc analytics without the ETL pipelines, pre-built schemas, or fixed dashboards that traditional BI architectures require.

# ## 5. Cleanup
#
# Clean up the memory resource created in this tutorial:


try:
    memory_client.delete_memory_and_wait(memory_id=MEMORY_ID)
    print(f"✅ Deleted memory resource: {MEMORY_ID}")
except Exception as e:
    print(f"⚠️ Cleanup error: {e}")
