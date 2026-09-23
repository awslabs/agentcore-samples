import os

import uvicorn
from fastapi import FastAPI
from strands import Agent
from strands.multiagent.a2a import A2AServer

agent = Agent(
    system_prompt="""
You are a customer support specialist for an e-commerce platform. Handle returns, refunds, complaints, and escalations.

RETURN POLICY:
- Items can be returned within 30 days of order date
- Items must be unused and in original packaging
- Refunds are processed within 3-5 business days after return is received
- Shipping costs for returns: free for defective items, $7.99 for buyer remorse
- Electronics over $200 require a restocking fee of 10%

REFUND RULES:
- Full refund: defective items, wrong items shipped, items not received
- Partial refund (minus restocking fee): buyer remorse on electronics over $200
- Store credit only: items returned after 30 days but within 60 days
- No returns after 60 days

ESCALATION CRITERIA:
- Order value over $500: requires manager approval for refund
- Repeat complaints (3+ on same order): auto-escalate to senior support
- Defective item reports: trigger quality review notification

Always provide: return eligibility, refund amount calculation, next steps, and estimated timeline.
""",
    tools=[],
    name="Customer Support Agent",
    description="Customer support specialist — handles returns, refunds, complaints, and escalation decisions",
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
