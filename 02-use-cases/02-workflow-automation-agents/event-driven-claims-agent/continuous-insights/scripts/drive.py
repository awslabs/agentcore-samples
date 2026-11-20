"""Send claims to the deployed event-driven claims agent.

Each session is one claim sent straight to the AgentCore Runtime (the same payload the sample's
S3 -> EventBridge -> trigger Lambda path builds). The mix covers an approval, a claim that hits the
Cedar limit, an oddly formatted policy number, a vague claim, an unknown policy, and a claim from an email
that is not on the policy. Each claimant email is included so Phase 3 attempts the notification step too.

Every invocation is a real Bedrock call (Claude Sonnet and Haiku) and takes 60 to 90 seconds.

Usage:
    python scripts/drive.py --name ClaimsAgent --count 100 --concurrency 20 [--offset 0] [--yes]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from common import ROOT, load_state, session, validate_name

FENDER_BENDER_CLAIM = (
    "I need to file a claim. My policy is POL-12345. Parking-lot fender bender yesterday, "
    + "rear bumper and tail light, repair quote is 2000 dollars."
)


class Scenario(NamedTuple):
    name: str
    prompt: str
    email: str  # the claimant's email; Phase 3 sends the notification to it


# Emails match the policy holders that the claims agent's seed script creates, as a real claimant's would.
# One scenario writes from an address that is not on the policy, so the identity checks have a case to catch.
SCENARIOS: list[Scenario] = [
    Scenario("approval-fender-bender", FENDER_BENDER_CLAIM, "john.smith@example.com"),
    Scenario(
        "large-house-fire",
        "File a claim for POL-67890. Major house fire, fully documented by the fire department, $150,000 damage.",
        "jane.doe@example.com",
    ),
    Scenario(
        "lowercase-policy-number",
        "my policy is pol-12345, hail dented my hood, repair estimate is $1,800",
        "john.smith@example.com",
    ),
    Scenario(
        "vague-claim",
        "I think something happened to my car. POL-12345. Maybe $30,000, no photos.",
        "john.smith@example.com",
    ),
    Scenario(
        "unknown-policy", "POL-00000, I scratched my car in a parking lot, about $500 of damage.", "driver@example.com"
    ),
    Scenario(
        "approval-storm-damage",
        "Policy POL-11111. A tree branch fell on my roof during last night's storm. The contractor quote is $4,500.",
        "bob.j@example.com",
    ),
    Scenario(
        "email-not-on-policy",
        "File a claim for POL-12345. Someone backed into my parked car, the repair estimate is $3,200.",
        "stranger@example.com",
    ),
]


def run_session(client, runtime_arn: str, scenario: Scenario) -> str:
    session_id = f"ci-{scenario.name}-{uuid.uuid4().hex}"[:64]  # runtime session ids must be 33+ chars
    payload = {"prompt": scenario.prompt, "source": "drive.py", "claimant_email": scenario.email}
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        qualifier="DEFAULT",
        payload=json.dumps(payload).encode("utf-8"),
    )
    response["response"].read()  # the answer streams; read it all so the session finishes
    return session_id


def build_plan(count: int, offset: int = 0) -> list[Scenario]:
    """The first `count` scenarios, cycling through SCENARIOS from index `offset`."""
    return [SCENARIOS[(offset + i) % len(SCENARIOS)] for i in range(count)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument("--count", type=int, default=12, help="number of claims to send")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--offset", type=int, default=0, help="start at this scenario index")
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    args = parser.parse_args()

    state = load_state(validate_name(args.name))
    plan = build_plan(args.count, args.offset)
    print(f"{args.count} claims -> {state['runtime_arn']}")
    print("These make real Bedrock calls and cost money; each takes 60 to 90 seconds.")
    if not args.yes and input("Proceed? Type yes: ").strip().lower() != "yes":
        return 1

    client = session(state["region"]).client(
        "bedrock-agentcore", config=Config(read_timeout=400, retries={"max_attempts": 5, "mode": "adaptive"})
    )
    log_path = Path(ROOT / f"sessions_{args.name}.jsonl")
    ok = failed = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool, log_path.open("a", encoding="utf-8") as log:
        futures = {pool.submit(run_session, client, state["runtime_arn"], sc): sc.name for sc in plan}
        for future in as_completed(futures):
            scenario = futures[future]
            record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "scenario": scenario}
            try:
                record.update(session_id=future.result(), ok=True)
                ok += 1
                print(f"OK   {scenario:26s} {record['session_id']}  ({ok + failed}/{args.count})", flush=True)
            except (ClientError, BotoCoreError) as err:
                record.update(ok=False, error=type(err).__name__)
                failed += 1
                print(f"FAIL {scenario:26s} {type(err).__name__}: {err}", file=sys.stderr, flush=True)
            log.write(json.dumps(record) + "\n")
            log.flush()
    print(f"\n{ok} sent, {failed} failed to invoke")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
