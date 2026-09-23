"""Shared configuration for the AgentCore Tool Search Plugin sample."""

import os

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
LAMBDA_FUNCTION_NAME = "agentcore-travel-tools"
LAMBDA_ROLE_NAME = "agentcore-travel-tools-role"
GATEWAY_NAME = "agentcore-travel-gateway"
GATEWAY_ROLE_NAME = "agentcore-travel-gateway-role"
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
STATE_FILE = os.path.join(os.path.dirname(__file__), ".deploy_state.json")
LAMBDA_DIR = os.path.join(os.path.dirname(__file__), "lambda")
