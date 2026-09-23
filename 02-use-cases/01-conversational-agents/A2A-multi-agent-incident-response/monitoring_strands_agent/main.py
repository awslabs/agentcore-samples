from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from agent_executor import MonitoringAgentExecutor
from starlette.responses import JSONResponse
import boto3
import logging
import os
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ssm = boto3.client("ssm")
agentcore_client = boto3.client("bedrock-agentcore")

# Configuration with validation
MODEL_ID = os.getenv("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")

MEMORY_ID = os.getenv("MEMORY_ID")
if not MEMORY_ID:
    raise RuntimeError("Missing MEMORY_ID environment variable")

GATEWAY_PROVIDER_NAME = os.getenv("GATEWAY_PROVIDER_NAME")
if not GATEWAY_PROVIDER_NAME:
    raise RuntimeError("Missing GATEWAY_PROVIDER_NAME environment variable")

AWS_REGION = os.getenv("MCP_REGION")
if not AWS_REGION:
    raise RuntimeError("Missing MCP_REGION environment variable")

# Use the complete runtime URL from environment variable, fallback to local
runtime_url = os.environ.get("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/")
host, port = "0.0.0.0", 9000

agent_card = AgentCard(
    name="Monitoring Agent",
    description="Monitoring agent that handles CloudWatch logs, metrics, dashboards, and AWS service monitoring",
    url=runtime_url,
    version="0.3.0",
    defaultInputModes=["text/plain"],
    defaultOutputModes=["text/plain"],
    capabilities=AgentCapabilities(streaming=True, pushNotifications=False),
    skills=[
        AgentSkill(
            id="x_amz_bedrock_agentcore_search",
            name="x_amz_bedrock_agentcore_search",
            description="A special tool that returns a trimmed down list of tools given a context. Use this tool only when there are many tools available and you want to get a subset that matches the provided context.",
            tags=[],
        ),
        AgentSkill(
            id="monitoragenta2aTarget___DescribeLogGroups",
            name="monitoragenta2aTarget___DescribeLogGroups",
            description="Lists the specified log groups. You can list all your log groups or filter the results by prefix. The results are ASCII-sorted by log group name. CloudWatch Logs doesn't support IAM policies that control access to the DescribeLogGroups action by using the aws:ResourceTag/key-name condition key.",
            tags=["cloudwatch", "logs", "monitoring"],
        ),
        AgentSkill(
            id="monitoragenta2aTarget___DescribeLogStreams",
            name="monitoragenta2aTarget___DescribeLogStreams",
            description="Lists the log streams for the specified log group.",
            tags=["cloudwatch", "logs", "monitoring"],
        ),
        AgentSkill(
            id="monitoragenta2aTarget___FilterLogEvents",
            name="monitoragenta2aTarget___FilterLogEvents",
            description="Lists log events from the specified log group. You can filter the results using a filter pattern.",
            tags=["cloudwatch", "logs", "monitoring", "search"],
        ),
        AgentSkill(
            id="monitoragenta2aTarget___GetLogEvents",
            name="monitoragenta2aTarget___GetLogEvents",
            description="Lists log events from the specified log stream.",
            tags=["cloudwatch", "logs", "monitoring"],
        ),
    ],
)

# Create request handler with executor
request_handler = DefaultRequestHandler(agent_executor=MonitoringAgentExecutor(), task_store=InMemoryTaskStore())

# Create A2A server
server = A2AStarletteApplication(agent_card=agent_card, http_handler=request_handler)

# Build the app and add health endpoint
app = server.build()


@app.route("/ping", methods=["GET"])
async def ping(request):
    """Ping endpoint"""
    return JSONResponse({"status": "healthy"})


logger.info("✅ A2A Server configured")
logger.info(f"📍 Server URL: {runtime_url}")
logger.info(f"🏥 Health check: {runtime_url}/health")
logger.info(f"🏓 Ping: {runtime_url}/ping")

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port)
