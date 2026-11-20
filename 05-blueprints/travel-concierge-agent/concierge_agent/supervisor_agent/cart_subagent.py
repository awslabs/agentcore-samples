"""
Cart Subagent

A subagent that handles cart and payment operations by connecting to cart tools
via the gateway. Exposed as a tool for the main supervisor agent.
"""

import os
import logging
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from gateway_client import get_gateway_client

logger = logging.getLogger(__name__)

REGION = os.getenv("AWS_REGION", "us-east-1")

# =============================================================================
# CART AGENT SYSTEM PROMPT
# =============================================================================

CART_AGENT_PROMPT = """
You are a helpful assistant for an e-commerce shopping cart system.
Help users manage their shopping carts and answer any questions about products, orders, and cart operations. 
For reference today's date is December 4th, 2025.

Your primary responsibilities include:
1. Adding items to the shopping cart
2. Removing items from the cart
3. Viewing cart contents
4. Clearing the entire cart
5. Processing payments via Visa tokenization
6. Onboarding new payment cards

You have access to the following tools:
- `cart_add_to_cart`: Add items to the user's cart
  IMPORTANT: When adding items, you MUST set item_type correctly:
    - For hotels: include "item_type": "hotel"
    - For flights: include "item_type": "flight"
    - For regular products: include "item_type": "product" (or omit, defaults to product)
- `cart_remove_from_cart`: Remove specific items from cart
- `cart_view_cart`: Display current cart contents
- `cart_clear_cart`: Empty the entire cart
- `cart_request_purchase_confirmation`: User confirmations that MUST be run before confirm_purchase
- `cart_confirm_purchase`: Process payment for cart items
- `cart_onboard_card`: Securely onboard a new payment card
- `cart_get_visa_iframe_config`: Get Visa iframe configuration for card entry

IMPORTANT GUIDELINES:

1. Always confirm cart operations with the user
2. For checkout, ensure the user has a payment method onboarded
3. When onboarding cards, explain the secure tokenization process
4. Provide clear feedback on successful operations
5. Handle errors gracefully and suggest next steps
6. Never store or log actual card numbers
7. Use Visa tokenization for all payment processing
8. You MUST always call request_purchase_confirmation before confirm_purchase

When responding:
- Be clear about what items are in the cart
- Show prices and totals when relevant
- Confirm successful operations
- Ask for missing information (like card details for checkout)
- Explain security features when handling payments

<instructions>
- Think step by step.
- Never use placeholder or mock product information.
- Use the provided tools to address user's requests.
- You should not use made-up or placeholder arguments.
- Use the tools multiple times for the user query, if needed. check the operation is done corrrectly, by getting the cart items at the end.

- PURCHASE FLOW (TWO STEPS WITH CARD CHECK):
  Step 1: When user expresses purchase intent ("buy it", "checkout", "purchase", etc.):
    *** MANDATORY FIRST STEP ***
    * Call check_user_has_payment_card() FIRST to check if user has a card

    * IF user has NO card (has_card: false):
      - Say EXACTLY: "You don't have a payment card on file. Please click the button below to add a card securely."
      - *** ABSOLUTELY FORBIDDEN *** NEVER ask for card number, CVV, expiration, or ANY card details in chat
      - *** ABSOLUTELY FORBIDDEN *** DO NOT say "I'll need" or "please provide" card info
      - STOP HERE - DO NOT proceed with purchase

    * IF user HAS a card (has_card: true):
      - Call request_purchase_confirmation() to prepare the purchase summary
      - If there are per night purchases, summarize these, like 800 per night for hotel for 3 nights 800*3 = 2400
      - Present the summary to the user and ask them to confirm

  Step 2: Only after user explicitly confirms ("yes", "confirm", "proceed"):
    * Call confirm_purchase() to execute the transaction

  *** CRITICAL RULES ***
  * NEVER ask for card details in chat - EVER
  * NEVER call confirm_purchase() without user confirmation
  * If user says "no" or "cancel", acknowledge and don't proceed

- CARD ONBOARDING:
  * *** ABSOLUTELY FORBIDDEN *** NEVER ask for card details in chat
  * When user wants to add a card: Say "Please click the button to add your card securely."
  * The UI handles card entry - you just tell user to click the button
</instructions>

Your primary goal is to ensure accurate and efficient cart operations with clear feedback to the user.
"""


# =============================================================================
# GATEWAY CLIENT FOR CART TOOLS
# =============================================================================


def get_cart_tools_client() -> MCPClient:
    """Get MCPClient filtered for cart tools only."""
    return get_gateway_client("^carttools___")


# =============================================================================
# BEDROCK MODEL
# =============================================================================

bedrock_model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    region_name=REGION,
    temperature=0.1,
)


# =============================================================================
# CART SUBAGENT TOOL
# =============================================================================


@tool
async def cart_manager(query: str, user_id: str = "", session_id: str = ""):
    """
    Process shopping cart and payment operations.

    AVAILABLE TOOLS:
    - get_cart: View cart contents (user_id)
    - add_to_cart: Add products (user_id, items list with asin/title/price)
    - add_hotel_to_cart: Add hotels (user_id, hotels list with title/price/hotel_id/city_code)
    - add_flight_to_cart: Add flights (user_id, flights list with title/price/flight_id/origin/destination/departure_date)
    - remove_from_cart: Remove items (user_id, identifiers list, item_type: product/hotel/flight)
    - clear_cart: Empty cart (user_id)
    - check_user_has_payment_card: Verify payment method (user_id)
    - request_purchase_confirmation: Pre-checkout summary (user_id)
    - confirm_purchase: Execute purchase after confirmation (user_id)
    - onboard_card: Add payment card (user_id, card details)
    - get_visa_iframe_config: Get secure card entry config (user_id)

    ROUTE HERE FOR:
    - View cart: "What's in my cart?", "Show my cart"
    - Add items: "Add this hotel to cart", "Add the flight"
    - Remove items: "Remove the hotel", "Take out the flight"
    - Clear cart: "Empty my cart", "Clear everything"
    - Checkout: "Buy these items", "Checkout", "Purchase"
    - Payment: "Add a payment card", "Setup payment method"

    Args:
        query: The cart/payment request.
        user_id: User identifier (REQUIRED for all cart operations).
        session_id: Session identifier for context.

    Returns:
        Cart operation result or payment status.
    """
    try:
        logger.info(f"Cart subagent (async) processing: {query[:100]}...")

        prompt_with_context = f"""{CART_AGENT_PROMPT}

        CRITICAL: You are currently serving user_id: {user_id}

        EVERY tool call MUST include user_id as the first parameter.
        Example tool calls:
        - get_cart(user_id="{user_id}")
        - clear_cart(user_id="{user_id}")
        - add_to_cart(user_id="{user_id}", items=[{{"asin": "123", "title": "Product", "price": "$10", "item_type": "product"}}])
        - add_to_cart(user_id="{user_id}", items=[{{"asin": "", "title": "Hotel Name", "price": "$100", "item_type": "hotel", "hotel_id": "h123", "city_code": "NYC"}}])
        - remove_from_cart(user_id="{user_id}", identifiers=[...], item_type="product")

        DO NOT ask the user for their user_id - you already have it: {user_id}"""

        cart_client = get_cart_tools_client()

        agent = Agent(
            name="cart_agent",
            model=bedrock_model,
            tools=[cart_client],
            system_prompt=prompt_with_context,
            trace_attributes={
                "user.id": user_id,
                "session.id": session_id,
                "agent.type": "cart_subagent",
            },
        )

        result = ""
        async for event in agent.stream_async(query):
            if "data" in event:
                yield {"data": event["data"]}
            if "current_tool_use" in event:
                yield {"current_tool_use": event["current_tool_use"]}
            if "result" in event:
                result = str(event["result"])

        yield {"result": result}

    except Exception as e:
        logger.error(f"Cart subagent async error: {e}", exc_info=True)
        yield {"error": str(e)}
