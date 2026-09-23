import os
import time

from bedrock_agentcore.memory import MemoryClient

REGION = os.getenv("AWS_REGION", "us-east-1")
MEMORY_ROLE_ARN = os.environ["MEMORY_EXECUTION_ROLE_ARN"]
ACTOR_ID = "user-42"
SESSION_ID = f"sess-{int(time.time())}"

client = MemoryClient(region_name=REGION)

memory = client.create_memory_and_wait(
    name="QuickstartMemorySdk",
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
