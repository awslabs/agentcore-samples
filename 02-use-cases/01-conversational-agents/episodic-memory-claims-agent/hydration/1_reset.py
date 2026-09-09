"""
Reset the demo to a clean slate (repeatable), keeping the SAME memory resource.

Clears, in place:
  1. all HITL review tasks (DynamoDB)
  2. all session-backend session rows (DynamoDB)
  3. all AgentCore Memory events for every (actor, session) it can find
  4. all AgentCore Memory records (episodes + reflections)

Keeping the same memory_id (vs recreating) means no SSM churn and no Lambda
cold-start staleness — only the contents are wiped. The custom strategy/prompts
already live on the memory, so they're preserved.

Usage:
    python hydration/1_reset.py
"""

import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", "src"))

from memory.config import get_memory_id, load_config

ACTORS = ["PH-1001", "PH-1042", "PH-1087", "PH-2001", "PH-2050", "PH-3001", "PH-3050"]


def _clear_table(ddb, name, key_fields):
    table = ddb.Table(name)
    items = table.scan().get("Items", [])
    for it in items:
        table.delete_item(Key={k: it[k] for k in key_fields})
    return len(items), items


def _list_records(bac, memory_id, ns):
    out, token = [], None
    while True:
        kw = {"memoryId": memory_id, "namespace": ns, "maxResults": 100}
        if token:
            kw["nextToken"] = token
        r = bac.list_memory_records(**kw)
        out.extend(r.get("memoryRecordSummaries", []))
        token = r.get("nextToken")
        if not token:
            break
    return out


def _list_events(bac, memory_id, actor, session):
    out, token = [], None
    while True:
        kw = {"memoryId": memory_id, "actorId": actor, "sessionId": session, "maxResults": 100}
        if token:
            kw["nextToken"] = token
        r = bac.list_events(**kw)
        out.extend(r.get("events", []))
        token = r.get("nextToken")
        if not token:
            break
    return out


def main():
    config = load_config()
    region = config["region"]
    memory_id = get_memory_id(config)
    ddb = boto3.resource("dynamodb", region_name=region)
    bac = boto3.client("bedrock-agentcore", region_name=region)

    print(f"=== Reset demo state (memory: {memory_id}) ===")

    # 1. Review tasks
    n_tasks, _ = _clear_table(ddb, config["review_tasks_table"], ["task_id"])
    print(f"  review tasks deleted: {n_tasks}")

    # 2. Session rows (capture actor/session pairs first, for event deletion)
    sess_table = config.get("session_backend", {}).get("table_name", "insurance-claims-session-backend-sessions")
    session_pairs = set()
    try:
        rows = ddb.Table(sess_table).scan().get("Items", [])
        for r in rows:
            if r.get("actor_id") and r.get("session_id"):
                session_pairs.add((r["actor_id"], r["session_id"]))
        n_sess, _ = _clear_table(ddb, sess_table, ["user_id", "session_id"])
        print(f"  session rows deleted: {n_sess}")
    except Exception as e:  # noqa: BLE001 - best-effort cleanup; continue on error
        print(f"  (session table note: {e})")

    # 3. Memory records — collect record ids + derive (actor, session) from episode namespaces
    record_ids = set()
    namespaces = ["claims/"] + [f"claims/{a}/" for a in ACTORS]
    for ns in namespaces:
        for rec in _list_records(bac, memory_id, ns):
            rid = rec.get("memoryRecordId")
            if rid:
                record_ids.add(rid)
            for n in rec.get("namespaces") or []:
                parts = n.strip("/").split("/")
                if len(parts) >= 3 and parts[0] == "claims":
                    session_pairs.add((parts[1], parts[2]))

    # 4. Delete events for every (actor, session)
    n_events = 0
    for actor, session in session_pairs:
        for e in _list_events(bac, memory_id, actor, session):
            try:
                bac.delete_event(memoryId=memory_id, actorId=actor, sessionId=session, eventId=e["eventId"])
                n_events += 1
            except Exception as ex:  # noqa: BLE001 - best-effort cleanup; continue on error
                print(f"    (event delete note {session}: {ex})")
    print(f"  memory events deleted: {n_events} (across {len(session_pairs)} session(s))")

    # 5. Delete memory records (episodes + reflections)
    n_rec = 0
    for rid in record_ids:
        try:
            bac.delete_memory_record(memoryId=memory_id, memoryRecordId=rid)
            n_rec += 1
        except Exception as ex:  # noqa: BLE001 - best-effort cleanup; continue on error
            print(f"    (record delete note {rid}: {ex})")
    print(f"  memory records deleted: {n_rec}")

    print("✅ Reset complete. Memory is empty; run hydration/2_autoseed.py next.")


if __name__ == "__main__":
    main()
