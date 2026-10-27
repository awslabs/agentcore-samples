"""AgentCore A2A entrypoint for the analysis agent.

Deploys the analysis agent as an A2A server on AgentCore Runtime.
AgentCore routes A2A protocol traffic to port 9000.

Usage (local):
    uvicorn deployment.agentcore_analysis:app --host 0.0.0.0 --port 9000
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from strands.multiagent.a2a import A2AServer

from agents import create_analysis_agent

runtime_url = os.environ.get("AGENTCORE_RUNTIME_URL", "http://0.0.0.0:9000/")

agent = create_analysis_agent()
a2a_server = A2AServer(agent, http_url=runtime_url, serve_at_root=True)

app = FastAPI(title="Analysis Agent (A2A)")


@app.get("/ping")
def ping() -> dict:
    """Health check endpoint required by AgentCore."""
    return {"status": "healthy"}


app.mount("/", a2a_server.to_fastapi_app())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)  # noqa: S104
