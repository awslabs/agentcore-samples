"""Configuration for multi-session evaluation.

Edit the values below to match your AWS environment and preferences.
"""

import os
from typing import Optional


# =============================================================================
# AWS Configuration
# =============================================================================
AWS_REGION = "us-east-1"
AWS_ACCOUNT_ID = "YOUR_AWS_ACCOUNT_ID"


# =============================================================================
# CloudWatch Log Groups
# =============================================================================

# Source log group where OTEL traces are stored for your Agent
SOURCE_LOG_GROUP = "your-source-log-group"

# Evaluation results log group (for score-based discovery and logging results). Setup an Online Evaluator to get this created if you haven't already
# This is the log group name without the /aws/bedrock-agentcore/evaluations/results/ prefix
EVAL_RESULTS_LOG_GROUP = "your-evaluation-log-group"

# Full path to evaluation results log group (auto-constructed from above)
EVAL_RESULTS_LOG_GROUP_FULL = f"/aws/bedrock-agentcore/evaluations/results/{EVAL_RESULTS_LOG_GROUP}"


# =============================================================================
# Evaluation Configuration
# =============================================================================

# Online Evaluation Config ID from AgentCore. Used to correlate offline evaluation
# results with your AgentCore dashboard. Find this in the AgentCore console under
# Online Evaluations, or in CloudWatch log group names (the suffix after the last dash).
# Example: If your log group is "MyAgent-Evaluation-5MB8aF5rLE", the config ID is "MyAgent-Evaluation-5MB8aF5rLE"
# Only needed if you want to log results back to CloudWatch for dashboard visualization.
EVALUATION_CONFIG_ID = "your-evaluation-config-id"

# Evaluator name for score-based discovery. Must match the evaluator name in your
# existing evaluation results (e.g., "Builtin.Correctness" or "Custom.MyEvaluator")
EVALUATOR_NAME = "Builtin.YourEvaluatorName"

# Service name for your Agent as it appears in AgentCore Observability dashboard.
# Check CloudWatch > Log groups > your agent's log group for the service.name attribute.
SERVICE_NAME = "your-service-name"


# =============================================================================
# Time Range Configuration
# =============================================================================

# How far back to look for sessions and traces (in hours)
LOOKBACK_HOURS = 72


# =============================================================================
# Session Discovery Configuration
# =============================================================================

# Maximum number of sessions to discover
MAX_SESSIONS = 100

# Score thresholds for score-based discovery (set to None to disable filtering)
MIN_SCORE: Optional[float] = None
MAX_SCORE: Optional[float] = 0.5


# =============================================================================
# Processing Configuration
# =============================================================================

# Maximum cases per session to evaluate (set to None for all)
MAX_CASES_PER_SESSION: Optional[int] = 10


# =============================================================================
# File Paths
# =============================================================================

# Output file path for session discovery results
DISCOVERED_SESSIONS_PATH = "discovered_sessions.json"

# Output file path for multi-session evaluation results
RESULTS_JSON_PATH = "multi_session_results.json"


# =============================================================================
# Helper Function
# =============================================================================


def setup_cloudwatch_environment() -> None:
    """Configure environment variables for CloudWatch logging."""
    os.environ["AWS_REGION"] = AWS_REGION
    os.environ["AWS_DEFAULT_REGION"] = AWS_REGION
    os.environ["AWS_ACCOUNT_ID"] = AWS_ACCOUNT_ID
    os.environ["EVALUATION_RESULTS_LOG_GROUP"] = EVAL_RESULTS_LOG_GROUP
    os.environ["OTEL_RESOURCE_ATTRIBUTES"] = f"service.name={SERVICE_NAME}"
