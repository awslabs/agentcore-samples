"""
Recreate the AgentCore Memory with the custom claims-focused episodic strategy.

Repeatable replacement for the throwaway recreate scripts. It:
  1. (optionally) deletes the current memory in config.json
  2. creates a fresh memory via memory.strategy.create_claims_memory
  3. updates config.json + the SSM parameter (single source of truth)
  4. enables CloudWatch log delivery for extraction/consolidation logs

Does NOT touch the IAM execution role (managed by setup/0_setup_infra.sh) — it
reads the role ARN from config.json, falling back to the conventional name.

Usage:
    python -m memory.recreate                 # delete old + create new
    python -m memory.recreate --keep-old      # create new without deleting old
    python -m memory.recreate --clear-sessions  # also clear DynamoDB session rows
"""

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import boto3
from bedrock_agentcore.memory import MemoryClient
from botocore.exceptions import ClientError
from memory.config import MEMORY_ID_SSM_PARAM, load_config
from memory.strategy import EXTRACTION_MODEL_ID, create_claims_memory

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("recreate")

CONFIG_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "setup", "config.json"))


def _exec_role_arn(cfg: dict, account: str) -> str:
    if cfg.get("memory_execution_role_arn"):
        return cfg["memory_execution_role_arn"]
    stack = cfg.get("stack_name", "insurance-claims-demo")
    return f"arn:aws:iam::{account}:role/{stack}-memory-execution-role"


def _enable_log_delivery(region: str, account: str, memory_id: str):
    logs = boto3.client("logs", region_name=region)
    memory_arn = f"arn:aws:bedrock-agentcore:{region}:{account}:memory/{memory_id}"
    log_group = f"/aws/vendedlogs/bedrock-agentcore/memory/APPLICATION_LOGS/{memory_id}"
    source_name = f"{memory_id}-app-logs"[:60]
    dest_name = f"{memory_id}-cwl-dest"[:60]
    try:
        logs.create_log_group(logGroupName=log_group)
    except ClientError as e:
        if "ResourceAlreadyExistsException" not in str(e):
            logger.warning("log group: %s", e)
    try:
        logs.put_delivery_source(name=source_name, resourceArn=memory_arn, logType="APPLICATION_LOGS")
        dest = logs.put_delivery_destination(
            name=dest_name,
            deliveryDestinationConfiguration={
                "destinationResourceArn": f"arn:aws:logs:{region}:{account}:log-group:{log_group}:*"
            },
        )
        logs.create_delivery(deliverySourceName=source_name, deliveryDestinationArn=dest["deliveryDestination"]["arn"])
        logger.info("Log delivery enabled -> %s", log_group)
    except ClientError as e:
        if "ConflictException" in str(e) or "already exists" in str(e):
            logger.info("Log delivery already configured.")
        else:
            logger.warning("log delivery: %s", e)


def main():
    parser = argparse.ArgumentParser(description="Recreate the claims memory (custom strategy)")
    parser.add_argument("--keep-old", action="store_true", help="Do not delete the existing memory")
    parser.add_argument("--clear-sessions", action="store_true", help="Also clear DynamoDB session rows")
    parser.add_argument("--model", default=EXTRACTION_MODEL_ID, help="Override extraction model id")
    args = parser.parse_args()

    cfg = load_config()
    region = cfg["region"]
    name = cfg.get("memory_name", "insurance_claims_demo_episodic_memory")
    old_mid = cfg.get("memory_id")

    account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    role_arn = _exec_role_arn(cfg, account)
    client = MemoryClient(region_name=region)

    if old_mid and not args.keep_old:
        logger.info("Deleting old memory %s ...", old_mid)
        try:
            client.delete_memory_and_wait(memory_id=old_mid, max_wait=300, poll_interval=10)
            logger.info("  deleted")
        except Exception as e:  # noqa: BLE001 - best-effort delete of old memory during recreate
            logger.info("  (already gone / note: %s)", str(e)[:80])

    logger.info("Creating memory %s with model %s ...", name, args.model)
    mid = create_claims_memory(client, name=name, memory_execution_role_arn=role_arn, model_id=args.model)
    logger.info("  created: %s", mid)

    # Update config.json
    cfg["memory_id"] = mid
    cfg["extraction_model_id"] = args.model
    cfg["memory_execution_role_arn"] = role_arn
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    logger.info("  config.json updated")

    # Update SSM (single source of truth)
    boto3.client("ssm", region_name=region).put_parameter(
        Name=MEMORY_ID_SSM_PARAM, Value=mid, Type="String", Overwrite=True
    )
    logger.info("  SSM %s updated", MEMORY_ID_SSM_PARAM)

    _enable_log_delivery(region, account, mid)

    if args.clear_sessions:
        table_name = cfg.get("session_backend", {}).get("table_name")
        if table_name:
            table = boto3.resource("dynamodb", region_name=region).Table(table_name)
            rows = table.scan().get("Items", [])
            for r in rows:
                table.delete_item(Key={"user_id": r["user_id"], "session_id": r["session_id"]})
            logger.info("  cleared %d DynamoDB session row(s)", len(rows))

    print(f"\nNEW_MEMORY_ID: {mid}")
    print("Restart the Flask server so it picks up the new memory id from SSM.")


if __name__ == "__main__":
    main()
