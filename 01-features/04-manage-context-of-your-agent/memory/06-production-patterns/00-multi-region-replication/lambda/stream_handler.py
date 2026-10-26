"""Kinesis-triggered Lambda: replicate LTM records to the target region.

This is the production LTM path. An Event Source Mapping invokes this handler
with a batch of Kinesis records published by the SOURCE memory's record stream
(``streamDeliveryResources`` with ``MEMORY_RECORDS`` / ``FULL_CONTENT``). Each
record is re-created in the TARGET region via ``BatchCreateMemoryRecords``, using
the source ``memoryRecordId`` as the ``requestIdentifier`` (idempotent).

STM is replicated separately by the application at write time (dual-write
``CreateEvent`` with ``extractionMode="SKIP"`` on the target) — see
``agentcore_replication.dual_writer``. This handler only handles LTM.

Environment variables
----------------------
TARGET_MEMORY_ID : target (replica) memory resource ID
TARGET_REGION    : target AWS region

A retryable failure raises, so the ESM retries the batch (configure
BisectBatchOnFunctionError + an SQS DLQ on the mapping for poison records).
"""

import json
import logging
import os

import boto3

from agentcore_replication.stream_consumer import (
    StreamStats,
    make_target_client,
    process_kinesis_records,
)

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

TARGET_MEMORY_ID = os.environ["TARGET_MEMORY_ID"]
TARGET_REGION = os.environ["TARGET_REGION"]

# Build the target client once per container (cold start) and reuse it.
_target_client = make_target_client(boto3.Session(), TARGET_REGION)


def lambda_handler(event, context):
    stats = StreamStats()
    process_kinesis_records(
        event.get("Records", []),
        target_client=_target_client,
        target_memory_id=TARGET_MEMORY_ID,
        stats=stats,
    )
    result = stats.as_dict()
    logger.info("LTM stream replication: %s", json.dumps(result))
    # Retryable errors already raised inside process_kinesis_records (so the ESM
    # retries). Terminal per-record failures are reported but don't fail the
    # batch — they'd otherwise block the shard forever. Route them to a DLQ via
    # the ESM's DestinationConfig.OnFailure if you need to capture them.
    return result
