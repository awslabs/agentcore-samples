#!/usr/bin/env python3

import argparse
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import boto3
from dotenv import load_dotenv

# Load environment variables from sre_agent directory
load_dotenv(Path(__file__).parent.parent / "sre_agent" / ".env")

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)


def _get_user_from_env() -> str:
    """Get user_id from environment variable.

    Returns:
        user_id from USER_ID environment variable or default
    """
    user_id = os.getenv("USER_ID")
    if user_id:
        logger.info(f"Using user_id from environment: {user_id}")
        return user_id
    else:
        # Fallback to default user_id
        default_user_id = "default-sre-user"
        logger.warning(
            f"USER_ID not set in environment, using default: {default_user_id}"
        )
        return default_user_id


def _get_session_from_env(mode: str) -> str:
    """Get session_id from environment variable or generate one.

    Args:
        mode: "interactive" or "prompt" for auto-generation prefix

    Returns:
        session_id from SESSION_ID environment variable or auto-generated
    """
    session_id = os.getenv("SESSION_ID")
    if session_id:
        logger.info(f"Using session_id from environment: {session_id}")
        return session_id
    else:
        # Auto-generate session_id (minimum 33 characters required)
        import uuid

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        unique_id = str(uuid.uuid4()).replace("-", "")[:12]  # 12 character UUID segment
        auto_session_id = f"{mode}-{timestamp}-{unique_id}"
        logger.info(
            f"SESSION_ID not set in environment, auto-generated: {auto_session_id}"
        )
        return auto_session_id


def main():
    parser = argparse.ArgumentParser(
        description="Invoke SRE Agent Runtime via AgentCore"
    )
    parser.add_argument("--prompt", required=True, help="Prompt to send to the agent")
    parser.add_argument(
        "--runtime-arn",
        help="Agent Runtime ARN (reads from .sre_agent_uri if not provided)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS region (default: AWS_REGION env var or us-east-1)",
    )
    parser.add_argument(
        "--session-id", help="Runtime session ID (generates one if not provided)"
    )

    args = parser.parse_args()

    # Get runtime ARN from file if not provided
    runtime_arn = args.runtime_arn
    if not runtime_arn:
        script_dir = Path(__file__).parent

        # First try to read from .agent_arn file (preferred)
        arn_file = script_dir / ".agent_arn"
        if arn_file.exists():
            runtime_arn = arn_file.read_text().strip()
            logging.info(f"Using runtime ARN from .agent_arn: {runtime_arn}")
        else:
            # Fallback to deriving from container URI
            uri_file = script_dir / ".sre_agent_uri"
            if uri_file.exists():
                container_uri = uri_file.read_text().strip()
                # Extract account ID and construct runtime ARN
                # Container URI format: account-id.dkr.ecr.region.amazonaws.com/repo:tag
                account_id = container_uri.split(".")[0]
                runtime_arn = f"arn:aws:bedrock-agentcore:{args.region}:{account_id}:runtime/sre-agent"
                logging.info(
                    f"Using runtime ARN derived from container URI: {runtime_arn}"
                )
            else:
                logging.error(
                    "No runtime ARN provided and neither .agent_arn nor .sre_agent_uri file found"
                )
                logging.error(
                    "Please provide --runtime-arn or ensure the agent is deployed"
                )
                return

    # Generate session ID if not provided
    session_id = args.session_id
    if not session_id:
        timestamp = str(int(time.time()))
        session_id = f"sre-agent-session-{timestamp}-invoke"
        logging.info(f"Generated session ID: {session_id}")

    # Validate session ID length (must be 33+ characters)
    if len(session_id) < 33:
        session_id = session_id + "-" + "x" * (33 - len(session_id))
        logging.info(f"Padded session ID to meet minimum length: {session_id}")

    # Create AgentCore client with custom timeout
    from botocore.config import Config

    # Increase read timeout to handle long-running agent operations
    config = Config(
        read_timeout=300,  # 5 minutes read timeout (default is 60 seconds)
        retries={"max_attempts": 3, "mode": "adaptive"},
    )

    agent_core_client = boto3.client(
        "bedrock-agentcore", region_name=args.region, config=config
    )

    # Get user_id and session_id from environment
    user_id = _get_user_from_env()
    env_session_id = _get_session_from_env("invoke")

    # Use env session_id if not provided via args
    if not args.session_id:
        session_id = env_session_id

    # Prepare payload with user_id and session_id
    payload = json.dumps(
        {"input": {"prompt": args.prompt, "user_id": user_id, "session_id": session_id}}
    )

    logging.info(f"Invoking agent runtime: {runtime_arn}")
    logging.info(f"Session ID: {session_id}")
    logging.info(f"Prompt: {args.prompt}")

    try:
        response = agent_core_client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            payload=payload,
            qualifier="DEFAULT",
        )

        response_body = response["response"].read()
        response_data = json.loads(response_body)

        logging.info("Agent Response:")
        print(json.dumps(response_data, indent=2))

        # Extract and print the message separately
        if "output" in response_data and "message" in response_data["output"]:
            print("\nMessage:")
            print(response_data["output"]["message"])

    except Exception as e:
        logging.error(f"Failed to invoke agent runtime: {e}")
        raise


if __name__ == "__main__":
    main()
