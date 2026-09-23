"""Memory record streaming to Amazon Kinesis Data Streams.

What you learn:
    - Create a Kinesis stream + IAM role AgentCore can assume
    - Configure `streamDeliveryResources` on CreateMemory
    - Trigger MemoryRecordCreated events via BatchCreateMemoryRecords
    - Read events from the stream and inspect their schema

Two surfaces:
    python record-streaming.py boto3
    python record-streaming.py sdk

Add `--cleanup` to delete the memory resource at the end. By default the
memory is kept so you can inspect it; the script prints the memoryId.

Prerequisites:
    pip install boto3 bedrock-agentcore
    export AWS_REGION=us-west-2   # use any AgentCore-supported region
"""

import base64
import json
import os
import sys
import time
import uuid

REGION = os.getenv("AWS_REGION", "us-west-2")
ACTOR_ID = "demo-user"


def _trust_policy() -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    )


def _permissions_policy(stream_arn: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["kinesis:PutRecords", "kinesis:DescribeStream"],
                    "Resource": stream_arn,
                }
            ],
        }
    )


def _read_kinesis_events(kinesis, stream_name, max_wait_seconds=60, max_events=10):
    info = kinesis.describe_stream(StreamName=stream_name)
    shard_id = info["StreamDescription"]["Shards"][0]["ShardId"]
    iterator = kinesis.get_shard_iterator(StreamName=stream_name, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")[
        "ShardIterator"
    ]

    events = []
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline and len(events) < max_events:
        resp = kinesis.get_records(ShardIterator=iterator, Limit=100)
        for record in resp["Records"]:
            data = record["Data"]
            if isinstance(data, str):
                data = base64.b64decode(data)
            events.append(json.loads(data))
        iterator = resp["NextShardIterator"]
        if not resp["Records"]:
            time.sleep(2)
    return events


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    unique = str(uuid.uuid4())[:8]
    kinesis = boto3.client("kinesis", region_name=REGION)
    iam = boto3.client("iam")
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    data = boto3.client("bedrock-agentcore", region_name=REGION)

    # 1. Kinesis stream
    stream_name = f"memory-record-stream-{unique}"
    kinesis.create_stream(StreamName=stream_name, ShardCount=1)
    kinesis.get_waiter("stream_exists").wait(StreamName=stream_name)
    stream_arn = kinesis.describe_stream(StreamName=stream_name)["StreamDescription"]["StreamARN"]
    print(f"[boto3] Stream {stream_arn}")

    # 2. IAM role AgentCore can assume to publish to the stream
    role_name = f"AgentCoreMemoryStreamingRole-{unique}"
    role_arn = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=_trust_policy(),
        Description="Allows AgentCore Memory to publish events to Kinesis",
    )["Role"]["Arn"]
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="KinesisPublishPolicy",
        PolicyDocument=_permissions_policy(stream_arn),
    )
    print(f"[boto3] Role {role_arn}; sleeping 10s for IAM propagation")
    time.sleep(10)

    # 3. Memory with stream delivery wired in
    memory_id = control.create_memory(
        name=f"streaming_memory_{unique}",
        description="Memory with record streaming enabled",
        eventExpiryDuration=7,
        memoryExecutionRoleArn=role_arn,
        # streamDeliveryResources is a STRUCTURE, not a bare list:
        # {"resources":[{"kinesis":{"dataStreamArn":..., "contentConfigurations":[{"type":..,"level":..}]}}]}
        # (verified against the CreateMemory API model; matches the cross-region CFN).
        streamDeliveryResources={
            "resources": [
                {
                    "kinesis": {
                        "dataStreamArn": stream_arn,
                        "contentConfigurations": [
                            {"type": "MEMORY_RECORDS", "level": "FULL_CONTENT"},
                        ],
                    }
                }
            ]
        },
        memoryStrategies=[
            {
                "userPreferenceMemoryStrategy": {
                    "name": "UserPreferences",
                    "namespaces": [f"/{ACTOR_ID}/user_preferences/"],
                }
            }
        ],
    )["memory"]["id"]
    print(f"[boto3] Memory {memory_id}")
    deadline = time.time() + 300
    while time.time() < deadline:
        if control.get_memory(memoryId=memory_id)["memory"]["status"] == "ACTIVE":
            break
        time.sleep(5)

    # 4. Trigger MemoryRecordCreated events directly (no extraction wait).
    data.batch_create_memory_records(
        memoryId=memory_id,
        records=[
            {
                "requestIdentifier": "rec-1",
                "namespaces": [f"/{ACTOR_ID}/user_preferences/"],
                "timestamp": str(int(time.time())),
                "content": {"text": "User prefers window seats on flights."},
            },
            {
                "requestIdentifier": "rec-2",
                "namespaces": [f"/{ACTOR_ID}/user_preferences/"],
                "timestamp": str(int(time.time())),
                "content": {"text": "User's favourite language is Python."},
            },
        ],
    )
    print("[boto3] Wrote 2 records — polling Kinesis for events...")

    events = _read_kinesis_events(kinesis, stream_name, max_wait_seconds=60, max_events=10)
    print(f"[boto3] Received {len(events)} stream event(s):")
    for e in events:
        evt = e.get("memoryStreamEvent", {})
        print(f"  - {evt.get('eventType')} @ {evt.get('eventTime')} | record={evt.get('memoryRecordId', 'N/A')}")

    # 5. Cleanup
    if cleanup:
        control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
        kinesis.delete_stream(StreamName=stream_name, EnforceConsumerDeletion=True)
        iam.delete_role_policy(RoleName=role_name, PolicyName="KinesisPublishPolicy")
        iam.delete_role(RoleName=role_name)
        print("[boto3] Cleaned up memory, stream, role")
    else:
        print(
            f"[boto3] Keeping resources (pass --cleanup to delete): "
            f"memory={memory_id} stream={stream_name} role={role_name}"
        )


# === AgentCore SDK ====================================================
def run_with_sdk(cleanup: bool = False) -> None:
    import boto3
    from bedrock_agentcore.memory import MemoryClient

    unique = str(uuid.uuid4())[:8]
    kinesis = boto3.client("kinesis", region_name=REGION)
    iam = boto3.client("iam")

    # 1. Kinesis stream + IAM role (no SDK surface — use boto3 directly).
    stream_name = f"memory-record-stream-sdk-{unique}"
    kinesis.create_stream(StreamName=stream_name, ShardCount=1)
    kinesis.get_waiter("stream_exists").wait(StreamName=stream_name)
    stream_arn = kinesis.describe_stream(StreamName=stream_name)["StreamDescription"]["StreamARN"]
    print(f"[sdk] Stream {stream_arn}")

    role_name = f"AgentCoreMemoryStreamingRoleSdk-{unique}"
    role_arn = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=_trust_policy(),
        Description="Allows AgentCore Memory to publish events to Kinesis",
    )["Role"]["Arn"]
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="KinesisPublishPolicy",
        PolicyDocument=_permissions_policy(stream_arn),
    )
    print(f"[sdk] Role {role_arn}; sleeping 10s for IAM propagation")
    time.sleep(10)

    # 2. Memory with stream delivery wired in via SDK kwargs.
    client = MemoryClient(region_name=REGION)
    memory = client.create_memory_and_wait(
        name=f"streaming_memory_sdk_{unique}",
        description="Memory with record streaming enabled (SDK)",
        strategies=[
            {
                "userPreferenceMemoryStrategy": {
                    "name": "UserPreferences",
                    "namespaces": [f"/{ACTOR_ID}/user_preferences/"],
                }
            }
        ],
        event_expiry_days=7,
        memory_execution_role_arn=role_arn,
        # Same structured shape as the boto3 surface (see note above).
        stream_delivery_resources={
            "resources": [
                {
                    "kinesis": {
                        "dataStreamArn": stream_arn,
                        "contentConfigurations": [
                            {"type": "MEMORY_RECORDS", "level": "FULL_CONTENT"},
                        ],
                    }
                }
            ]
        },
    )
    memory_id = memory["id"]
    print(f"[sdk] Memory {memory_id}")

    # 3. Trigger MemoryRecordCreated events directly.
    client.batch_create_memory_records(
        memoryId=memory_id,
        records=[
            {
                "requestIdentifier": "rec-1",
                "namespaces": [f"/{ACTOR_ID}/user_preferences/"],
                "timestamp": str(int(time.time())),
                "content": {"text": "User prefers window seats on flights."},
            },
            {
                "requestIdentifier": "rec-2",
                "namespaces": [f"/{ACTOR_ID}/user_preferences/"],
                "timestamp": str(int(time.time())),
                "content": {"text": "User's favourite language is Python."},
            },
        ],
    )
    print("[sdk] Wrote 2 records — polling Kinesis for events...")

    events = _read_kinesis_events(kinesis, stream_name, max_wait_seconds=60, max_events=10)
    print(f"[sdk] Received {len(events)} stream event(s):")
    for e in events:
        evt = e.get("memoryStreamEvent", {})
        print(f"  - {evt.get('eventType')} @ {evt.get('eventTime')} | record={evt.get('memoryRecordId', 'N/A')}")

    # 4. Cleanup
    if cleanup:
        client.delete_memory_and_wait(memory_id=memory_id)
        kinesis.delete_stream(StreamName=stream_name, EnforceConsumerDeletion=True)
        iam.delete_role_policy(RoleName=role_name, PolicyName="KinesisPublishPolicy")
        iam.delete_role(RoleName=role_name)
        print("[sdk] Cleaned up memory, stream, role")
    else:
        print(
            f"[sdk] Keeping resources (pass --cleanup to delete): "
            f"memory={memory_id} stream={stream_name} role={role_name}"
        )


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--cleanup"]
    cleanup = "--cleanup" in sys.argv[1:]
    surface = args[0] if args else "boto3"
    if surface == "boto3":
        run_with_boto3(cleanup=cleanup)
    elif surface == "sdk":
        run_with_sdk(cleanup=cleanup)
    else:
        print(f"Unknown surface {surface!r}. Use boto3 | sdk.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
