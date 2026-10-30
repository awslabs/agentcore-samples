"""
Run a single claim scenario against the deployed agent (any mode).

Same self-play approach as 2_autoseed.py but mode-agnostic. Talks to the
deployed AgentCore Runtime (authenticated via Cognito access token).

Usage:
    python hydration/run_claim.py
    python hydration/run_claim.py --actor PH-1001 --name "Bob Thompson" --opening "..." --facts "..."
"""

import argparse
import json
import os
import sys

import boto3
import requests
from strands import Agent

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", "src"))

from memory.config import get_decision_mode, load_config

MODEL_ID = "global.anthropic.claude-sonnet-4-6"

_DONE_MARKERS = (
    "approved",
    "denied",
    "escalated",
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

DATE HANDLING: If the assistant questions or corrects a date you mentioned, do
NOT argue. Simply accept their correction and move on. For example: "You're
right, let me correct that — [use whatever date they suggest]."

CLAIM FACTS:
{facts}

You have ALREADY sent this opening message:
"{opening}"

The assistant will now ask follow-up questions. Answer them as {name}.
"""


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
    resp = requests.post(
        f"{session_api}/sessions",
        headers={"Authorization": f"Bearer {id_token}", "Content-Type": "application/json"},
        json={"session_title": title},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["session"]


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
            # AgentCore format: nested contentBlockDelta
            delta = parsed.get("event", {}).get("contentBlockDelta", {}).get("delta", {}).get("text")
            if delta:
                event = json.loads(delta)
                if event.get("event") == "message":
                    message = event.get("data", "")
                elif event.get("event") == "error":
                    raise RuntimeError(f"agent error: {event.get('data')}")
                continue
            # Flask SSE format (fallback for local dev)
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


def main():
    parser = argparse.ArgumentParser(description="Run a single claim against the deployed agent")
    parser.add_argument("--scenario", default=None, help="Scenario ID from scenarios.py (e.g. david-kitchen-fire)")
    parser.add_argument("--actor", default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--opening", default=None)
    parser.add_argument("--facts", default=None)
    parser.add_argument("--runtime-url", default=None, help="Override AgentCore Runtime URL")
    args = parser.parse_args()

    # Load from scenario if specified
    if args.scenario:
        from scenarios import get_scenarios

        matches = get_scenarios([args.scenario])
        if not matches:
            print(f"ERROR: scenario '{args.scenario}' not found")
            sys.exit(1)
        scn = matches[0]
        opening = args.opening or scn["opening"]
        facts = args.facts or scn["facts"]
        actor = args.actor or scn["actor_id"]
        name = args.name or scn["name"]
    else:
        actor = args.actor or "PH-1001"
        name = args.name or "Bob Thompson"
        opening = args.opening or (
            "Hi, I need to file a claim. A pipe burst behind my bathroom wall upstairs "
            "and caused water damage to the hallway and bathroom. My policy is HO-2024-1001."
        )
        facts = (
            args.facts
            or """\
- Incident: pipe burst behind the upstairs bathroom wall, flooding the bathroom and hallway.
- Date: June 10, 2026. Discovered June 12 (noticed wet carpet). Filing today (~12-day delay).
- Damage: warped hardwood in hallway, soaked drywall behind the toilet, bathroom floor tiles cracking.
- No mold yet but worried about it developing.
- Estimated cost: about $11,000 (plumber, drywall, flooring, repaint).
- Plumber fixed the pipe on June 13. Have the invoice ($450).
- Photos: yes, taken before the plumber fixed it.
- Policy: HO-2024-1001
- Contact: Bob Thompson, (555) 014-1001, 142 Maple Street, Springfield, IL 62704.
"""
        )

    config = load_config()
    region = config["region"]
    client_id = config["cognito"]["client_id"]
    session_api = config.get("session_backend", {}).get("api_url")
    runtime_url = args.runtime_url or config.get("agentcore_runtime", {}).get("url", "")

    if not runtime_url:
        print("ERROR: AgentCore Runtime URL not found. Pass --runtime-url or add agentcore_runtime.url to config.json")
        sys.exit(1)

    # Login
    creds = {u["actor_id"]: u for u in config.get("users", [])}
    u = creds[actor]
    cognito = boto3.client("cognito-idp", region_name=region)
    id_token, access_token = _login(cognito, client_id, u["username"], u["password"])

    # Check mode from SSM (source of truth)
    mode = get_decision_mode(config)
    print(f"Running claim against AgentCore Runtime (mode={mode})")

    # Create session
    scenario_label = args.scenario or "manual"
    session = _create_session(session_api, id_token, f"[Auto] {scenario_label}")
    session_id = session["session_id"]
    actor_id = session.get("actor_id") or actor

    print(f"Actor: {actor_id} | Session: {session_id}\n")

    simulant = Agent(
        model=MODEL_ID,
        system_prompt=SIMULANT_SYSTEM.format(name=name, facts=facts, opening=opening),
    )

    customer_msg = opening
    print(f"[{name}] {customer_msg}")

    _GOODBYE_MARKERS = ("bye", "goodbye", "take care", "have a good")

    for turn in range(1, args.max_turns + 1):
        agent_text = call_agent(customer_msg, actor_id, session_id, runtime_url, access_token)
        print(f"\n[Agent] {agent_text}\n")

        if is_done(agent_text):
            print(f"--- done (turn {turn}) ---")
            return

        if turn == args.max_turns:
            print(f"--- max turns ({args.max_turns}) ---")
            return

        customer_msg = str(simulant(agent_text)).strip()
        print(f"[{name}] {customer_msg}")

        # Stop if simulant is saying goodbye
        if any(m in customer_msg.lower() for m in _GOODBYE_MARKERS):
            print(f"--- done (turn {turn}) ---")
            return


if __name__ == "__main__":
    main()
