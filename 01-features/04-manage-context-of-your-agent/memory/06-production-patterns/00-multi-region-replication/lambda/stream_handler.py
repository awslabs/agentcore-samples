"""Kinesis-triggered Lambda: replicate LTM records to the target region.

This is the production LTM path. An Event Source Mapping invokes this handler
with a batch of Kinesis records published by the SOURCE memory's record stream
(``streamDeliveryResources`` with ``MEMORY_RECORDS`` / ``FULL_CONTENT``). Each
create, update, and delete is applied to the TARGET region. A DynamoDB record map
tracks which target record each source record became, so updates and deletes
reach the right record and redelivered events are skipped.

STM is replicated separately by the application at write time (dual-write
``CreateEvent`` with ``extractionMode="SKIP"`` on the target) — see
``agentcore_replication.dual_writer``. This handler only handles LTM.

Environment variables
----------------------
TARGET_MEMORY_ID : target (replica) memory resource ID
TARGET_REGION    : target AWS region
RECORD_MAP_TABLE : DynamoDB table for the source-to-target record ID map

A retryable failure raises, so the ESM retries the batch (configure
BisectBatchOnFunctionError + an SQS DLQ on the mapping for poison records).
"""

import json
import logging
import os

import boto3
from agentcore_replication.record_map import DynamoDBRecordMap
from agentcore_replication.stream_consumer import (
    StreamStats,
    make_target_client,
    process_kinesis_records,
)

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

TARGET_MEMORY_ID = os.environ["TARGET_MEMORY_ID"]
TARGET_REGION = os.environ["TARGET_REGION"]
RECORD_MAP_TABLE = os.environ["RECORD_MAP_TABLE"]

# Build the clients once per container (cold start) and reuse them.
_target_client = make_target_client(boto3.Session(), TARGET_REGION)
_record_map = DynamoDBRecordMap(boto3.resource("dynamodb").Table(RECORD_MAP_TABLE))


def lambda_handler(event, context):
    stats = StreamStats()
    process_kinesis_records(
        event.get("Records", []),
        target_client=_target_client,
        target_memory_id=TARGET_MEMORY_ID,
        record_map=_record_map,
        stats=stats,
    )
    result = stats.as_dict()
    logger.info("LTM stream replication: %s", json.dumps(result))
    # Retryable errors already raised inside process_kinesis_records (so the ESM
    # retries). Terminal per-record failures are reported but don't fail the
    # batch — they'd otherwise block the shard forever. Route them to a DLQ via
    # the ESM's DestinationConfig.OnFailure if you need to capture them.
    return result
