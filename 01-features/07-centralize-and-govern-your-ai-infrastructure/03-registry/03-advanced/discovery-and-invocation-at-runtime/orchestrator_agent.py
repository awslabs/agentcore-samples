import os

import boto3
import uvicorn
from fastapi import FastAPI
from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
from utils import (
    create_a2a_tool_from_metadata,
    create_mcp_client_from_metadata,
    fetch_oauth_token,
    parse_server_metadata,
)

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
REGISTRY_ARN = os.environ["REGISTRY_ARN"]
COGNITO_DOMAIN = os.environ["COGNITO_DOMAIN"]
CLIENT_ID = os.environ["CLIENT_ID"]
SCOPES = os.environ["SCOPES"]
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")

session = boto3.Session()

_sm = session.client("secretsmanager", region_name=REGION)
CLIENT_SECRET = _sm.get_secret_value(SecretId=os.environ["CLIENT_SECRET_NAME"])["SecretString"]

dp_client = session.client("agent-registry", region_name=REGION)


@tool
def discover_and_execute(request: str) -> str:
    """Search the AWS Agent Registry for relevant tools and agents, then execute the request.

    Args:
        request: The user request to process.

    Returns:
        The response from executing the request with dynamically discovered tools.
    """
    access_token = fetch_oauth_token(COGNITO_DOMAIN, CLIENT_ID, CLIENT_SECRET, SCOPES, REGION)

    search_queries = [
        request,
        "order management status tracking cancel update",
        "pricing discount promo code savings",
        "customer support returns refunds complaints",
    ]
    all_records = {}
    for q in search_queries:
        results = dp_client.search_discoverable_registry_records(
            registryIds=[REGISTRY_ARN],
            searchQuery=q,
            maxResults=5,
        ).get("registryRecords", [])
        for rec in results:
            name = rec.get("name", "")
            if name not in all_records:
                all_records[name] = rec

    records = list(all_records.values())
    if not records:
        return "No tools found in registry for this request."

    mcp_clients, a2a_tools, seen_urls = [], [], set()
    for rec in records:
        meta = parse_server_metadata(rec)
        if meta["protocol"] == "MCP":
            url = meta.get("url")
            if url and url not in seen_urls:
                c = create_mcp_client_from_metadata(meta, access_token)
                if c:
                    mcp_clients.append(c)
                    seen_urls.add(url)
        elif meta["protocol"] == "A2A":
            fn = create_a2a_tool_from_metadata(meta, session, REGION)
            if fn:
                a2a_tools.append(fn)

    if not mcp_clients and not a2a_tools:
        return "No tools could be instantiated from registry results."

    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    started_clients = []
    try:
        mcp_tools = []
        for c in mcp_clients:
            c.start()
            started_clients.append(c)
            mcp_tools.extend(c.list_tools_sync())

        sub_agent = Agent(
            model=model,
            tools=mcp_tools + a2a_tools,
            system_prompt=(
                "You are an Order Management & Customer Service assistant. "
                "Use the RIGHT tool for each request:\n"
                "- Order lookups, status, cancellations, address changes: use MCP tools\n"
                "- Pricing, discounts, promo codes: use the pricing_agent tool\n"
                "- Returns, refunds, complaints, escalations: use the customer_support_agent tool\n"
                "Always use the most specific tool for the task."
            ),
        )
        return str(sub_agent(request))
    finally:
        for c in started_clients:
            try:
                c.stop()
            except Exception:  # noqa: BLE001, S110
                pass


model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
agent = Agent(
    model=model,
    name="Orchestrator Agent",
    description="Order management orchestrator that discovers and invokes tools and agents from the AWS Agent Registry",
    system_prompt=(
        "You are an orchestrator agent. For every user request, use the "
        "discover_and_execute tool to search the registry and process it. "
        "Always pass the full user request to the tool."
    ),
    tools=[discover_and_execute],
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
