# Run: pip install -qr requirements.txt  # shell command from notebook

import os
import json
import time
import base64
import logging
import boto3
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Configure regions — override with environment variables if needed
PRIMARY_REGION = os.getenv("PRIMARY_REGION", "us-east-1")
SECONDARY_REGION = os.getenv("SECONDARY_REGION", "us-west-2")
ACCOUNT_ID = boto3.client("sts").get_caller_identity()["Account"]

# Used by the cleanup cells to find CloudFormation stacks and S3 buckets
STACK_PREFIX = "agentcore-replication"

print(f"Account: {ACCOUNT_ID}")
print(f"Primary: {PRIMARY_REGION}  Secondary: {SECONDARY_REGION}")

# Run: bash scripts/deploy.sh {PRIMARY_REGION} {SECONDARY_REGION}  # shell command from notebook


def get_memory_id(key):
    """Look up a memory ID from the DynamoDB config table."""
    ddb = boto3.client("dynamodb", region_name=PRIMARY_REGION)
    item = ddb.get_item(TableName="AgentCoreMemoryReplicationConfig", Key={"PK": {"S": key}}).get("Item", {})
    return item.get("memory_id", {}).get("S")


# The deploy script stores memory IDs in the config table
print("Reading memory IDs from config table...")
primary_memory_id = get_memory_id("MEMORY_ID_PRIMARY")
secondary_memory_id = get_memory_id("MEMORY_ID_SECONDARY")

# Verify both memories are ACTIVE
for label, mid, region in [
    ("Primary", primary_memory_id, PRIMARY_REGION),
    ("Secondary", secondary_memory_id, SECONDARY_REGION),
]:
    if mid:
        client = boto3.client("bedrock-agentcore-control", region_name=region)
        status = client.get_memory(memoryId=mid)["memory"]["status"]
        print(f"{label} Memory: {mid} ({status})")
    else:
        print(f"{label} Memory: NOT FOUND")

assert primary_memory_id and secondary_memory_id, "Deployment incomplete — run scripts/deploy.sh first"


# Verify active region tracking
ddb = boto3.client("dynamodb", region_name=PRIMARY_REGION)
item = ddb.get_item(TableName="AgentCoreMemoryReplicationConfig", Key={"PK": {"S": "ACTIVE_REGION"}}).get("Item", {})

print(f"Active region: {item.get('region', {}).get('S', 'NOT SET')}")

# Create a client for the primary region's AgentCore Memory data plane
primary_client = boto3.client("bedrock-agentcore", region_name=PRIMARY_REGION)

# Test records simulating real agent memory — preferences and evaluations
test_records = [
    {"text": "User prefers Python for backend services", "ns": "user/alice"},
    {"text": "User likes event-driven architectures with Lambda", "ns": "user/alice"},
    {"text": "User is evaluating multi-region disaster recovery", "ns": "user/bob"},
]

created_ids = []
for i, rec in enumerate(test_records):
    resp = primary_client.batch_create_memory_records(
        memoryId=primary_memory_id,
        records=[
            {
                "requestIdentifier": f"test-{i}-{int(time.time())}",  # unique ID for idempotency
                "content": {"text": rec["text"]},
                "namespaces": [rec["ns"]],  # e.g. user/alice
                "timestamp": str(int(time.time())),  # epoch seconds as string
            }
        ],
    )
    rid = resp["successfulRecords"][0]["memoryRecordId"]
    created_ids.append(rid)
    print(f"Created: {rid} — {rec['text'][:60]}")

print(f"\n✅ Created {len(created_ids)} records in {PRIMARY_REGION}")

# Create a client for the secondary region's AgentCore Memory data plane
secondary_client = boto3.client("bedrock-agentcore", region_name=SECONDARY_REGION)

# Poll the secondary for records in the 'replicated/' namespace
# The Lambda consumer prefixes namespaces with 'replicated/' when writing to the remote region
# So 'user/alice' in primary becomes 'replicated/user/alice' in secondary
print("Waiting for replication (polling every 10s, up to 120s)...\n")
start = time.time()
replicated = []

while time.time() - start < 120:
    try:
        resp = secondary_client.list_memory_records(
            memoryId=secondary_memory_id,
            namespacePath="replicated/",  # hierarchical match — finds replicated/user/alice, replicated/user/bob, etc.
        )
        replicated = resp.get("memoryRecordSummaries", [])
        if len(replicated) >= len(test_records):
            break
    except Exception:
        pass
    time.sleep(10)

elapsed = time.time() - start
print(f"Found {len(replicated)} replicated record(s) in {elapsed:.0f}s:\n")
for r in replicated:
    text = r.get("content", {}).get("text", "N/A")[:80]
    print(f"  {r['memoryRecordId']} — {text}")

if len(replicated) >= len(test_records):
    print(f"\n✅ All {len(test_records)} records replicated successfully!")
else:
    print(f"\n⚠️  Only {len(replicated)}/{len(test_records)} replicated. Check Lambda logs for errors.")

# Read raw events from the primary's Kinesis stream
# These are the same events the Lambda consumer processes
kinesis = boto3.client("kinesis", region_name=PRIMARY_REGION)
stream_info = kinesis.describe_stream(StreamName="agentcore-memory-stream")
shard_id = stream_info["StreamDescription"]["Shards"][0]["ShardId"]

# Start from the oldest available record (TRIM_HORIZON)
iterator = kinesis.get_shard_iterator(
    StreamName="agentcore-memory-stream",
    ShardId=shard_id,
    ShardIteratorType="TRIM_HORIZON",
)["ShardIterator"]

events = []
for _ in range(5):  # poll up to 5 times
    resp = kinesis.get_records(ShardIterator=iterator, Limit=100)
    for rec in resp["Records"]:
        data = base64.b64decode(rec["Data"]) if isinstance(rec["Data"], str) else rec["Data"]
        events.append(json.loads(data))
    iterator = resp["NextShardIterator"]
    if not resp["Records"]:
        time.sleep(2)

print(f"Read {len(events)} event(s) from Kinesis:\n")
for i, evt in enumerate(events[:10]):
    se = evt.get("memoryStreamEvent", {})
    print(f"  [{se.get('eventType', '?')}] {se.get('memoryRecordId', 'N/A')[:40]}  ns={se.get('namespaces', [])}")

# Enable secondary FIRST — this starts the reverse replication path
# before we cut off the forward path. No replication gap.
# Run: bash scripts/toggle-streaming.sh enable {SECONDARY_REGION}  # shell command from notebook

# Then disable primary — it stops publishing events to Kinesis
# Run: bash scripts/toggle-streaming.sh disable {PRIMARY_REGION}  # shell command from notebook

# Update active region in DynamoDB
ddb.put_item(
    TableName="AgentCoreMemoryReplicationConfig",
    Item={
        "PK": {"S": "ACTIVE_REGION"},
        "region": {"S": SECONDARY_REGION},
        "updated_at": {"S": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "updated_by": {"S": "notebook-failover"},
    },
)
print(f"Active region updated to: {SECONDARY_REGION}")

# Write a record to the secondary (now the active region)
resp = secondary_client.batch_create_memory_records(
    memoryId=secondary_memory_id,
    records=[
        {
            "requestIdentifier": f"failover-test-{int(time.time())}",
            "content": {"text": "Record created during failover in secondary region"},
            "namespaces": ["user/failover-test"],
            "timestamp": str(int(time.time())),
        }
    ],
)
failover_id = resp["successfulRecords"][0]["memoryRecordId"]
print(f"Created in secondary: {failover_id}")

# Poll the primary for the replicated record
# The secondary's Lambda is now replicating to the primary
print("Waiting for replication to primary (polling every 10s, up to 120s)...\n")
start = time.time()
recs = []
while time.time() - start < 120:
    try:
        recs = primary_client.list_memory_records(memoryId=primary_memory_id, namespacePath="replicated/").get(
            "memoryRecordSummaries", []
        )
        if recs:
            break
    except Exception:
        pass
    time.sleep(10)

if recs:
    for r in recs:
        print(f"  {r['memoryRecordId']} — {r.get('content', {}).get('text', '')[:80]}")
    print(f"\n✅ Failover replication working! ({time.time() - start:.0f}s)")
else:
    print("⚠️  No replicated records yet — check Lambda logs in secondary region")

# Failback: restore original configuration
# Same process in reverse — enable primary, disable secondary
# Run: bash scripts/toggle-streaming.sh enable {PRIMARY_REGION}  # shell command from notebook
# Run: bash scripts/toggle-streaming.sh disable {SECONDARY_REGION}  # shell command from notebook

# Update the config table so the application layer knows primary is active again
ddb.put_item(
    TableName="AgentCoreMemoryReplicationConfig",
    Item={
        "PK": {"S": "ACTIVE_REGION"},
        "region": {"S": PRIMARY_REGION},
        "updated_at": {"S": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "updated_by": {"S": "notebook-failback"},
    },
)
print(f"\n✅ Failback complete. Active region: {PRIMARY_REGION}")

# Delete AgentCore Memory instances
for region, mid in [
    (PRIMARY_REGION, primary_memory_id),
    (SECONDARY_REGION, secondary_memory_id),
]:
    try:
        client = boto3.client("bedrock-agentcore-control", region_name=region)
        client.delete_memory(memoryId=mid)
        print(f"Deleting memory {mid} in {region}...")
        # Wait for deletion
        for _ in range(30):
            try:
                status = client.get_memory(memoryId=mid)["memory"]["status"]
                if status == "DELETING":
                    time.sleep(5)
                else:
                    break
            except client.exceptions.ResourceNotFoundException:
                print("  ✅ Deleted")
                break
    except Exception as e:
        print(f"  Error: {e}")

# Delete CloudFormation stacks
for region in [PRIMARY_REGION, SECONDARY_REGION]:
    cf = boto3.client("cloudformation", region_name=region)
    try:
        cf.delete_stack(StackName=f"{STACK_PREFIX}-regional")
        print(f"Deleting regional stack in {region}...")
    except Exception as e:
        print(f"  Error: {e}")

# Global stack (only in primary)
try:
    cf_primary = boto3.client("cloudformation", region_name=PRIMARY_REGION)
    cf_primary.delete_stack(StackName=f"{STACK_PREFIX}-global")
    print("Deleting global stack...")
except Exception as e:
    print(f"  Error: {e}")

# Clean up S3 buckets
for region in [PRIMARY_REGION, SECONDARY_REGION]:
    bucket = f"{STACK_PREFIX}-artifacts-{ACCOUNT_ID}-{region}"
    try:
        s3 = boto3.client("s3", region_name=region)
        objs = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
        for obj in objs:
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
        s3.delete_bucket(Bucket=bucket)
        print(f"Deleted S3 bucket: {bucket}")
    except Exception as e:
        print(f"  S3 cleanup ({bucket}): {e}")

print("\n✅ Cleanup complete")
