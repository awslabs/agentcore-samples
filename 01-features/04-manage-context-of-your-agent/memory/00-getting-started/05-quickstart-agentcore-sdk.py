import json as _json
import os
import time

import boto3
from bedrock_agentcore.memory import MemoryClient

REGION = os.getenv("AWS_REGION", "us-east-1")

# Get or create a minimal IAM role for AgentCore Memory execution (mirrors
# 04-quickstart-boto3.py so this script also runs without a pre-provisioned role).
_iam = boto3.client("iam", region_name=REGION)
_sts = boto3.client("sts", region_name=REGION)
_account_id = _sts.get_caller_identity()["Account"]
_role_name = "AgentCoreMemoryExecutionRole"
MEMORY_ROLE_ARN = os.getenv(
    "MEMORY_EXECUTION_ROLE_ARN",
    f"arn:aws:iam::{_account_id}:role/{_role_name}",
)
try:
    _iam.get_role(RoleName=_role_name)
except _iam.exceptions.NoSuchEntityException:
    _iam.create_role(
        RoleName=_role_name,
        AssumeRolePolicyDocument=_json.dumps(
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
        ),
        Description="Execution role for AgentCore Memory",
    )
    _iam.put_role_policy(
        RoleName=_role_name,
        PolicyName="BedrockAccess",
        PolicyDocument=_json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "bedrock:InvokeModel",
                            "bedrock:InvokeModelWithResponseStream",
                        ],
                        "Resource": "*",
                    }
                ],
            }
        ),
    )
    # IAM is eventually consistent: give the new role time to propagate before use.
    time.sleep(10)

ACTOR_ID = "user-42"
SESSION_ID = f"sess-{int(time.time())}"

client = MemoryClient(region_name=REGION)

memory = client.create_memory_and_wait(
    name=f"QuickstartMemorySdk_{int(time.time()) % 100000}",
    description="Getting-started memory resource (SDK)",
    strategies=[],
    event_expiry_days=30,
    memory_execution_role_arn=MEMORY_ROLE_ARN,
)
memory_id = memory["id"]
print("Memory:", memory_id, memory["status"])

client.create_event(
    memory_id=memory_id,
    actor_id=ACTOR_ID,
    session_id=SESSION_ID,
    messages=[
        ("My name is Alex and I prefer Python.", "USER"),
        ("Nice to meet you, Alex.", "ASSISTANT"),
    ],
)

turns = client.get_last_k_turns(memory_id=memory_id, actor_id=ACTOR_ID, session_id=SESSION_ID, k=5)
for turn in turns:
    for msg in turn:
        print(msg["role"], "→", msg["content"]["text"])

client.update_memory_strategies_and_wait(
    memory_id=memory_id,
    add_strategies=[
        {
            "semanticMemoryStrategy": {
                "name": "UserFacts",
                "namespaceTemplates": ["/users/{actorId}/facts/"],
            }
        }
    ],
)

# Extraction is asynchronous — give it ~60s before retrieving.
time.sleep(60)

hits = client.retrieve_memories(
    memory_id=memory_id,
    namespace=f"/users/{ACTOR_ID}/facts",
    query="What programming language does the user prefer?",
    top_k=3,
)
for h in hits:
    print(h["content"]["text"])

client.delete_memory_and_wait(memory_id=memory_id)
