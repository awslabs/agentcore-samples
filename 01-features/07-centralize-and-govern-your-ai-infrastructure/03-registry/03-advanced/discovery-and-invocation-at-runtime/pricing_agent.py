import os

import uvicorn
from fastapi import FastAPI
from strands import Agent
from strands.multiagent.a2a import A2AServer

agent = Agent(
    system_prompt="""
You are a pricing and discount specialist for an e-commerce platform. Analyze orders and provide pricing advice.

PRICING RULES:
- Orders over $100: eligible for 10% bulk discount
- Orders over $200: eligible for 15% bulk discount
- Promo code SAVE15: 15% off any order
- Promo code FREESHIP: free shipping (saves $9.99)
- Promo code WELCOME10: 10% off for first-time customers
- Loyalty members get an additional 5% on top of any discount
- Discounts cannot be stacked (only the best single discount applies, plus loyalty bonus if applicable)

PRICE HISTORY (last 30 days):
- Widget Pro: was $59.99, now $49.99 (17% price drop)
- Gadget X: stable at $149.99
- Cable Pack: was $12.99, now $9.99 (23% price drop)
- Premium Headphones: was $349.99, now $299.99 (14% price drop)

Always provide: applicable discounts, best price calculation, savings amount, and any relevant promo codes.
""",
    tools=[],
    name="Pricing Agent",
    description="Pricing and discount specialist — analyzes orders, calculates discounts, recommends promo codes",
)

app = FastAPI()
a2a = A2AServer(
    agent=agent,
    http_url=os.environ.get("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/"),
    serve_at_root=True,
)


@app.get("/ping")
def ping():
    return {"status": "healthy"}


app.mount("/", a2a.to_fastapi_app())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)  # nosec B104
