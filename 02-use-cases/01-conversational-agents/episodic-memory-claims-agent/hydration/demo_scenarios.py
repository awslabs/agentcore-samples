"""
Demo scenarios — designed to trigger memory pattern retrieval and show
the adjudication agent consulting learned reflections.

These run in AUTO mode and are meant for live demo:
  - Scenario A: should find approval patterns (clean claim, matches training)
  - Scenario B: should find denial/escalation patterns (delayed + repeat)

Usage:
    # Run a scenario
    python hydration/demo_scenarios.py run --scenario demo-clean-fire
    python hydration/demo_scenarios.py run --scenario demo-delayed-repeat

    # Delete a scenario's session + memory events (before extraction runs)
    python hydration/demo_scenarios.py delete --session <session_id>

    # List recent demo sessions
    python hydration/demo_scenarios.py list
"""

import argparse
import json
import os
import sys

import boto3
import requests
from strands import Agent

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", "src"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.config import get_memory_id, load_config

MODEL_ID = "global.anthropic.claude-sonnet-4-6"

DEMO_SCENARIOS = {
    "demo-clean-fire": {
        "name": "David Park",
        "actor_id": "PH-2001",
        "opening": (
            "Hi, I need to file a homeowner's claim. There was an electrical fire "
            "in my laundry room yesterday — the dryer overheated and scorched the wall "
            "and melted the electrical outlet. My policy is HO-2024-2001."
        ),
        "facts": """\
- Incident: electrical fire from dryer overheating in the laundry room.
- Date: yesterday (one day ago). Filed today.
- Damage: scorched wall behind dryer (~4x4 ft area), melted electrical outlet, damaged dryer (totaled), smoke residue on ceiling.
- Injuries: none.
- Fire department: yes, called 911, they responded and confirmed it out. Incident #FD-2026-0705.
- Estimated cost: about $6,500 (wall repair $2,000, outlet replacement $500, new dryer $1,200, smoke cleanup $2,800).
- Documentation: photos taken immediately, fire department report available.
- No prior claims — first time filing.
- Contact: David Park, (555) 020-2001, 88 Willow Lane, Springfield, IL 62704.
""",
        "description": "Clean fire claim — should auto-approve. Triggers fire/documentation approval patterns.",
    },
    "demo-delayed-repeat": {
        "name": "Charlie Davis",
        "actor_id": "PH-1087",
        "opening": (
            "I need to file a claim. There was water damage in my basement — a pipe "
            "started leaking behind the utility shelf. I noticed it about three weeks "
            "ago but I'm just now getting around to filing. Policy HO-2024-1087."
        ),
        "facts": """\
- Incident: slow pipe leak behind the utility shelf in the basement.
- Date: noticed about 3 weeks ago (roughly June 15, 2026). Filing today (~21-day delay).
- Damage: water-stained drywall, warped baseboard, some mold starting on the bottom of the wall.
- Injuries: none.
- Mitigation: I put a bucket under it and ran a fan. Didn't call a plumber until last week.
- Plumber fixed it 5 days ago. Have the invoice ($380).
- Estimated cost: about $8,500 (drywall, baseboard, mold remediation, repaint).
- Documentation: photos of the current damage, plumber invoice. No professional mold assessment yet.
- If asked about delay: "Honestly I thought it was minor and would dry out. It got worse."
- If asked about prior claims: "Yeah, I had a water damage issue about a year and a half ago too."
- Contact: Charlie Davis, (555) 014-1087, 27 Cedar Court, Springfield, IL 62704.
""",
        "description": "Delayed + repeat water damage — should deny/escalate. Triggers fraud pattern reflections.",
    },
}

_DONE_MARKERS = (
    "approved",
    "denied",
    "under review",
    "received your claim",
    "be contacted",
    "will be in touch",
    "now under review",
    "decision:",
    "not able to approve",
    "unable to approve",
    "claim has been denied",
    "i appreciate your help",
    "thank you for letting me know",
)

SIMULANT_SYSTEM = """\
You are {name}, a policyholder filing an insurance claim. Stay in character.
Answer the assistant's questions using ONLY the claim facts below. Keep replies
natural and concise. Never reveal you are an AI.

IMPORTANT: Once the assistant delivers a decision (approved, denied, or under
review), respond with a brief acknowledgment and end the conversation. Say
something like "Okay, thank you for letting me know. I appreciate your help."
Do NOT ask follow-up questions, do NOT express frustration, do NOT ask about
appeals. Just acknowledge and wrap up.

CLAIM FACTS:
{facts}

You have ALREADY sent this opening message:
"{opening}"

The assistant will now ask follow-up questions. Answer them as {name}.
"""


def _login(cognito, client_id, username, password):
    r = cognito.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        ClientId=client_id,
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    return r["AuthenticationResult"]["IdToken"], r["AuthenticationResult"]["AccessToken"]


def _create_session(session_api, id_token, title):
    resp = requests.post(
        f"{session_api}/sessions",
        headers={"Authorization": f"Bearer {id_token}", "Content-Type": "application/json"},
        json={"session_title": title},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["session"]


def call_agent(prompt, actor_id, session_id, runtime_url, access_token):
    resp = requests.post(
        runtime_url,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"prompt": prompt, "actorId": actor_id, "sessionId": session_id, "memoryMode": "reflections"},
        timeout=300,
    )
    resp.raise_for_status()
    message = ""
    for line in resp.text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            parsed = json.loads(line[6:])
            delta = parsed.get("event", {}).get("contentBlockDelta", {}).get("delta", {}).get("text")
            if delta:
                event = json.loads(delta)
                if event.get("event") == "message":
                    message = event.get("data", "")
                elif event.get("event") == "error":
                    raise RuntimeError(f"agent error: {event.get('data')}")
                continue
            if parsed.get("event") == "message":
                message = parsed.get("data", "")
            elif parsed.get("event") == "error":
                raise RuntimeError(f"agent error: {parsed.get('data')}")
        except json.JSONDecodeError:
            continue
    return message


def is_done(text):
    low = text.lower()
    return any(m in low for m in _DONE_MARKERS)


def run_scenario(scenario_id):
    scn = DEMO_SCENARIOS.get(scenario_id)
    if not scn:
        print(f"ERROR: Unknown scenario '{scenario_id}'. Available: {list(DEMO_SCENARIOS.keys())}")
        sys.exit(1)

    config = load_config()
    region = config["region"]
    client_id = config["cognito"]["client_id"]
    runtime_url = config.get("agentcore_runtime", {}).get("url", "")
    session_api = config.get("session_backend", {}).get("api_url")

    if not runtime_url:
        print("ERROR: agentcore_runtime.url not in config.json")
        sys.exit(1)

    creds = {u["actor_id"]: u for u in config.get("users", [])}
    u = creds[scn["actor_id"]]
    cognito = boto3.client("cognito-idp", region_name=region)
    id_token, access_token = _login(cognito, client_id, u["username"], u["password"])

    session = _create_session(session_api, id_token, f"[Auto] {scenario_id}")
    session_id = session["session_id"]
    actor_id = session.get("actor_id") or scn["actor_id"]

    print(f"Scenario: {scenario_id}")
    print(f"  {scn['description']}")
    print(f"  Actor: {actor_id} | Session: {session_id}")
    print()

    simulant = Agent(
        model=MODEL_ID,
        system_prompt=SIMULANT_SYSTEM.format(name=scn["name"], facts=scn["facts"], opening=scn["opening"]),
    )

    customer_msg = scn["opening"]
    print(f"[{scn['name']}] {customer_msg}")

    for turn in range(1, 8):
        agent_text = call_agent(customer_msg, actor_id, session_id, runtime_url, access_token)
        print(f"\n[Agent] {agent_text}\n")

        if is_done(agent_text):
            print(f"--- done (turn {turn}) | session: {session_id} ---")
            return session_id

        if turn == 7:
            print(f"--- max turns | session: {session_id} ---")
            return session_id

        customer_msg = str(simulant(agent_text)).strip()
        print(f"[{scn['name']}] {customer_msg}")

        # Stop if simulant is saying goodbye
        if any(m in customer_msg.lower() for m in ("bye", "goodbye", "take care", "have a good")):
            print(f"--- done (turn {turn}) | session: {session_id} ---")
            return session_id

    return session_id


def delete_session(session_id):
    config = load_config()
    region = config["region"]
    memory_id = get_memory_id(config)
    ddb = boto3.resource("dynamodb", region_name=region)
    bac = boto3.client("bedrock-agentcore", region_name=region)

    print(f"Deleting session: {session_id}")
    print(f"  Memory: {memory_id}")

    # 1. Delete session row
    sess_table = ddb.Table(
        config.get("session_backend", {}).get("table_name", "insurance-claims-session-backend-sessions")
    )
    items = sess_table.scan().get("Items", [])
    deleted_sess = 0
    for it in items:
        if it.get("session_id") == session_id:
            sess_table.delete_item(Key={"user_id": it["user_id"], "session_id": it["session_id"]})
            deleted_sess += 1
    print(f"  Session rows deleted: {deleted_sess}")

    # 2. Delete review tasks
    review_table = ddb.Table(config.get("reviews_backend", {}).get("table_name", "insurance-claims-demo-review-tasks"))
    reviews = review_table.scan().get("Items", [])
    deleted_reviews = 0
    for r in reviews:
        if r.get("session_id") == session_id or r.get("task_id") == session_id:
            review_table.delete_item(Key={"task_id": r["task_id"]})
            deleted_reviews += 1
    print(f"  Review tasks deleted: {deleted_reviews}")

    # 3. Delete memory events for this session (all actors including 'system' for subtools)
    deleted_events = 0
    for actor in ["system", "PH-1001", "PH-1042", "PH-1087", "PH-2001", "PH-2050", "PH-3001", "PH-3050"]:
        try:
            evs = bac.list_events(memoryId=memory_id, actorId=actor, sessionId=session_id, maxResults=100).get(
                "events", []
            )
            for e in evs:
                bac.delete_event(memoryId=memory_id, actorId=actor, sessionId=session_id, eventId=e["eventId"])
                deleted_events += 1
        except Exception as e:  # noqa: BLE001 - best-effort cleanup; continue on error
            print(f"  (cleanup note: {e})")
    print(f"  Memory events deleted: {deleted_events}")

    # 4. Delete episode records for this session
    deleted_records = 0
    for actor in ["PH-1001", "PH-1042", "PH-1087", "PH-2001", "PH-2050", "PH-3001", "PH-3050"]:
        ns = f"claims/{actor}/{session_id}/"
        try:
            recs = bac.list_memory_records(memoryId=memory_id, namespace=ns, maxResults=20).get(
                "memoryRecordSummaries", []
            )
            for r in recs:
                bac.delete_memory_record(memoryId=memory_id, memoryRecordId=r["memoryRecordId"])
                deleted_records += 1
        except Exception as e:  # noqa: BLE001 - best-effort cleanup; continue on error
            print(f"  (cleanup note: {e})")
    print(f"  Episode records deleted: {deleted_records}")
    print("  Done.")


def list_sessions():
    config = load_config()
    ddb = boto3.resource("dynamodb", region_name=config["region"])
    sess_table = ddb.Table(
        config.get("session_backend", {}).get("table_name", "insurance-claims-session-backend-sessions")
    )
    items = sess_table.scan().get("Items", [])
    demo_sessions = [
        i for i in items if "[Auto]" in (i.get("session_title") or "") or "[Training]" in (i.get("session_title") or "")
    ]
    demo_sessions.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    if not demo_sessions:
        print("No demo sessions found.")
        return

    print(f"Demo sessions ({len(demo_sessions)}):")
    for s in demo_sessions:
        print(
            f"  {s.get('session_id', '?'):40} {s.get('actor_id', '?'):10} {s.get('session_title', '')[:50]}  {s.get('created_at', '')[:16]}"
        )


def main():
    parser = argparse.ArgumentParser(description="Demo scenario runner with cleanup")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run a demo scenario")
    run_p.add_argument("--scenario", required=True, choices=list(DEMO_SCENARIOS.keys()))

    del_p = sub.add_parser("delete", help="Delete a session and its memory events")
    del_p.add_argument("--session", required=True, help="Session ID to delete")

    sub.add_parser("list", help="List recent demo sessions")

    args = parser.parse_args()

    if args.command == "run":
        run_scenario(args.scenario)
    elif args.command == "delete":
        delete_session(args.session)
    elif args.command == "list":
        list_sessions()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
