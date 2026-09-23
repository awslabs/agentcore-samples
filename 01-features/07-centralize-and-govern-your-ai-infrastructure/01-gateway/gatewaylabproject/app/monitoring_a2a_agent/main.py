"""Monitoring agent on AgentCore Runtime (A2A protocol).

Self-contained A2A agent (a2a-sdk + Starlette + uvicorn) that answers CloudWatch
questions using local boto3-backed tools. Serves on 0.0.0.0:9000 with a /ping
health route, as expected by the AgentCore Runtime A2A service contract.
"""

import logging
import os

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from starlette.responses import JSONResponse

from agent_executor import MonitoringAgentExecutor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HOST, PORT = "0.0.0.0", 9000
RUNTIME_URL = os.environ.get("AGENTCORE_RUNTIME_URL", f"http://127.0.0.1:{PORT}/")

agent_card = AgentCard(
    name="Monitoring Agent",
    description="Monitoring agent that answers questions about CloudWatch logs, metrics, and dashboards.",
    url=RUNTIME_URL,
    version="1.0.0",
    defaultInputModes=["text/plain"],
    defaultOutputModes=["text/plain"],
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[
        AgentSkill(
            id="describe_log_groups",
            name="describe_log_groups",
            description="List CloudWatch log groups, optionally filtered by name prefix.",
            tags=["cloudwatch", "logs"],
        ),
        AgentSkill(
            id="filter_log_events",
            name="filter_log_events",
            description="Search log events across a log group with a filter pattern.",
            tags=["cloudwatch", "logs", "search"],
        ),
        AgentSkill(
            id="get_log_events",
            name="get_log_events",
            description="Read the most recent log events from a specific log stream.",
            tags=["cloudwatch", "logs"],
        ),
        AgentSkill(
            id="get_metric_statistics",
            name="get_metric_statistics",
            description="Retrieve CloudWatch metric statistics over a recent time window.",
            tags=["cloudwatch", "metrics"],
        ),
        AgentSkill(
            id="list_dashboards",
            name="list_dashboards",
            description="List CloudWatch dashboards.",
            tags=["cloudwatch", "dashboards"],
        ),
    ],
)

request_handler = DefaultRequestHandler(
    agent_executor=MonitoringAgentExecutor(),
    task_store=InMemoryTaskStore(),
)


async def ping(request):
    """Health check for the AgentCore Runtime."""
    return JSONResponse({"status": "healthy"})


server = A2AStarletteApplication(agent_card=agent_card, http_handler=request_handler)
# A2AStarletteApplication.build() returns a constructed Starlette app, which has
# no .route decorator. Register the /ping health route up front instead.
app = server.build()
app.add_route("/ping", ping, methods=["GET"])


if __name__ == "__main__":
    logger.info("Starting A2A monitoring agent on %s:%s", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT)
