"""Self-managed memory strategy — you own the extraction pipeline.

What you learn:
    - Configure `customMemoryStrategy` with `selfManagedConfiguration`
    - Set trigger conditions (messageBasedTrigger / tokenBasedTrigger /
      timeBasedTrigger)
    - Wire the SNS topic that AgentCore notifies when a trigger fires
    - Write extracted records back via `BatchCreateMemoryRecords`

Self-managed flow:
    1. AgentCore writes the conversation payload to your S3 bucket
       (the bucket that the memoryExecutionRoleArn can write to)
    2. AgentCore publishes a notification to your SNS topic
    3. Your subscriber (Lambda, ECS task, etc.) reads the payload from S3,
       runs your extraction + consolidation logic, and calls
       BatchCreateMemoryRecords / BatchUpdateMemoryRecords / BatchDeleteMemoryRecords
       to persist results.

This script focuses on (1) wiring the strategy correctly. The extraction
subscriber lives outside AgentCore — see the example in
`examples/single-agent/with-strands-agent/02-custom-hook/
culinary-assistant-self-managed-strategy/lambda_function.py`.

Two surfaces:
    python self-managed-strategy.py boto3
    python self-managed-strategy.py sdk

Add `--cleanup` to delete the memory resource at the end. By default the
memory is kept so you can inspect it; the script prints the memoryId.

Prerequisites:
    pip install boto3 bedrock-agentcore
    export AWS_REGION=us-east-1   # use any AgentCore-supported region
    export MEMORY_EXECUTION_ROLE_ARN=arn:aws:iam::<acct>:role/<role>
    export PAYLOAD_BUCKET=my-agentcore-payload-bucket
    export TOPIC_ARN=arn:aws:sns:<region>:<acct>:agentcore-self-managed
"""

import os
import sys
import time
import uuid

REGION = os.getenv("AWS_REGION", "us-east-1")
NAMESPACE_TEMPLATE = "/users/{actorId}/custom/"


def _strategy(payload_bucket: str, topic_arn: str) -> dict:
    return {
        "customMemoryStrategy": {
            "name": "MyOwnExtractor",
            "description": "Custom extraction owned by my Lambda",
            "namespaceTemplates": [NAMESPACE_TEMPLATE],
            "configuration": {
                "selfManagedConfiguration": {
                    "invocationConfiguration": {
                        "payloadDeliveryBucketName": payload_bucket,
                        "topicArn": topic_arn,
                    },
                    "historicalContextWindowSize": 10,
                    "triggerConditions": [
                        {"messageBasedTrigger": {"messageCount": 6}},
                        {"tokenBasedTrigger": {"tokenCount": 4000}},
                        {"timeBasedTrigger": {"idleSessionTimeout": 300}},
                    ],
                }
            },
        }
    }


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    role_arn = os.environ["MEMORY_EXECUTION_ROLE_ARN"]
    bucket = os.environ["PAYLOAD_BUCKET"]
    topic_arn = os.environ["TOPIC_ARN"]

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    memory_id = control.create_memory(
        name=f"SelfManaged_{int(time.time())}",
        description="Self-managed extraction strategy (boto3)",
        eventExpiryDuration=30,
        memoryExecutionRoleArn=role_arn,
        memoryStrategies=[_strategy(bucket, topic_arn)],
    )["memory"]["id"]
    print(f"[boto3] Created memory {memory_id}")
    deadline = time.time() + 300
    while time.time() < deadline:
        if control.get_memory(memoryId=memory_id)["memory"]["status"] == "ACTIVE":
            break
        time.sleep(5)

    print(
        f"[boto3] Memory ready. Send events with CreateEvent; AgentCore delivers\n"
        f"        payloads to s3://{bucket}/ and notifies {topic_arn} when a\n"
        f"        trigger fires. Have your subscriber call BatchCreateMemoryRecords\n"
        f"        to persist extracted records."
    )

    if cleanup:
        control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
        print(f"[boto3] Deleted memory {memory_id}")
    else:
        print(f"[boto3] Keeping memory {memory_id} (pass --cleanup to delete)")


# === AgentCore SDK ====================================================
def run_with_sdk(cleanup: bool = False) -> None:
    from bedrock_agentcore.memory import MemoryClient

    role_arn = os.environ["MEMORY_EXECUTION_ROLE_ARN"]
    bucket = os.environ["PAYLOAD_BUCKET"]
    topic_arn = os.environ["TOPIC_ARN"]

    client = MemoryClient(region_name=REGION)
    memory = client.create_memory_and_wait(
        name=f"SelfManagedSdk_{int(time.time())}",
        description="Self-managed extraction strategy (SDK)",
        strategies=[_strategy(bucket, topic_arn)],
        event_expiry_days=30,
        memory_execution_role_arn=role_arn,
    )
    memory_id = memory["id"]
    print(f"[sdk] Created memory {memory_id}")

    print(
        f"[sdk] Memory ready. Send events with create_event; AgentCore delivers\n"
        f"        payloads to s3://{bucket}/ and notifies {topic_arn} when a\n"
        f"        trigger fires. Your subscriber calls\n"
        f"        client.batch_create_memory_records (forwarded by MemoryClient)\n"
        f"        to persist extracted records."
    )

    if cleanup:
        client.delete_memory_and_wait(memory_id=memory_id)
        print(f"[sdk] Deleted memory {memory_id}")
    else:
        print(f"[sdk] Keeping memory {memory_id} (pass --cleanup to delete)")


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
