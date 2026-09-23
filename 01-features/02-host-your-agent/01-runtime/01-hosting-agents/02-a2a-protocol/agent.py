"""
A2A Agent — hosted on AgentCore Runtime with Agent-to-Agent protocol.

This agent exposes an A2A-compatible interface with an agent card
for discovery and task-based communication.
"""

import json
import os

import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse
from strands import Agent, tool
from strands.models.bedrock import BedrockModel

app = FastAPI()


# ── Tools ────────────────────────────────────────────────────────────────────


@tool
def search_documentation(query: str) -> str:
    """Search technical documentation for a given query.

    Args:
        query: The search query.

    Returns:
        Search results as a string.
    """
    # In production, connect to a real documentation search API
    return (
        f"Documentation results for '{query}':\n"
        f"1. Getting started guide for {query}\n"
        f"2. API reference for {query}\n"
        f"3. Best practices for {query}\n"
    )


@tool
def summarize_text(text: str) -> str:
    """Summarize a block of text.

    Args:
        text: The text to summarize.

    Returns:
        A concise summary.
    """
    return f"Summary: {text[:200]}..."


# ── Agent Setup ──────────────────────────────────────────────────────────────

model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

agent = Agent(
    model=model,
    tools=[search_documentation, summarize_text],
    system_prompt=(
        "You are a technical documentation assistant. "
        "Use the search_documentation tool to find relevant docs "
        "and summarize_text to provide concise answers."
    ),
)


# ── A2A Agent Card ───────────────────────────────────────────────────────────

AGENT_CARD = {
    "name": "Documentation Assistant",
    "description": "A technical documentation search and summarization agent",
    "url": "https://runtime.bedrock-agentcore.amazonaws.com",
    "version": "1.0.0",
    "capabilities": {
        "streaming": False,
        "pushNotifications": False,
    },
    "skills": [
        {
            "id": "doc-search",
            "name": "Documentation Search",
            "description": "Search and summarize technical documentation",
        }
    ],
}


# ── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/ping")
async def ping():
    return JSONResponse({"status": "ok"})


@app.get("/.well-known/agent.json")
async def agent_card():
    """A2A agent card for discovery."""
    return JSONResponse(AGENT_CARD)


@app.post("/")
async def invocations(request: Request):
    """Handle A2A task requests (JSON-RPC 2.0 at root path)."""
    body = await request.json()

    # Extract the user message from A2A task format
    prompt = ""
    if "params" in body and "message" in body["params"]:
        parts = body["params"]["message"].get("parts", [])
        for part in parts:
            if part.get("type") == "text":
                prompt = part.get("text", "")
                break
    elif "prompt" in body:
        prompt = body["prompt"]
    else:
        prompt = json.dumps(body)

    # Run the agent
    response = agent(prompt)
    result_text = response.message["content"][0]["text"]

    # Return in A2A task result format
    return JSONResponse(
        {
            "result": {
                "status": "completed",
                "parts": [{"type": "text", "text": result_text}],
            }
        }
    )


if __name__ == "__main__":
    host = os.environ.get("AGENT_RUNTIME_HOST", "0.0.0.0")  # nosec B104
    port = int(os.environ.get("AGENT_PORT", 9000))  # A2A uses port 9000
    uvicorn.run(app, host=host, port=port, log_level="info")
