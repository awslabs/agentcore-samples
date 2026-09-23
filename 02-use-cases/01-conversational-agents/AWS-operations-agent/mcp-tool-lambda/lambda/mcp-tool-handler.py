"""
AWS Operations Agent Gateway Lambda Handler - Optimized Version
Handles AWS resource inspection tools via Strands Agent integration
Updated: 2025-08-02 - Added optimized system prompt with python_repl and shell tools
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

# Import Strands components at module level
from strands import Agent
from strands.models import BedrockModel
from strands_tools import (
    use_aws,
    current_time,
)

STRANDS_AVAILABLE = True
logging.info("Strands modules imported successfully with shell tool")

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS Service Tool Configurations
SERVICE_QUERIES = {
    # Core AWS Services
    "ec2_read_operations": "List and describe EC2 resources including instances, security groups, VPCs, subnets, and key pairs. Include instance states, types, and network configurations.",
    "s3_read_operations": "List and describe S3 resources including buckets, bucket policies, lifecycle configurations, and access settings. Include bucket regions and creation dates.",
    "lambda_read_operations": "List and describe Lambda resources including functions, layers, aliases, and event source mappings. Include runtime, memory, timeout, and last modified information.",
    "cloudformation_read_operations": "List and describe CloudFormation resources including stacks, stack resources, stack events, and templates. Include stack status and creation times.",
    "iam_read_operations": "List and describe IAM resources including users, roles, policies, and groups. Include policy attachments and permissions (read-only operations only).",
    "rds_read_operations": "List and describe RDS and database resources including DB instances, clusters, snapshots, and parameter groups. Include engine types, versions, and status.",
    "cloudwatch_read_operations": "List and describe CloudWatch resources including metrics, alarms, log groups, and dashboards. Include alarm states and metric statistics.",
    "cost_explorer_read_operations": "Retrieve cost and billing information including cost breakdowns, usage reports, and budget information. Include cost trends and service-wise spending.",
    # Additional AWS Services
    "ecs_read_operations": "List and describe ECS resources including clusters, services, tasks, and task definitions. Include service status and task counts.",
    "eks_read_operations": "List and describe EKS resources including clusters, node groups, and add-ons. Include cluster status, versions, and configurations.",
    "sns_read_operations": "List and describe SNS resources including topics, subscriptions, and platform applications. Include topic ARNs and subscription counts.",
    "sqs_read_operations": "List and describe SQS resources including queues, queue attributes, and message statistics. Include queue types and visibility timeouts.",
    "dynamodb_read_operations": "List and describe DynamoDB resources including tables, indexes, and backups. Include table status, item counts, and throughput settings.",
    "route53_read_operations": "List and describe Route53 resources including hosted zones, record sets, and health checks. Include DNS configurations and routing policies.",
    "apigateway_read_operations": "List and describe API Gateway resources including REST APIs, resources, methods, and deployments. Include API stages and endpoint configurations.",
    "ses_read_operations": "List and describe SES resources including verified identities, configuration sets, and sending statistics. Include reputation metrics and quotas.",
    "bedrock_read_operations": "List and describe Bedrock resources including foundation models, model customization jobs, and inference profiles. Include model capabilities and availability.",
    "sagemaker_read_operations": "List and describe SageMaker resources including endpoints, models, training jobs, and notebook instances. Include status and configurations.",
}

BASIC_TOOLS = ["hello_world", "get_time"]
AWS_SERVICE_TOOLS = list(SERVICE_QUERIES.keys())
ALL_TOOLS = BASIC_TOOLS + AWS_SERVICE_TOOLS


def extract_tool_name(context, event: Dict[str, Any]) -> Optional[str]:
    """Extract tool name from Gateway context or event."""

    # Try Gateway context first
    if hasattr(context, "client_context") and context.client_context:
        if hasattr(context.client_context, "custom") and context.client_context.custom:
            tool_name = context.client_context.custom.get("bedrockAgentCoreToolName")
            if tool_name and "___" in tool_name:
                # Remove namespace prefix (e.g., "aws-tools___hello_world" -> "hello_world")
                return tool_name.split("___", 1)[1]
            elif tool_name:
                return tool_name

    # Fallback to event-based extraction
    for field in ["tool_name", "toolName", "name", "method", "action", "function"]:
        if field in event:
            return event[field]

    # Infer from event structure
    if isinstance(event, dict):
        if "name" in event and len(event) == 1:
            return "hello_world"  # Typical hello_world structure
        elif len(event) == 0:
            return "get_time"  # Empty args typically means get_time

    return None


def handle_hello_world(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle hello_world tool."""
    name = event.get("name", "World")
    message = f"Hello, {name}! This message is from a Lambda function via AWS Operations Agent Gateway."

    return {
        "success": True,
        "result": message,
        "tool": "hello_world",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def handle_get_time(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle get_time tool."""
    current_time = datetime.utcnow().isoformat() + "Z"

    return {
        "success": True,
        "result": f"Current UTC time: {current_time}",
        "tool": "get_time",
        "timestamp": current_time,
    }


def handle_aws_service_tool(tool_name: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle AWS service tools using Strands Agent."""

    # Check if Strands is available
    if not STRANDS_AVAILABLE:
        return {
            "success": False,
            "error": f"Strands modules not available for {tool_name}. Please check Lambda dependencies.",
            "tool": tool_name,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    try:
        # Get the natural language query from the simplified schema
        user_query = event.get("query", "")
        if not user_query:
            return {
                "success": False,
                "error": f"Missing required 'query' parameter for {tool_name}",
                "tool": tool_name,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

        logger.info(f"Initializing Strands Agent for: {tool_name}")

        # Initialize Bedrock model
        bedrock_model = BedrockModel(
            region_name="us-east-1",
            model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
            temperature=0.1,
        )

        # Import loop control tools
        from strands_tools import stop, handoff_to_user

        # Create Strands Agent with loop control tools
        agent = Agent(
            model=bedrock_model,
            tools=[use_aws, stop, handoff_to_user, current_time],
            system_prompt="""
            You are an AWS assistant. IMPORTANT LOOP CONTROL RULES:
            
            1. Keep track of how many AWS operations you've performed
            2. If you've made more than 15 AWS tool calls, use the 'stop' tool immediately
            3. If you encounter repetitive operations, use 'handoff_to_user' to get guidance
            4. If you're stuck in a loop, call 'stop' with an explanation
            5. Always provide a summary before calling 'stop'
            
            Available tools for loop control:
            - stop: Gracefully terminate when done or when hitting limits
            - handoff_to_user: Get human guidance when uncertain
            - use_aws: Your main AWS operations tool

            CRITICAL: For any date-related queries, ALWAYS use the current_time tool first to get the current date before calculating date ranges.
            """,
        )

        # Build query
        # Get the natural language query from the simplified schema
        if not user_query:
            return {
                "success": False,
                "error": f"Missing required 'query' parameter for {tool_name}",
                "tool": tool_name,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

        # Build a simple, direct query (removed complex service context to prevent over-execution)
        # service_name = tool_name.replace('_read_operations', '').upper()
        # final_query = f"AWS {service_name}: {user_query}\n\nExecute this single operation directly and return results."

        # logger.info(f"Executing simplified query for {tool_name}: {user_query}")

        # Execute query
        # response = agent(final_query)
        response = agent(user_query)
        logger.info("##################################")
        print(str(response))
        logger.info("##################################")
        # Extract response text
        response_text = ""
        if hasattr(response, "message") and "content" in response.message:
            for content_block in response.message["content"]:
                if content_block.get("type") == "text" or "text" in content_block:
                    response_text += content_block.get("text", "")
        else:
            response_text = str(response)

        logger.info(f"Response length: {len(response_text)} characters")

        return response_text
        # return {
        #     'success': True,
        #     'result': response_text,
        #     'tool': tool_name,
        #     'service': tool_name.replace('_read_operations', '').replace('_', '-'),
        #     'user_query': user_query,
        #     'timestamp': datetime.utcnow().isoformat() + 'Z'
        # }

    except Exception as e:
        logger.error(f"AWS service tool error: {str(e)}")
        return {
            "success": False,
            "error": f"AWS Service Tool Error: {str(e)}",
            "tool": tool_name,
            "service": tool_name.replace("_read_operations", "").replace("_", "-"),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }


def lambda_handler(event, context):
    """
    AWS Operations Agent Gateway Lambda Handler - Optimized Version

    Handles basic tools (hello_world, get_time) and AWS service tools
    via Strands Agent integration with comprehensive error handling.
    """
    logger.info("AWS Operations Agent Gateway Lambda Handler - START")
    logger.info(f"Event: {json.dumps(event, default=str)}")

    try:
        # Extract tool name
        tool_name = extract_tool_name(context, event)
        logger.info(f"Tool: {tool_name}")

        if not tool_name:
            return {
                "success": False,
                "error": "Unable to determine tool name from context or event",
                "available_tools": ALL_TOOLS,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

        # Route to appropriate handler
        if tool_name == "hello_world":
            return handle_hello_world(event)

        elif tool_name == "get_time":
            return handle_get_time(event)

        elif tool_name in AWS_SERVICE_TOOLS:
            return handle_aws_service_tool(tool_name, event)

        else:
            # Unknown tool
            return {
                "success": False,
                "error": f"Unknown tool: {tool_name}",
                "available_tools": ALL_TOOLS,
                "total_tools": len(ALL_TOOLS),
                "categories": {"basic": BASIC_TOOLS, "aws_services": AWS_SERVICE_TOOLS},
                "tool": tool_name,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

    except Exception as e:
        logger.error(f"Handler error: {str(e)}")
        return {
            "success": False,
            "error": f"Internal error: {str(e)}",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    finally:
        logger.info("AWS Operations Agent Gateway Lambda Handler - END")
