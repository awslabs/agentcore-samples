"""
Setup script — creates the CloudWatch log group and log stream for LlamaIndex observability.

Run once before your first agent invocation:
    python setup.py
    python setup.py --region us-west-2

The log group name created here must match the value in:
    OTEL_EXPORTER_OTLP_LOGS_HEADERS=x-aws-log-group=<name>,...
"""

import argparse
import logging

import boto3
from botocore.exceptions import ClientError

LOG_GROUP = "agents/llama-index-agent-logs"
LOG_STREAM = "default"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def setup(region: str):
    client = boto3.client("logs", region_name=region)

    try:
        client.create_log_group(logGroupName=LOG_GROUP)
        logger.info("Created log group: %s", LOG_GROUP)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceAlreadyExistsException":
            logger.info("Log group already exists: %s", LOG_GROUP)
        else:
            raise

    try:
        client.create_log_stream(logGroupName=LOG_GROUP, logStreamName=LOG_STREAM)
        logger.info("Created log stream: %s/%s", LOG_GROUP, LOG_STREAM)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceAlreadyExistsException":
            logger.info("Log stream already exists: %s/%s", LOG_GROUP, LOG_STREAM)
        else:
            raise

    logger.info("\nSetup complete. Update .env.example → .env with:")
    logger.info(
        "  OTEL_EXPORTER_OTLP_LOGS_HEADERS=x-aws-log-group=%s,x-aws-log-stream=%s,...",
        LOG_GROUP,
        LOG_STREAM,
    )


def main():
    parser = argparse.ArgumentParser(description="Create CloudWatch log group/stream for LlamaIndex observability")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    args = parser.parse_args()
    setup(args.region)


if __name__ == "__main__":
    main()
