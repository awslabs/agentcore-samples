"""
Lab 02: Strands Diagnostics Agent Lambda Handler

This module provides the Lambda handler function for the diagnostics agent.
It's designed to work with AgentCore Gateway and MCP protocol.

Features:
- Accepts actor_id and session_id for user context propagation
- Integrates with AgentCore Memory via agent state
- Defines diagnostic tools (EC2, NGINX, DynamoDB logs, metrics)
- Handles async agent invocation within Lambda's synchronous context
- Returns structured responses compatible with MCP

Event structure (from Gateway via MCP):
{
    "query": "User's diagnostic query",
    "actor_id": "user-identifier-from-jwt",
    "session_id": "session-id-for-grouping-calls"
}
"""

import asyncio
import os


def lambda_handler(event, context):
    """
    Lambda handler for AgentCore Gateway invoking Strands diagnostics agent.

    Receives query, actor_id, and session_id from Gateway via MCP protocol.
    Creates Strands agent with memory hooks and invokes it asynchronously.
    Returns structured response with agent output and request metadata.

    Args:
        event: Dictionary with keys:
            - query (string): User's diagnostic query
            - actor_id (string): User identifier from JWT token
            - session_id (string): Session ID for grouping related calls
        context: Lambda context object

    Returns:
        Dictionary with response structure:
            {
                "status": "success" | "error",
                "request_id": "session-id or aws_request_id",
                "agent_input": "user's query",
                "response": "agent's response text",
                "type": "strands_agent_response"
            }
    """
    try:
        # Add lib/ to Python path to find pip-installed packages
        import sys

        current_dir = os.path.dirname(os.path.abspath(__file__))
        lib_path = os.path.join(current_dir, "lib")
        if lib_path not in sys.path:
            sys.path.insert(0, lib_path)

        from strands import Agent, tool
        from lab_helpers import mock_data

        # Get model ID from environment variable (set by Lambda configuration)
        MODEL_ID = os.getenv("MODEL_ID", "global.anthropic.claude-sonnet-4-20250514-v1:0")

        # ===================================================================
        # DEFINE DIAGNOSTIC TOOLS
        # ===================================================================

        @tool(description="Fetch EC2 application logs to identify application errors and issues")
        def get_ec2_logs(limit: int = 10) -> dict:
            """Fetch recent EC2 application logs from mock data"""
            logs = mock_data.get_ec2_logs()
            return {
                "logs": logs[:limit],
                "total": len(logs),
                "errors": [log["message"] for log in logs if "error" in log["message"].lower()][:5],
            }

        @tool(description="Fetch NGINX access/error logs to identify HTTP errors and worker issues")
        def get_nginx_logs(limit: int = 10) -> dict:
            """Fetch NGINX access/error logs from mock data"""
            logs = mock_data.get_nginx_logs()
            return {
                "logs": logs[:limit],
                "total": len(logs),
                "http_errors": [log["message"] for log in logs if "5" in log["message"]][:5],
                "worker_issues": [log["message"] for log in logs if "worker" in log["message"].lower()][:5],
            }

        @tool(description="Fetch DynamoDB operation logs to detect throttling and service issues")
        def get_dynamodb_logs(limit: int = 10) -> dict:
            """Fetch DynamoDB operation logs from mock data"""
            logs = mock_data.get_dynamodb_logs()
            return {
                "logs": logs[:limit],
                "total": len(logs),
                "throttling": [log["message"] for log in logs if "throttl" in log["message"].lower()][:5],
                "unavailable": [log["message"] for log in logs if "unavailable" in log["message"].lower()][:5],
            }

        @tool(description="Fetch CloudWatch metrics (CPU, Memory) to analyze resource utilization")
        def get_cloudwatch_metrics(metric_name: str, limit: int = 10) -> dict:
            """Fetch CloudWatch metrics from mock data"""
            metrics = mock_data.get_metrics(metric_name)
            high_values = [
                m for m in metrics if m.get("Maximum", 0) > (80 if metric_name == "MemoryUtilization" else 85)
            ]
            return {
                "metric": metric_name,
                "data_points": len(metrics),
                "high_utilization_periods": len(high_values),
                "peak_value": max([m.get("Maximum", 0) for m in metrics]) if metrics else 0,
            }

        # ===================================================================
        # EXTRACT REQUEST CONTEXT
        # ===================================================================

        # Extract parameters from Gateway event
        agent_input = event.get("query", "Analyze system logs for issues")
        actor_id = event.get("actor_id", "unknown-actor")
        session_id = event.get("session_id", "default-session")

        # Use session_id as request tracking ID (unique per user interaction)
        request_id = session_id

        # Store in agent state for memory hooks to access
        agent_state = {"actor_id": actor_id, "session_id": session_id}

        # ===================================================================
        # CREATE STRANDS AGENT
        # ===================================================================

        diagnostic_agent = Agent(
            name="system_diagnostics_agent",
            model=MODEL_ID,
            tools=[
                get_ec2_logs,
                get_nginx_logs,
                get_dynamodb_logs,
                get_cloudwatch_metrics,
            ],
            system_prompt="""You are an expert system diagnostics agent. Your role is to analyze system logs and metrics to identify issues and their root causes.

When diagnosing system issues:
1. Start by gathering relevant logs (EC2, NGINX, DynamoDB)
2. Check CloudWatch metrics to understand resource utilization patterns
3. Correlate findings across different sources
4. Provide a clear assessment of severity and recommended actions

Always be thorough in your investigation and provide evidence-based conclusions.""",
            state=agent_state,  # Pass actor_id and session_id for memory hooks
        )

        # ===================================================================
        # RUN AGENT ASYNCHRONOUSLY
        # ===================================================================

        # Create async function to run agent
        async def run_agent():
            """Run agent asynchronously and return response"""
            return await diagnostic_agent.invoke_async(agent_input)

        # Execute async function within sync Lambda context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            agent_response = loop.run_until_complete(run_agent())
        finally:
            loop.close()

        # ===================================================================
        # RETURN RESPONSE
        # ===================================================================

        return {
            "status": "success",
            "request_id": request_id,
            "agent_input": agent_input,
            "actor_id": actor_id,
            "session_id": session_id,
            "response": str(agent_response),
            "type": "strands_agent_response",
        }

    except Exception as e:
        import traceback

        return {
            "status": "error",
            "error_message": str(e),
            "traceback": traceback.format_exc(),
            "request_id": context.aws_request_id if context else "unknown",
        }
