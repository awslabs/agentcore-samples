"""
Customer Support Agent — Getting Started Sample

This is the agent code you'll copy into app/CustomerSupport/main.py after
running `agentcore create`. It replaces the generated sample with a customer
support agent that has two tools: product lookup and return policy lookup.

Usage:
  1. agentcore create --name CustomerSupport --framework Strands --model-provider Bedrock --defaults
  2. cd CustomerSupport
  3. Copy this file to app/CustomerSupport/main.py
  4. agentcore dev          # test locally
  5. agentcore deploy       # deploy to AWS
  6. agentcore invoke "What products do you have?" --stream
"""

from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from model.load import load_model

app = BedrockAgentCoreApp()
log = app.logger

# --- Product & Policy Data ---

RETURN_POLICIES = {
    "electronics": {
        "window": "30 days",
        "condition": "Original packaging required, must be unused or defective",
        "refund": "Full refund to original payment method",
    },
    "accessories": {
        "window": "14 days",
        "condition": "Must be in original packaging, unused",
        "refund": "Store credit or exchange",
    },
    "audio": {
        "window": "30 days",
        "condition": "Defective items only after 15 days",
        "refund": "Full refund within 15 days, replacement after",
    },
}

PRODUCTS = {
    "PROD-001": {
        "name": "Wireless Headphones",
        "price": 79.99,
        "category": "audio",
        "description": "Noise-cancelling Bluetooth headphones with 30h battery life",
        "warranty_months": 12,
    },
    "PROD-002": {
        "name": "Smart Watch",
        "price": 249.99,
        "category": "electronics",
        "description": "Fitness tracker with heart rate monitor, GPS, and 5-day battery",
        "warranty_months": 24,
    },
    "PROD-003": {
        "name": "Laptop Stand",
        "price": 39.99,
        "category": "accessories",
        "description": "Adjustable aluminum laptop stand for ergonomic desk setup",
        "warranty_months": 6,
    },
    "PROD-004": {
        "name": "USB-C Hub",
        "price": 54.99,
        "category": "accessories",
        "description": "7-in-1 USB-C hub with HDMI, USB-A, SD card reader, and ethernet",
        "warranty_months": 12,
    },
    "PROD-005": {
        "name": "Mechanical Keyboard",
        "price": 129.99,
        "category": "electronics",
        "description": "RGB mechanical keyboard with Cherry MX switches",
        "warranty_months": 24,
    },
}


# --- Tools ---
# The @tool decorator turns a Python function into a tool the LLM can call.
# The docstring is what the model reads to decide when and how to use the tool.


@tool
def get_return_policy(product_category: str) -> str:
    """Get return policy information for a specific product category.

    Args:
        product_category: Product category (e.g., 'electronics', 'accessories', 'audio')

    Returns:
        Formatted return policy details including timeframes and conditions
    """
    category = product_category.lower()
    if category in RETURN_POLICIES:
        policy = RETURN_POLICIES[category]
        return (
            f"Return policy for {category}: Window: {policy['window']}, "
            f"Condition: {policy['condition']}, Refund: {policy['refund']}"
        )
    return f"No specific return policy found for '{product_category}'. Please contact support."


@tool
def get_product_info(query: str) -> str:
    """Search for product information by name, ID, or keyword.

    Args:
        query: Product name, ID (e.g., 'PROD-001'), or search keyword

    Returns:
        Product details including name, price, category, and description
    """
    query_lower = query.lower()
    # Search by product ID
    if query.upper() in PRODUCTS:
        p = PRODUCTS[query.upper()]
        return (
            f"{p['name']} ({query.upper()}): ${p['price']}, Category: {p['category']}, "
            f"{p['description']}, Warranty: {p['warranty_months']} months"
        )
    # Search by keyword
    results = [
        f"{pid}: {p['name']} - ${p['price']} - {p['description']}"
        for pid, p in PRODUCTS.items()
        if query_lower in p["name"].lower()
        or query_lower in p["description"].lower()
        or query_lower in p["category"].lower()
    ]
    if results:
        return "Found products:\n" + "\n".join(results)
    return f"No products found matching '{query}'."


# --- Agent Setup ---

SYSTEM_PROMPT = """You are a helpful and professional customer support assistant for an e-commerce company.

Your role is to:
- Provide accurate information using the tools available to you
- Be friendly, patient, and understanding with customers
- Always offer additional help after answering questions

You have access to:
1. get_return_policy() - Look up return policies by product category
2. get_product_info() - Search product information by name, ID, or keyword

Always use the appropriate tool rather than guessing."""

_agent = None


def get_or_create_agent():
    global _agent
    if _agent is None:
        _agent = Agent(
            model=load_model(),
            system_prompt=SYSTEM_PROMPT,
            tools=[get_return_policy, get_product_info],
        )
    return _agent


@app.entrypoint
async def invoke(payload, context):
    log.info("Invoking Agent...")
    agent = get_or_create_agent()
    stream = agent.stream_async(payload.get("prompt"))
    async for event in stream:
        if "data" in event and isinstance(event["data"], str):
            yield event["data"]


if __name__ == "__main__":
    app.run()
