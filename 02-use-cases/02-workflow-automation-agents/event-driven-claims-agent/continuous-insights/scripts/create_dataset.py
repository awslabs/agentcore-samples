"""Create an AgentCore managed dataset that holds the claim scenarios drive.py sends.

The dataset is the single place where the test claims live. Each scenario is one fixed, single-turn
example: the claim text is the turn input, and the claimant's email is in the scenario metadata.
drive.py reads the scenarios from this dataset, so you can add or change claims in the dataset
(console, CLI, or SDK) without editing code.

The script is safe to run again: it reuses the dataset recorded in the state file, or one with the same name.

Usage:
    python scripts/create_dataset.py --name ClaimsAgent
"""

from __future__ import annotations

import argparse
import sys

from botocore.exceptions import BotoCoreError, ClientError
from common import evaluation_clients, load_state, poll, save_state, validate_name

FENDER_BENDER_CLAIM = (
    "I need to file a claim. My policy is POL-12345. Parking-lot fender bender yesterday, "
    + "rear bumper and tail light, repair quote is 2000 dollars."
)

# (scenario id, what it tests, claim text, claimant email).
# Emails match the policy holders that the claims agent's seed script creates, as a real claimant's would.
# One scenario writes from an address that is not on the policy, so the identity checks have a case to catch.
CLAIMS = [
    ("approval-fender-bender", "A clean, low-value claim", FENDER_BENDER_CLAIM, "john.smith@example.com"),
    (
        "large-house-fire",
        "A high-value claim that should go to a human reviewer",
        "File a claim for POL-67890. Major house fire, fully documented by the fire department, $150,000 damage.",
        "jane.doe@example.com",
    ),
    (
        "lowercase-policy-number",
        "A valid claim with the policy number in lowercase",
        "my policy is pol-12345, hail dented my hood, repair estimate is $1,800",
        "john.smith@example.com",
    ),
    (
        "vague-claim",
        "A vague claim with no evidence",
        "I think something happened to my car. POL-12345. Maybe $30,000, no photos.",
        "john.smith@example.com",
    ),
    (
        "unknown-policy",
        "A policy that does not exist",
        "POL-00000, I scratched my car in a parking lot, about $500 of damage.",
        "driver@example.com",
    ),
    (
        "approval-storm-damage",
        "A claim type that the policy does not cover",
        "Policy POL-11111. A tree branch fell on my roof during last night's storm. The contractor quote is $4,500.",
        "bob.j@example.com",
    ),
    (
        "email-not-on-policy",
        "A claimant who is not the policy holder",
        "File a claim for POL-12345. Someone backed into my parked car, the repair estimate is $3,200.",
        "stranger@example.com",
    ),
]


def build_examples() -> list[dict]:
    """The claims as dataset examples (fixed scenarios with one turn each)."""
    return [
        {
            "scenario_id": scenario_id,
            "scenario_description": description,
            "turns": [{"input": prompt}],
            # `order` keeps the scenarios in the same sequence every time drive.py cycles through them.
            "metadata": {"claimant_email": email, "order": order},
        }
        for order, (scenario_id, description, prompt, email) in enumerate(CLAIMS)
    ]


def dataset_name(name: str) -> str:
    return f"{name}_claim_scenarios"[:48]  # dataset names allow up to 48 letters, digits, and underscores


def find_dataset(control, name: str) -> str | None:
    """The ID of the dataset called `name`, or None."""
    token = None
    while True:
        page = control.list_datasets(**({"nextToken": token} if token else {}))
        for item in page.get("datasets", []):
            if item.get("datasetName") == name:
                return item["datasetId"]
        token = page.get("nextToken")
        if not token:
            return None


def wait_until_active(control, dataset_id: str) -> dict:
    def ready() -> dict | None:
        dataset = control.get_dataset(datasetId=dataset_id)
        print(f"  dataset status={dataset['status']}", flush=True)
        return dataset if dataset["status"] in ("ACTIVE", "CREATE_FAILED") else None

    dataset = poll(ready, timeout_s=300, interval_s=3, what="the dataset to become ACTIVE")
    if dataset["status"] != "ACTIVE":
        sys.exit(f"The dataset was not created: {dataset.get('failureReason', dataset['status'])}")
    return dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    name = validate_name(args.name)
    state = load_state(name)
    control, _ = evaluation_clients(state["region"])
    try:
        dataset_id = state.get("dataset_id") or find_dataset(control, dataset_name(name))
        if dataset_id:
            print(f"Using the existing dataset {dataset_id}")
        else:
            dataset_id = control.create_dataset(
                datasetName=dataset_name(name),
                description="Claim scenarios for the continuous insights walkthrough",
                source={"inlineExamples": {"examples": build_examples()}},
            )["datasetId"]
            print(f"Created dataset {dataset_id}")
        dataset = wait_until_active(control, dataset_id)
    except (ClientError, BotoCoreError) as err:
        sys.exit(f"Could not create or read the dataset: {err}")
    state["dataset_id"] = dataset_id
    save_state(name, state)
    print(f"\nDataset {dataset_id} is ACTIVE with {dataset['exampleCount']} scenarios.")
    print(f"Next: python scripts/drive.py --name {name} --count 100 --concurrency 20")
    return 0


if __name__ == "__main__":
    sys.exit(main())
