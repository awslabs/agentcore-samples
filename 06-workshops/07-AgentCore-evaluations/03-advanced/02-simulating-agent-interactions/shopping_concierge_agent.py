"""Shopping Concierge agent: used by Strands-AgentCore-ShoppingConcierge.ipynb.

A Strands agent with eight tools over a deterministic mock product catalog,
mock shopping carts, and mock orders. The mock data makes evaluation results
fully reproducible across runs.

Deployed to AgentCore Runtime via the `agentcore` CLI.
See Step 3 of `Strands-AgentCore-ShoppingConcierge.ipynb`.
"""

from strands import Agent, tool
from strands.models.bedrock import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
DEFAULT_SYSTEM_PROMPT = (
    "You are a shopping concierge assistant. "
    "Help customers find products, manage their shopping cart, and complete purchases."
)

# ---------------------------------------------------------------------------
# Mock product catalog
# ---------------------------------------------------------------------------
PRODUCTS = {
    "PROD-001": {
        "name": "Wireless Noise-Cancelling Headphones",
        "category": "Electronics",
        "price": 79.99,
        "rating": 4.5,
        "stock": 15,
        "description": "Over-ear headphones with 30hr battery, active noise cancellation.",
    },
    "PROD-002": {
        "name": "Running Shoes (Size 10)",
        "category": "Footwear",
        "price": 89.99,
        "rating": 4.3,
        "stock": 8,
        "description": "Lightweight trail running shoes, breathable mesh upper.",
    },
    "PROD-003": {
        "name": "Stainless Steel Water Bottle 32oz",
        "category": "Kitchen",
        "price": 24.99,
        "rating": 4.7,
        "stock": 50,
        "description": "Vacuum insulated, keeps cold 24h / hot 12h. BPA-free.",
    },
    "PROD-004": {
        "name": "Yoga Mat Premium 6mm",
        "category": "Sports",
        "price": 34.99,
        "rating": 4.6,
        "stock": 20,
        "description": "Non-slip surface, extra thick 6mm cushioning, carrying strap included.",
    },
    "PROD-005": {
        "name": "USB-C Hub 7-in-1",
        "category": "Electronics",
        "price": 45.99,
        "rating": 4.2,
        "stock": 30,
        "description": "4K HDMI, 100W PD, 2x USB-A 3.0, SD/MicroSD, USB-C data.",
    },
    "PROD-006": {
        "name": "Cotton Crew-Neck T-Shirt (L)",
        "category": "Clothing",
        "price": 19.99,
        "rating": 4.4,
        "stock": 100,
        "description": "100% organic cotton, preshrunk, available in 12 colors.",
    },
    "PROD-007": {
        "name": "Smart LED Desk Lamp",
        "category": "Home",
        "price": 39.99,
        "rating": 4.5,
        "stock": 25,
        "description": "Touch dimmer, 5 color temps, USB charging port, memory function.",
    },
    "PROD-008": {
        "name": "Coffee Grinder Burr Electric",
        "category": "Kitchen",
        "price": 54.99,
        "rating": 4.6,
        "stock": 12,
        "description": "Conical burr, 18 grind settings, 60g hopper, quiet motor.",
    },
    "PROD-009": {
        "name": "Resistance Bands Set (5 levels)",
        "category": "Sports",
        "price": 22.99,
        "rating": 4.4,
        "stock": 40,
        "description": "Natural latex, 10-50 lb resistance levels, includes carry bag.",
    },
    "PROD-010": {
        "name": "Mechanical Keyboard TKL",
        "category": "Electronics",
        "price": 129.99,
        "rating": 4.7,
        "stock": 10,
        "description": "Tenkeyless, Cherry MX Red switches, RGB backlight, USB-C detachable.",
    },
}

# Mock cart: session_id -> list of {product_id, quantity, price_each}
_carts: dict = {}

# Mock orders
_orders = {
    "ORD-SC-001": {
        "status": "delivered",
        "items": [{"product_id": "PROD-001", "qty": 1}],
        "total": 79.99,
        "delivered": "2026-04-08",
    },
    "ORD-SC-002": {
        "status": "in_transit",
        "items": [{"product_id": "PROD-003", "qty": 2}],
        "total": 49.98,
        "est_delivery": "2026-04-14",
        "tracking": "1Z999AA1012345678",
    },
    "ORD-SC-003": {
        "status": "processing",
        "items": [
            {"product_id": "PROD-007", "qty": 1},
            {"product_id": "PROD-006", "qty": 2},
        ],
        "total": 79.97,
        "placed": "2026-04-10",
    },
}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def search_products(query: str, category: str = None, max_price: float = None) -> str:
    """Search the product catalog by keyword, optionally filtered by category and max price."""
    query_lower = query.lower()
    results = []
    for pid, p in PRODUCTS.items():
        if category and p["category"].lower() != category.lower():
            continue
        if max_price and p["price"] > max_price:
            continue
        # Simple keyword match against name + description + category
        text = f"{p['name']} {p['description']} {p['category']}".lower()
        if any(word in text for word in query_lower.split()):
            results.append(f"{pid}: {p['name']} - ${p['price']:.2f} | Rating: {p['rating']} | Stock: {p['stock']}")
    if not results:
        return f"No products found matching '{query}'" + (f" in category '{category}'" if category else "")
    return "Products found:\n" + "\n".join(results)


@tool
def get_product_details(product_id: str) -> str:
    """Get detailed information about a specific product by its ID."""
    p = PRODUCTS.get(product_id)
    if not p:
        return f"Product {product_id} not found."
    return (
        f"{product_id}: {p['name']}\n"
        f"Category: {p['category']}\n"
        f"Price: ${p['price']:.2f}\n"
        f"Rating: {p['rating']}/5.0\n"
        f"In Stock: {p['stock']} units\n"
        f"Description: {p['description']}"
    )


@tool
def add_to_cart(product_id: str, quantity: int, session_id: str = "default") -> str:
    """Add a product to the shopping cart."""
    p = PRODUCTS.get(product_id)
    if not p:
        return f"Cannot add to cart: product {product_id} not found."
    if p["stock"] < quantity:
        return f"Cannot add {quantity} units - only {p['stock']} in stock."
    cart = _carts.setdefault(session_id, [])
    # Update existing item or add new
    for item in cart:
        if item["product_id"] == product_id:
            item["quantity"] += quantity
            return f"Updated cart: {p['name']} quantity is now {item['quantity']}."
    cart.append({"product_id": product_id, "quantity": quantity, "price_each": p["price"]})
    subtotal = quantity * p["price"]
    return f"Added {quantity}x {p['name']} to cart. Item subtotal: ${subtotal:.2f}."


@tool
def view_cart(session_id: str = "default") -> str:
    """View the current shopping cart contents and total."""
    cart = _carts.get(session_id, [])
    if not cart:
        return "Your cart is empty."
    lines = []
    total = 0.0
    for item in cart:
        p = PRODUCTS.get(item["product_id"], {})
        name = p.get("name", item["product_id"])
        subtotal = item["quantity"] * item["price_each"]
        total += subtotal
        lines.append(f"  - {name} x{item['quantity']} @ ${item['price_each']:.2f} = ${subtotal:.2f}")
    lines.append(f"\nCart Total: ${total:.2f}")
    return "Your cart:\n" + "\n".join(lines)


@tool
def checkout(shipping_address: str, payment_method: str, session_id: str = "default") -> str:
    """Complete checkout for current cart. Returns order confirmation."""
    import uuid

    cart = _carts.get(session_id, [])
    if not cart:
        return "Cannot checkout - cart is empty."
    total = sum(i["quantity"] * i["price_each"] for i in cart)
    order_id = f"ORD-SC-{uuid.uuid4().hex[:6].upper()}"
    # Clear cart after checkout
    _carts[session_id] = []
    return (
        f"Order confirmed! Order ID: {order_id}\n"
        f"Total charged: ${total:.2f}\n"
        f"Payment: {payment_method}\n"
        f"Shipping to: {shipping_address}\n"
        f"Estimated delivery: 3-5 business days. Confirmation email sent."
    )


@tool
def get_order_status(order_id: str) -> str:
    """Check the status of an existing order."""
    order = _orders.get(order_id)
    if not order:
        return f"Order {order_id} not found. Please check your order ID."
    items_desc = ", ".join(
        f"{PRODUCTS.get(i['product_id'], {}).get('name', i['product_id'])} x{i['qty']}" for i in order["items"]
    )
    status = order["status"]
    total = order["total"]
    extra = ""
    if status == "delivered":
        extra = f" Delivered on {order.get('delivered', 'N/A')}."
    elif status == "in_transit":
        extra = f" Est. delivery: {order.get('est_delivery', 'N/A')}. Tracking: {order.get('tracking', 'N/A')}."
    elif status == "processing":
        extra = f" Placed on {order.get('placed', 'N/A')}. Processing in warehouse."
    return f"Order {order_id}: {status.upper()}\nItems: {items_desc}\nTotal: ${total:.2f}.{extra}"


@tool
def track_shipment(order_id: str) -> str:
    """Get detailed shipment tracking for an in-transit order."""
    order = _orders.get(order_id)
    if not order:
        return f"No order found with ID {order_id}."
    if order["status"] != "in_transit":
        return f"Order {order_id} is not currently in transit (status: {order['status']})."
    tracking = order.get("tracking", "N/A")
    est = order.get("est_delivery", "N/A")
    return (
        f"Tracking for {order_id}: {tracking}\n"
        f"Current location: Regional distribution center, Portland OR\n"
        f"Last scan: 2026-04-11 09:23 AM\n"
        f"Estimated delivery: {est}"
    )


@tool
def initiate_return(order_id: str, reason: str) -> str:
    """Initiate a return for a delivered order."""
    order = _orders.get(order_id)
    if not order:
        return f"Order {order_id} not found."
    if order["status"] != "delivered":
        return f"Cannot return order {order_id} - it must be delivered first (current: {order['status']})."
    return (
        f"Return initiated for order {order_id}.\n"
        f"Reason: {reason}\n"
        f"Prepaid return label sent to your email. Please ship within 14 days.\n"
        f"Refund of ${order['total']:.2f} will be processed within 3-5 business days after receipt."
    )


TOOLS = [
    search_products,
    get_product_details,
    add_to_cart,
    view_cart,
    checkout,
    get_order_status,
    track_shipment,
    initiate_return,
]

# ---------------------------------------------------------------------------
# Agent setup
# ---------------------------------------------------------------------------

agent = Agent(
    model=BedrockModel(model_id=DEFAULT_MODEL_ID),
    tools=TOOLS,
    system_prompt=DEFAULT_SYSTEM_PROMPT,
)


@app.entrypoint
def invoke(payload, context):
    result = agent(payload.get("prompt", "Hello"))
    return {"response": str(result)}


if __name__ == "__main__":
    app.run()
