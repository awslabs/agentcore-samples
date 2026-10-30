"""Flexible namespaces — your own variables, for multi-tenant multi-agent memory.

Built-in templates can only substitute {actorId}, {sessionId}, {memoryStrategyId}.
Flexible namespaces let you declare your own variables on the memory resource
(`namespaceKeys`) and supply their values per event
(`extractionConfig.namespaceVariables`).

The use case: a trip-planning crew, sold to several tenants
    One tenant, one traveler, one planning session — and three specialist agents
    working that same conversation (flight, hotel, activity). Two custom
    variables carry the dimensions {actorId}/{sessionId} can't:

        tenantid -> whose data this is          (the isolation boundary)
        agentid  -> which crew member wrote it  (shared vs. private memory)

    Only the per-agent template names {agentid}, which is what makes the other two
    crew-wide: there is no wildcard, so a strategy is shared precisely because its
    template doesn't mention the agent. One prefix query then serves each audience.

Corner cases are marked `CORNER CASE`. Exact vs. prefix retrieval and the built-in
variables are in namespaces-and-organization.py; the limits, the key lifecycle,
and the rest of the gotchas are in README.md.

Run:
    python flexible-namespaces.py [--cleanup]

boto3 only — the AgentCore SDK models neither `namespaceKeys` nor
`namespaceVariables`, so there is no `sdk` mode for this feature.

Prerequisites:
    pip install "boto3>=1.43.75"   # first release to model namespaceKeys
    export AWS_REGION=us-east-1
"""

import os
import sys
import time
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

REGION = os.getenv("AWS_REGION", "us-east-1")
EXTRACTION_WAIT_SECONDS = 120  # extraction is async; poll, don't assume

TENANT_ID = "acme"
ACTOR_ID = "traveler-priya"
SESSION_ID = f"trip-tokyo-{int(time.time())}"

# Custom variables are lowercase — it's {agentid}, never {agentId}. Only the
# built-ins keep their canonical mixed case.
PREFERENCES_TEMPLATE = "/tenants/{tenantid}/travelers/{actorId}/shared/preferences/"
HANDOFF_TEMPLATE = "/tenants/{tenantid}/travelers/{actorId}/shared/sessions/{sessionId}/"
FINDINGS_TEMPLATE = "/tenants/{tenantid}/travelers/{actorId}/agents/{agentid}/findings/"

# A key may carry `validation`: an allowedValues list, a regexPattern, or both.
# CORNER CASE: with both, the service ANDs them — rules only narrow, never widen.
# So to admit the three standing specialists *and* a helper the orchestrator
# spawns on demand ("agent-visa-1"), one rule has to cover the whole roster;
# adding an allowedValues roster alongside would reject the spawned agents.
NAMESPACE_KEYS = [
    {"key": "tenantid", "validation": {"allowedValues": ["acme", "globex"]}},
    {"key": "agentid", "validation": {"regexPattern": "^(flight|hotel|activity|agent-[a-z0-9-]+)$"}},
]

# The crew's turns share one actor and one session — only namespaceVariables
# differ. The second tenant is here to show the isolation boundary.
TURNS = [
    (
        ACTOR_ID,
        SESSION_ID,
        {"tenantid": TENANT_ID, "agentid": "flight"},
        "I want to fly to Tokyo in March, and I always take an aisle seat.",
        "Found ANA 809, Seattle to Haneda, March 14 — aisle 32C held for you.",
    ),
    (
        ACTOR_ID,
        SESSION_ID,
        {"tenantid": TENANT_ID, "agentid": "hotel"},
        "Somewhere quiet near a train station. I don't do high floors.",
        "Booked Hotel Ryumeikan Ochanomizu, room 204, two minutes from the JR line.",
    ),
    # 'agent-visa-1' matches the spawned-agent alternative in the regexPattern, so a
    # helper created mid-session gets its own lane with no control-plane change.
    (
        ACTOR_ID,
        SESSION_ID,
        {"tenantid": TENANT_ID, "agentid": "agent-visa-1"},
        "Do I need a visa? My passport is Indian.",
        "Indian passport holders need a short-stay visa for Japan; apply by February.",
    ),
    # A different tenant, same product. Nothing here can surface under /tenants/acme/.
    (
        "traveler-sam",
        f"trip-lisbon-{int(time.time())}",
        {"tenantid": "globex", "agentid": "flight"},
        "I need to get to Lisbon in April, window seat please.",
        "Held TAP 204, Boston to Lisbon, April 3 — window 18A.",
    ),
    # CORNER CASE: omitting a variable is NOT an error — and that's the danger.
    # CreateEvent succeeds and the shared templates still resolve, but Findings has
    # no value for {agentid}, so its extraction is silently dropped: the budget
    # below surfaces as a shared preference and never as a finding.
    (
        ACTOR_ID,
        SESSION_ID,
        {"tenantid": TENANT_ID},
        "Keep the whole trip under $4000.",
        "Noted — I'll hold the total budget at $4000.",
    ),
]

control = boto3.client("bedrock-agentcore-control", region_name=REGION)
data = boto3.client("bedrock-agentcore", region_name=REGION)


def wait_active(memory_id: str) -> None:
    deadline = time.time() + 300
    while control.get_memory(memoryId=memory_id)["memory"]["status"] != "ACTIVE":
        if time.time() > deadline:
            raise TimeoutError(f"{memory_id} never became ACTIVE")
        time.sleep(5)


def agent_turn(memory_id: str, actor_id: str, session_id: str, variables: dict, user: str, agent: str) -> None:
    """One agent's turn. Values are substituted verbatim into the namespace path."""
    data.create_event(
        memoryId=memory_id,
        actorId=actor_id,
        sessionId=session_id,
        eventTimestamp=datetime.now(timezone.utc),
        payload=[
            {"conversational": {"role": "USER", "content": {"text": user}}},
            {"conversational": {"role": "ASSISTANT", "content": {"text": agent}}},
        ],
        # CORNER CASE: namespaceVariables has a minimum of 1 entry, so an entirely
        # untagged turn must omit extractionConfig — passing {} is rejected.
        extractionConfig={"namespaceVariables": variables},
    )


def main(cleanup: bool = False) -> None:
    # === 1. Declare the variables the templates may use ==================
    # Every custom variable in a template must be declared here. The reverse isn't
    # required: a declared-but-unreferenced key is legal ("dangling"), which is how
    # you reserve a name ahead of a strategy rollout.
    memory = control.create_memory(
        name=f"FlexNamespaces_{int(time.time())}",
        description="Flexible namespaces — multi-tenant, multi-agent trip planning crew",
        eventExpiryDuration=30,
        memoryStrategies=[
            {"userPreferenceMemoryStrategy": {"name": "Preferences", "namespaceTemplates": [PREFERENCES_TEMPLATE]}},
            {"summaryMemoryStrategy": {"name": "Handoff", "namespaceTemplates": [HANDOFF_TEMPLATE]}},
            {"semanticMemoryStrategy": {"name": "Findings", "namespaceTemplates": [FINDINGS_TEMPLATE]}},
        ],
        namespaceKeys=NAMESPACE_KEYS,
    )["memory"]
    memory_id = memory["id"]
    # .get, not [..]: an older botocore model yields no such response member at all.
    print(f"Created {memory_id} with keys {[k['key'] for k in memory.get('namespaceKeys', [])]}")
    wait_active(memory_id)

    # === 2. Resolve the variables per turn ===============================
    for actor_id, session_id, variables, user, agent in TURNS:
        agent_turn(memory_id, actor_id, session_id, variables, user, agent)
    print(f"Wrote {len(TURNS)} turns: 3 tagged crew turns, 1 other tenant, 1 missing {{agentid}}")

    # CORNER CASE: validation runs on CreateEvent, so a bad value drops the whole
    # event — its short-term turns too, not just the long-term records. An agent
    # that isn't on the roster ('billing') costs the crew the turn, not just its lane.
    try:
        agent_turn(memory_id, ACTOR_ID, SESSION_ID, {"tenantid": TENANT_ID, "agentid": "billing"}, "x", "x")
    except ClientError as exc:
        print(f"Rejected as expected: {exc.response['Error']['Message']}")

    # === 3. The payoff: one prefix query per audience ====================
    # You query resolved paths, never templates. Read the namespaces printed below
    # rather than assuming — they show which variables resolved.
    print(f"\nPolling up to {EXTRACTION_WAIT_SECONDS}s for extraction...")
    records, deadline = [], time.time() + EXTRACTION_WAIT_SECONDS
    while not records and time.time() < deadline:
        records = data.list_memory_records(memoryId=memory_id, namespacePath=f"/tenants/{TENANT_ID}/")[
            "memoryRecordSummaries"
        ]
        if not records:
            time.sleep(10)

    for label, path in [
        ("this tenant only — the IAM boundary", f"/tenants/{TENANT_ID}/"),
        ("crew-wide — what every agent reads", f"/tenants/{TENANT_ID}/travelers/{ACTOR_ID}/shared/"),
        ("the hotel agent's lane only", f"/tenants/{TENANT_ID}/travelers/{ACTOR_ID}/agents/hotel/"),
        ("the other tenant, untouched by the queries above", "/tenants/globex/"),
    ]:
        found = data.list_memory_records(memoryId=memory_id, namespacePath=path)["memoryRecordSummaries"]
        print(f"\n{path}  — {label} ({len(found)}):")
        for record in found:
            print(f"  - [{','.join(record.get('namespaces', []))}] {record['content']['text'][:60]}")

    if cleanup:
        control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
        print(f"\nDeleted memory {memory_id}")
    else:
        print(f"\nKeeping memory {memory_id} (pass --cleanup to delete)")


if __name__ == "__main__":
    main(cleanup="--cleanup" in sys.argv[1:])
