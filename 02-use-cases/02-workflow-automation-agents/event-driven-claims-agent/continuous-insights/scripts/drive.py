"""Send claims to the deployed event-driven claims agent.

The claims come from the AgentCore managed dataset that create_dataset.py creates. Each scenario in the
dataset is one claim: the turn input is the claim text, and the claimant's email is in the scenario
metadata. Each session is one claim sent straight to the AgentCore Runtime (the same payload the sample's
S3 -> EventBridge -> trigger Lambda path builds). The claimant email is included so Phase 3 attempts the
notification step too.

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
from common import ROOT, evaluation_clients, load_state, session, validate_name


class Scenario(NamedTuple):
    name: str
    prompt: str
    email: str  # the claimant's email; Phase 3 sends the notification to it


def to_scenario(example: dict) -> Scenario:
    """One dataset example as a claim. Raises ValueError when the example is not a single-turn claim."""
    turns = example.get("turns") or []
    prompt = turns[0].get("input") if turns else None
    email = (example.get("metadata") or {}).get("claimant_email")
    if not isinstance(prompt, str) or not prompt or not email:
        raise ValueError(
            f"Scenario {example.get('scenario_id')!r} needs one turn with a text input and metadata.claimant_email."
        )
    return Scenario(example["scenario_id"], prompt, email)


def load_scenarios(control, dataset_id: str) -> list[Scenario]:
    """The scenarios in the dataset's draft, in the order set by metadata.order (then by scenario ID)."""
    examples, token = [], None
    while True:
        page = control.list_dataset_examples(datasetId=dataset_id, **({"nextToken": token} if token else {}))
        examples.extend(page.get("examples", []))
        token = page.get("nextToken")
        if not token:
            break
    last = len(examples)  # scenarios without a whole-number metadata.order go after the ordered ones

    def position(example: dict) -> tuple[int, str]:
        order = (example.get("metadata") or {}).get("order")
        return (order if isinstance(order, int) else last, example.get("scenario_id", ""))

    examples.sort(key=position)
    return [to_scenario(example) for example in examples]


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


def build_plan(scenarios: list[Scenario], count: int, offset: int = 0) -> list[Scenario]:
    """The first `count` claims, cycling through `scenarios` from index `offset`."""
    return [scenarios[(offset + i) % len(scenarios)] for i in range(count)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument("--count", type=int, default=12, help="number of claims to send")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--offset", type=int, default=0, help="start at this scenario index")
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    args = parser.parse_args()

    state = load_state(validate_name(args.name))
    if not state.get("dataset_id"):
        sys.exit(f"No dataset recorded. Run: python scripts/create_dataset.py --name {args.name}")
    control, _ = evaluation_clients(state["region"])
    try:
        scenarios = load_scenarios(control, state["dataset_id"])
    except (ClientError, BotoCoreError, ValueError) as err:
        sys.exit(f"Could not read the scenarios from dataset {state['dataset_id']}: {err}")
    if not scenarios:
        sys.exit(f"Dataset {state['dataset_id']} has no scenarios.")
    plan = build_plan(scenarios, args.count, args.offset)
    print(
        f"{args.count} claims from {len(scenarios)} scenarios in dataset {state['dataset_id']} -> {state['runtime_arn']}"
    )
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
