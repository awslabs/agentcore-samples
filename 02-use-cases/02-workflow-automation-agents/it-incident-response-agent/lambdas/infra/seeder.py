"""Custom resource handler: seed DynamoDB tables from S3 on deploy.

Uses CDK Provider framework — return a dict on success, raise on failure.
No cfnresponse needed (the Provider framework handles it).
"""

import json
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_s3 = boto3.client("s3")
_ddb = boto3.resource("dynamodb")
_kb = boto3.client("bedrock-agent")


def _seed_table(table_name: str, items_json: bytes) -> int:
    """Batch-write items into a DynamoDB table."""
    table = _ddb.Table(table_name)
    items = json.loads(items_json)
    if not isinstance(items, list):
        raise ValueError(f"Expected list in seed file for {table_name}, got {type(items).__name__}")
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
    return len(items)


def handler(event, context):
    """CDK Provider onEvent handler."""
    request_type = event.get("RequestType")
    logger.info("Seeder invoked: %s", request_type)

    if request_type == "Delete":
        return {"PhysicalResourceId": "seeder"}

    props = event["ResourceProperties"]
    seed_bucket = props["SeedBucket"]
    users_table = props["UsersTable"]
    processes_table = props["ProcessesTable"]

    # Seed DynamoDB tables
    users_json = _s3.get_object(Bucket=seed_bucket, Key="seed/users.json")["Body"].read()
    processes_json = _s3.get_object(Bucket=seed_bucket, Key="seed/processes.json")["Body"].read()

    n_users = _seed_table(users_table, users_json)
    n_processes = _seed_table(processes_table, processes_json)
    logger.info("Seeded %d users, %d processes", n_users, n_processes)

    # Optional: trigger KB ingestion
    kb_id = props.get("KnowledgeBaseId")
    data_source_id = props.get("DataSourceId")

    if kb_id and data_source_id:
        try:
            job = _kb.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=data_source_id)
            logger.info("Started KB ingestion: %s", job["ingestionJob"]["ingestionJobId"])
        except Exception:
            logger.exception("KB ingestion failed (non-fatal)")

    return {
        "PhysicalResourceId": "seeder",
        "Data": {
            "UsersSeeded": str(n_users),
            "ProcessesSeeded": str(n_processes),
        },
    }
