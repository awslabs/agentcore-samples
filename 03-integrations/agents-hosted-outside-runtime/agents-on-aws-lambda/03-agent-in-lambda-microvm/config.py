"""
Shared configuration for the MicroVM + AgentCore Observability demo.

Every resource this demo creates carries the same PREFIX so cleanup can find
and delete them without touching anything else in the account.
"""
from __future__ import annotations

import os

import boto3

# ---- knobs (override via env if needed) -----------------------------------

REGION = os.environ.get("AWS_REGION", "us-east-1")
PREFIX = os.environ.get("MICROVM_DEMO_PREFIX", "microvm-agentcore-obs")

# The Bedrock model the agent uses. Cross-region inference profile for Haiku.
MODEL_ID = os.environ.get(
    "AGENT_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)

# MicroVM sizing. The default baseline (2 GB / 1 vCPU) is more than enough
# for a single Strands agent, but we set it explicitly for clarity.
BASELINE_MEMORY_MIB = int(os.environ.get("MICROVM_MEMORY_MIB", "2048"))

# How long a launched MicroVM is allowed to run (seconds). 1800 = 30 minutes,
# which is plenty for the demo but keeps the bill floor small if we forget
# to terminate.
MICROVM_MAX_DURATION_SECS = int(os.environ.get("MICROVM_MAX_DURATION_SECS", "1800"))

# ---- derived names --------------------------------------------------------

IMAGE_NAME = f"{PREFIX}-image"
BUILD_ROLE_NAME = f"{PREFIX}-build-role"
EXEC_ROLE_NAME = f"{PREFIX}-exec-role"

# AgentCore convention for the agent's log group. The <agent-id> segment is
# what the CloudWatch GenAI Observability dashboard uses to group traces and
# spans for a single agent.
AGENT_ID = PREFIX  # keep it simple — the prefix IS the agent id
AGENT_LOG_GROUP = f"/aws/bedrock-agentcore/runtimes/{AGENT_ID}"
AGENT_LOG_STREAM_RUNTIME = "runtime-logs"
AGENT_LOG_STREAM_SPANS = "spans"

# Where the CloudWatch Agent build itself will log (image build output).
BUILD_LOG_GROUP = f"/aws/lambda-microvms/{IMAGE_NAME}"

# Managed base image (populated from list_managed_microvm_images at deploy time).
DEFAULT_BASE_IMAGE_ARN = f"arn:aws:lambda:{REGION}:aws:microvm-image:al2023-1"

# S3 code artifact
CODE_ARTIFACT_KEY = f"{PREFIX}/app.zip"


def account_id() -> str:
    return boto3.client("sts").get_caller_identity()["Account"]


def bucket_name() -> str:
    # S3 bucket names must be globally unique and DNS-safe.
    return f"{PREFIX}-{account_id()}-{REGION}"


def build_role_arn() -> str:
    return f"arn:aws:iam::{account_id()}:role/{BUILD_ROLE_NAME}"


def exec_role_arn() -> str:
    return f"arn:aws:iam::{account_id()}:role/{EXEC_ROLE_NAME}"


def image_arn() -> str:
    return f"arn:aws:lambda:{REGION}:{account_id()}:microvm-image:{IMAGE_NAME}"


def agent_log_group_arn() -> str:
    return f"arn:aws:logs:{REGION}:{account_id()}:log-group:{AGENT_LOG_GROUP}:*"


# ---- OTEL env vars baked into the MicroVM image ---------------------------
#
# These are the exact variables the AgentCore Observability docs prescribe for
# an agent hosted OUTSIDE the AgentCore runtime. The ADOT SDK (activated by
# OTEL_PYTHON_DISTRO=aws_distro + OTEL_PYTHON_CONFIGURATOR=aws_configurator)
# uses them to route OTLP over http/protobuf to the CloudWatch OTLP endpoint,
# SigV4-signed with the execution role's credentials.
#
# aws.log.group.names + x-aws-log-group direct spans and structured logs into
# the agent's own log group (unified span destination). This requires
# aws-opentelemetry-distro >= 0.18.0 and a Logs resource policy that allows
# X-Ray to PutLogEvents on that log group (deploy.py installs the policy).

def otel_env() -> dict[str, str]:
    log_group = AGENT_LOG_GROUP
    service_name = f"{PREFIX}-agent"
    return {
        # AWS_REGION is Lambda-injected automatically; setting it here is
        # rejected as a reserved env var.
        # Agent identity for our own log lines / span attributes
        "AGENT_MODEL_ID": MODEL_ID,
        "OTEL_SERVICE_NAME": service_name,
        # ADOT activation
        "AGENT_OBSERVABILITY_ENABLED": "true",
        "OTEL_PYTHON_DISTRO": "aws_distro",
        "OTEL_PYTHON_CONFIGURATOR": "aws_configurator",
        # Point resource attributes + span/log routing at the agent's log group
        "OTEL_RESOURCE_ATTRIBUTES": (
            f"service.name={service_name},"
            f"aws.log.group.names={log_group},"
            f"cloud.platform=aws_lambda_microvm"
        ),
        "OTEL_EXPORTER_OTLP_LOGS_HEADERS": (
            f"x-aws-log-group={log_group},"
            f"x-aws-log-stream={AGENT_LOG_STREAM_RUNTIME},"
            f"x-aws-metric-namespace=bedrock-agentcore"
        ),
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS": (
            f"x-aws-log-group={log_group},"
            f"x-aws-log-stream={AGENT_LOG_STREAM_SPANS}"
        ),
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_TRACES_EXPORTER": "otlp",
    }
