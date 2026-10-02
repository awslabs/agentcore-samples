"""
Auto-seed claims by self-play: an LLM "customer simulant" (the policyholder)
converses with the real claims agent until each claim is filed.

For each scenario it:
  1. sends the opening message to the AgentCore Runtime,
  2. reads the agent's reply,
  3. if the agent asks follow-ups, the simulant answers them from the brief,
  4. repeats until the agent closes the claim ("under review") or a turn cap.

Human mode only (the agent files a review task via submit_claim_for_human_review;
no decision is made). Resolution is left to a human adjuster in the console.

Usage:
    python hydration/2_autoseed.py                       # all scenarios
    python hydration/2_autoseed.py --scenario bob-garage-fire alice-jewelry-theft
    python hydration/2_autoseed.py --max-turns 6
    python hydration/2_autoseed.py --runtime-url <url>   # override runtime URL
"""

import argparse
import json
import os
import sys
import time

import boto3
import requests
from strands import Agent

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "agent", "src"))
sys.path.insert(0, PROJECT_ROOT)

from hydration.scenarios import get_scenarios
from memory.config import get_decision_mode, load_config

MODEL_ID = "global.anthropic.claude-sonnet-4-6"

_DONE_MARKERS = (
    "under review",
    "received your claim",
    "received all the details",
    "be contacted",
    "contacted with a decision",
    "will be in touch",
    "now under review",
    "recorded everything",
)

SIMULANT_SYSTEM = """\
You are {name}, a policyholder filing an insurance claim with your insurer's
claims assistant. Stay fully in character as the customer.

Answer the assistant's questions using ONLY the claim facts below. If you're
asked something not covered, give a brief, plausible answer consistent with the
facts. Keep replies natural and concise, like a real person, answer what was
asked, don't dump everything at once. Never reveal you are an AI or that these
are scripted facts, and never mention "facts", "scenario", or "brief".

CLAIM FACTS:
{facts}

You have ALREADY sent this opening message to the assistant:
"{opening}"

The assistant will now ask follow-up questions. Answer them as {name}.
"""


def call_agent(prompt: str, actor_id: str, session_id: str, runtime_url: str, access_token: str) -> str:
    """Call the AgentCore Runtime and parse the response."""
    resp = requests.post(
        runtime_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "prompt": prompt,
            "actorId": actor_id,
            "sessionId": session_id,
            "memoryMode": "reflections",
        },
        timeout=300,
    )
    resp.raise_for_status()
    message = ""
    for line in resp.text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            parsed = json.loads(line[6:])
            # AgentCore format
            delta = parsed.get("event", {}).get("contentBlockDelta", {}).get("delta", {}).get("text")
            if delta:
                event = json.loads(delta)
                if event.get("event") == "message":
                    message = event.get("data", "")
                elif event.get("event") == "error":
                    raise RuntimeError(f"agent error: {event.get('data')}")
                continue
            # Flask SSE fallback (agentcore dev local)
            if parsed.get("event") == "message":
                message = parsed.get("data", "")
            elif parsed.get("event") == "error":
                raise RuntimeError(f"agent error: {parsed.get('data')}")
        except json.JSONDecodeError:
            continue
    return message


def is_done(agent_text: str) -> bool:
    low = agent_text.lower()
    return any(m in low for m in _DONE_MARKERS)


def _login(cognito, client_id: str, username: str, password: str):
    """Authenticate and return (id_token, access_token)."""
    r = cognito.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        ClientId=client_id,
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    return (
        r["AuthenticationResult"]["IdToken"],
        r["AuthenticationResult"]["AccessToken"],
    )


def _create_session(session_api: str, id_token: str, title: str) -> dict:
    """Create a session via the session backend."""
    resp = requests.post(
        f"{session_api}/sessions",
        headers={"Authorization": f"Bearer {id_token}", "Content-Type": "application/json"},
        json={"session_title": title},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["session"]


def _title_for(scn: dict) -> str:
    return f"[Training] {scn['id']}"


def run_scenario(
    scn: dict, runtime_url: str, session_api: str, id_token: str, access_token: str, max_turns: int
) -> str:
    """Run one scenario end-to-end. Returns the session_id."""
    session = _create_session(session_api, id_token, _title_for(scn))
    session_id = session["session_id"]
    actor_id = session.get("actor_id") or scn["actor_id"]

    print(f"\n{'=' * 70}\nSCENARIO {scn['id']}  ({scn['name']}, {actor_id}, session {session_id})\n{'=' * 70}")

    simulant = Agent(
        model=MODEL_ID,
        system_prompt=SIMULANT_SYSTEM.format(name=scn["name"], facts=scn["facts"], opening=scn["opening"]),
    )

    customer_msg = scn["opening"]
    print(f"\n[{scn['name']}] {customer_msg}")

    for turn in range(1, max_turns + 1):
        agent_text = call_agent(customer_msg, actor_id, session_id, runtime_url, access_token)
        print(f"\n[Agent] {agent_text}\n")

        if is_done(agent_text):
            print(f"--- claim filed (turn {turn}) ---")
            return session_id

        if turn == max_turns:
            print(f"--- reached max turns ({max_turns}) without a clear close ---")
            return session_id

        customer_msg = str(simulant(agent_text)).strip()
        print(f"[{scn['name']}] {customer_msg}")

    return session_id


def main():
    parser = argparse.ArgumentParser(description="Auto-seed claims via customer-simulant self-play")
    parser.add_argument("--scenario", nargs="*", help="Scenario id(s) to run (default: all)")
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between scenarios")
    parser.add_argument("--runtime-url", default=None, help="Override AgentCore Runtime URL")
    parser.add_argument("--session-api", default=None, help="Override session backend API URL")
    args = parser.parse_args()

    config = load_config()
    region = config["region"]
    client_id = config["cognito"]["client_id"]
    runtime_url = args.runtime_url or config.get("agentcore_runtime", {}).get("url", "")
    session_api = args.session_api or config.get("session_backend", {}).get("api_url")

    if not runtime_url:
        print("ERROR: AgentCore Runtime URL not found. Pass --runtime-url or add agentcore_runtime.url to config.json")
        sys.exit(1)
    if not session_api:
        print("ERROR: session backend API URL not found in config.json; pass --session-api")
        sys.exit(1)

    creds = {u["actor_id"]: u for u in config.get("users", [])}
    cognito = boto3.client("cognito-idp", region_name=region)

    # Mode is read from SSM (source of truth), not the admin API — the admin
    # API is now group-restricted and policyholder tokens get a 403.
    mode = get_decision_mode(config)

    if mode != "human":
        print(f"WARNING: decision_mode is '{mode}', not 'human'. This seeder expects human mode.")
        if input("Continue anyway? [y/N] ").strip().lower() != "y":
            sys.exit(0)

    scenarios = get_scenarios(args.scenario)
    if not scenarios:
        print(f"No matching scenarios for {args.scenario}")
        sys.exit(1)

    print(f"Seeding {len(scenarios)} scenario(s) against AgentCore Runtime (mode={mode}); session API {session_api}")

    # Cache tokens per actor_id: (id_token, access_token)
    tokens: dict[str, tuple[str, str]] = {}
    results = []
    for i, scn in enumerate(scenarios):
        actor_id = scn["actor_id"]
        try:
            if actor_id not in tokens:
                u = creds.get(actor_id)
                if not u:
                    raise RuntimeError(f"no demo credentials for {actor_id} in config.json")
                tokens[actor_id] = _login(cognito, client_id, u["username"], u["password"])
            id_token, access_token = tokens[actor_id]
            sid = run_scenario(scn, runtime_url, session_api, id_token, access_token, args.max_turns)
            results.append((scn["id"], sid, "ok"))
        except Exception as e:  # noqa: BLE001 - continue seeding remaining scenarios on failure
            print(f"!! scenario {scn['id']} failed: {e}")
            results.append((scn["id"], "-", f"FAILED: {e}"))
        if i < len(scenarios) - 1:
            time.sleep(args.delay)

    print(f"\n{'=' * 70}\nSEEDING SUMMARY\n{'=' * 70}")
    for sid_name, sid, status in results:
        print(f"  {sid_name:28} {sid:28} {status}")


if __name__ == "__main__":
    main()
