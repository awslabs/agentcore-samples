import os
import time
import uuid
from datetime import datetime, timezone

import boto3

REGION = os.getenv("AWS_REGION", "us-east-1")
ACTOR_ID = "user-42"

# Get or create a minimal IAM role for AgentCore Memory execution
_iam = boto3.client("iam", region_name=REGION)
_sts = boto3.client("sts", region_name=REGION)
_account_id = _sts.get_caller_identity()["Account"]
_role_name = "AgentCoreMemoryExecutionRole"
MEMORY_ROLE_ARN = os.getenv(
    "MEMORY_EXECUTION_ROLE_ARN",
    f"arn:aws:iam::{_account_id}:role/{_role_name}",
)
# Create the role if it doesn't exist
try:
    _iam.get_role(RoleName=_role_name)
except _iam.exceptions.NoSuchEntityException:
    import json as _json

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
    # IAM is eventually consistent: CreateMemory below fails with "Please provide a role
    # with a valid trust policy" if it runs before the new role has propagated.
    time.sleep(10)
SESSION_ID = f"sess-{int(time.time())}"

control = boto3.client("bedrock-agentcore-control", region_name=REGION)
data = boto3.client("bedrock-agentcore", region_name=REGION)

resp = control.create_memory(
    name="QuickstartMemory",
    description="Getting-started memory resource",
    eventExpiryDuration=30,
    memoryExecutionRoleArn=MEMORY_ROLE_ARN,
    clientToken=str(uuid.uuid4()),
)
memory_id = resp["memory"]["id"]
print("Created:", memory_id)

# Wait for ACTIVE
deadline = time.time() + 300
while time.time() < deadline:
    status = control.get_memory(memoryId=memory_id)["memory"]["status"]
    if status == "ACTIVE":
        break
    if status == "FAILED":
        raise RuntimeError("Memory creation failed")
    time.sleep(5)
print("Status:", status)

data.create_event(
    memoryId=memory_id,
    actorId=ACTOR_ID,
    sessionId=SESSION_ID,
    eventTimestamp=datetime.now(timezone.utc),
    payload=[
        {
            "conversational": {
                "role": "USER",
                "content": {"text": "My name is Alex and I prefer Python."},
            }
        },
        {
            "conversational": {
                "role": "ASSISTANT",
                "content": {"text": "Nice to meet you, Alex."},
            }
        },
    ],
)

events = data.list_events(memoryId=memory_id, actorId=ACTOR_ID, sessionId=SESSION_ID)["events"]
for e in events:
    print(e["eventId"], e["eventTimestamp"])

# Fetch one
if events:
    full = data.get_event(
        memoryId=memory_id,
        actorId=ACTOR_ID,
        sessionId=SESSION_ID,
        eventId=events[0]["eventId"],
    )
    print(full["event"]["payload"])

control.update_memory(
    memoryId=memory_id,
    clientToken=str(uuid.uuid4()),
    memoryStrategies={
        "addMemoryStrategies": [
            {
                "semanticMemoryStrategy": {
                    "name": "UserFacts",
                    "namespaceTemplates": ["/users/{actorId}/facts/"],
                }
            }
        ]
    },
)

# UpdateMemory is asynchronous. Wait for ACTIVE before relying on the new strategy —
# extraction only runs once it is applied, and DeleteMemory below is rejected while
# the resource is still UPDATING.
deadline = time.time() + 300
while time.time() < deadline:
    status = control.get_memory(memoryId=memory_id)["memory"]["status"]
    if status == "ACTIVE":
        break
    time.sleep(5)

# Extraction is asynchronous — give it ~90s before retrieving.
time.sleep(90)

hits = data.retrieve_memory_records(
    memoryId=memory_id,
    namespace=f"/users/{ACTOR_ID}/facts",
    searchCriteria={
        "searchQuery": "What programming language does the user prefer?",
        "topK": 3,
    },
)["memoryRecordSummaries"]

for h in hits:
    print(h["content"]["text"])

control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
