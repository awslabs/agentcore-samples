"""
Dataset Management with Amazon Bedrock AgentCore.

Demonstrates all key capabilities of the DatasetClient:

  Part A — PREDEFINED dataset lifecycle
      1.  Create a PREDEFINED dataset from inline examples
      2.  Get dataset metadata (including presigned download URL)
      3.  List all datasets in the account (paginated)
      4.  Update dataset metadata (description, tags)
      5.  Add new examples to the DRAFT
      6.  List DRAFT examples (paginated)
      7.  Update existing examples in the DRAFT
      8.  Delete specific examples from the DRAFT
      9.  Publish the DRAFT as an immutable version
     10.  List published versions
     11.  Read examples from a specific published version
     12.  Delete a specific published version

  Part B — SIMULATED dataset lifecycle
     13.  Create a SIMULATED dataset with actor-profile scenarios
     14.  List and inspect simulated examples
     15.  Update an actor-profile example
     16.  Publish and clean up

  Part C — Using a managed dataset with AgentCore evaluations
     17.  Load examples from a published version
     18.  Build a Dataset object from managed dataset examples
     19.  Run EvaluationClient using ground truth from the managed dataset

  Part D — Cleanup
     20.  Delete all datasets created by this script

Usage:
    python manage_datasets.py [--region REGION] [--config PATH] [--skip-eval]

Args:
    --region     AWS region (default: from boto3 session, fallback us-east-1)
    --config     Path to agent_config.json written by deploy.py
                 (default: ../../utils/agent_config.json)
                 Required for Part C (evaluation integration). Skipped if absent
                 or --skip-eval is passed.
    --skip-eval  Skip Part C (evaluation). Useful for testing dataset APIs without
                 a deployed agent.

Prerequisites:
    pip install -r requirements.txt
"""

import argparse
import json
import time
import uuid
from datetime import timedelta
from pathlib import Path

import boto3
from boto3.session import Session

# ============================================================
# 0. Parse args
# ============================================================

_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_CONFIG = _SCRIPT_DIR / ".." / ".." / "utils" / "agent_config.json"

parser = argparse.ArgumentParser(description="Dataset Management demo")
parser.add_argument("--region", default=None, help="AWS region")
parser.add_argument(
    "--config",
    default=str(_DEFAULT_CONFIG),
    help="Path to agent_config.json (for Part C evaluation integration)",
)
parser.add_argument(
    "--skip-eval",
    action="store_true",
    help="Skip Part C — evaluation integration",
)
args = parser.parse_args()

REGION = args.region or Session().region_name or "us-east-1"

# Load agent config for evaluation integration (Part C).
# AGENT_REGION may differ from REGION — the dataset service and the agent runtime
# can live in different regions. Dataset operations always use REGION; agent
# invocation and EvaluationClient in Part C always use AGENT_REGION.
_config_path = Path(args.config)
_run_eval = not args.skip_eval and _config_path.exists()
AGENT_ID = AGENT_ARN = CW_LOG_GROUP = AGENT_REGION = None
if _run_eval:
    _cfg = json.loads(_config_path.read_text())
    AGENT_ID = _cfg["agent_id"]
    AGENT_ARN = _cfg["agent_arn"]
    CW_LOG_GROUP = _cfg["cw_log_group"]
    AGENT_REGION = _cfg.get("region") or REGION
elif not args.skip_eval:
    print(
        f"  Note: agent_config.json not found at {_config_path}. "
        "Part C (evaluation integration) will be skipped. "
        "Run `cd ../utils && python deploy.py` first, or pass --skip-eval."
    )

print("=" * 60)
print("AgentCore Dataset Management Demo")
print("=" * 60)
print(f"  Dataset region : {REGION}")
print(f"  Agent region   : {AGENT_REGION or '(none)'}")
print(f"  Part C         : {'enabled' if _run_eval else 'skipped (no agent config)'}")

from bedrock_agentcore.evaluation import DatasetClient  # noqa: E402

client = DatasetClient(region_name=REGION)

# Track dataset IDs for cleanup
_created_datasets: list[str] = []

# Unique suffix to avoid naming conflicts across runs
_SUFFIX = f"dm_{int(time.time())}"

# ============================================================
# Part A — PREDEFINED dataset lifecycle
# ============================================================

print("\n" + "=" * 60)
print("Part A — PREDEFINED dataset lifecycle")
print("=" * 60)

# ── Step 1: Create a PREDEFINED dataset from inline examples ──────────────────
#
# schemaType="AGENTCORE_EVALUATION_PREDEFINED_V1" is for datasets where you
# know the exact expected responses and tool trajectories ahead of time.
# Required fields per example: scenario_id, turns (each with input).
# Optional: expected_trajectory (toolNames list), assertions, metadata.
#
# source.inlineExamples.examples — provide up to 5 MB of examples inline.
# For larger datasets, upload to S3 first and use source.s3 instead.

print("\n[A1] Creating PREDEFINED dataset ...")
ds_name_predefined = f"hr_eval_predefined_{_SUFFIX}"
predefined_ds = client.create_dataset_and_wait(
    datasetName=ds_name_predefined,
    schemaType="AGENTCORE_EVALUATION_PREDEFINED_V1",
    source={
        "inlineExamples": {
            "examples": [
                {
                    "scenario_id": "pto-balance-check",
                    "turns": [
                        {
                            "input": "What is the current PTO balance for employee EMP-001?",
                            "expected_response": "Employee EMP-001 has 10 remaining PTO days out of 15 total.",
                        }
                    ],
                    "expected_trajectory": {"toolNames": ["get_pto_balance"]},
                    "assertions": [
                        {"text": "Agent called get_pto_balance for EMP-001"},
                        {"text": "Agent reported 10 remaining PTO days"},
                    ],
                },
                {
                    "scenario_id": "pto-policy-lookup",
                    "turns": [
                        {
                            "input": "What is the company PTO policy?",
                            "expected_response": (
                                "Full-time employees accrue 15 days of PTO per year. "
                                "Requests must be submitted at least 2 business days in advance."
                            ),
                        }
                    ],
                    "expected_trajectory": {"toolNames": ["lookup_hr_policy"]},
                    "assertions": [
                        {"text": "Agent called lookup_hr_policy with topic=pto"},
                        {"text": "Agent mentioned 15 days annual accrual"},
                    ],
                },
                {
                    "scenario_id": "401k-info",
                    "turns": [
                        {
                            "input": "How does the 401k match work?",
                            "expected_response": (
                                "The company matches 100% of contributions up to 4% of salary, "
                                "plus 50% on the next 2%, for a total effective match of up to 5%."
                            ),
                        }
                    ],
                    "expected_trajectory": {"toolNames": ["get_benefits_summary"]},
                    "assertions": [
                        {"text": "Agent called get_benefits_summary with benefit_type=401k"},
                        {"text": "Agent correctly described the 4% full match and 50% on next 2%"},
                    ],
                },
            ]
        }
    },
)
predefined_ds_id = predefined_ds["datasetId"]
_created_datasets.append(predefined_ds_id)
print(f"  datasetId    : {predefined_ds_id}")
print(f"  status       : {predefined_ds['status']}")
print(f"  exampleCount : {predefined_ds.get('exampleCount', '?')}")
print(f"  draftStatus  : {predefined_ds.get('draftStatus', '?')}")

# ── Step 2: Get dataset metadata ──────────────────────────────────────────────
#
# get_dataset returns metadata plus a presigned download URL (TTL: 5 min) for
# the DRAFT's dataset.jsonl file.
# Pass datasetVersion="1" (or any version number) to get a specific version.

print("\n[A2] Getting dataset metadata ...")
got = client.get_dataset(datasetId=predefined_ds_id)
print(f"  datasetName          : {got['datasetName']}")
print(f"  schemaType           : {got['schemaType']}")
print(f"  status               : {got['status']}")
print(f"  exampleCount (DRAFT) : {got.get('exampleCount', '?')}")
if "downloadUrl" in got:
    print("  downloadUrl          : <presigned URL — use to download dataset.jsonl>")
    print(f"  downloadUrlExpiresAt : {got.get('downloadUrlExpiresAt', '?')}")

# ── Step 3: List all datasets in the account ──────────────────────────────────
#
# list_datasets supports pagination via maxResults + nextToken.
# DatasetSummary includes: datasetArn, datasetId, datasetName, status, exampleCount.

print("\n[A3] Listing datasets (first page, max 5) ...")
list_resp = client.list_datasets(maxResults=5)
datasets_page = list_resp.get("datasets", [])
print(f"  Found {len(datasets_page)} dataset(s) on this page")
for d in datasets_page:
    print(
        f"    {d.get('datasetName', '?'):<40} status={d.get('status', '?'):<12} examples={d.get('exampleCount', '?')}"
    )
if list_resp.get("nextToken"):
    print("  (more pages available via nextToken)")

# ── Step 4: Update dataset metadata ──────────────────────────────────────────
#
# Only description and tags can be updated. datasetName, schemaType, and
# kmsKeyArn are immutable after creation. This is a synchronous operation.

print("\n[A4] Updating dataset metadata ...")
client.update_dataset(
    datasetId=predefined_ds_id,
    description="HR evaluation dataset — PTO, policy, and benefits scenarios",
)
got = client.get_dataset(datasetId=predefined_ds_id)
print(f"  description updated to: {got.get('description', '?')!r}")

# ── Step 5: Add new examples to the DRAFT ────────────────────────────────────
#
# add_examples_and_wait appends examples to the existing DRAFT.
# It waits until the async mutation finishes (status: UPDATING → ACTIVE).
# Each example gets an auto-generated exampleId (returned in addedCount).
# All-or-nothing: if any example fails schema validation, none are added.

print("\n[A5] Adding new examples to the DRAFT ...")
add_resp = client.add_examples_and_wait(
    datasetId=predefined_ds_id,
    source={
        "inlineExamples": {
            "examples": [
                {
                    "scenario_id": "submit-pto-request",
                    "turns": [
                        {
                            "input": "Please submit a PTO request for EMP-001 from 2026-04-14 to 2026-04-16.",
                            "expected_response": "PTO request submitted and approved for EMP-001.",
                        }
                    ],
                    "expected_trajectory": {"toolNames": ["submit_pto_request"]},
                    "assertions": [
                        {"text": "Agent called submit_pto_request for EMP-001"},
                        {"text": "Agent confirmed the request was approved"},
                    ],
                },
                {
                    "scenario_id": "pay-stub-lookup",
                    "turns": [
                        {
                            "input": "Can you pull up the January 2026 pay stub for EMP-001?",
                            "expected_response": "EMP-001 January 2026: gross pay $8,333.33, net pay $5,362.50.",
                        }
                    ],
                    "expected_trajectory": {"toolNames": ["get_pay_stub"]},
                    "assertions": [
                        {"text": "Agent called get_pay_stub for EMP-001 period 2026-01"},
                        {"text": "Agent reported gross pay $8,333.33"},
                    ],
                },
            ]
        }
    },
)
print(f"  status       : {add_resp.get('status', '?')}")
got = client.get_dataset(datasetId=predefined_ds_id)
print(f"  exampleCount : {got.get('exampleCount', '?')} (DRAFT — after adding 2 examples)")

# ── Step 6: List DRAFT examples ───────────────────────────────────────────────
#
# list_dataset_examples returns the full content of each example with pagination.
# Default: reads the DRAFT. Pass datasetVersion="N" for a specific version.
# datasetVersion="DRAFT" is equivalent to the default.

print("\n[A6] Listing DRAFT examples ...")
examples_resp = client.list_dataset_examples(datasetId=predefined_ds_id)
examples = examples_resp.get("examples", [])
print(f"  datasetVersion : {examples_resp.get('datasetVersion', '?')}")
print(f"  Total examples : {len(examples)}")
for ex in examples:
    turns = ex.get("turns", [])
    first_input = turns[0]["input"][:60] if turns else "?"
    print(f"    [{ex.get('exampleId', '?')[:8]}...] {ex.get('scenario_id', '?'):<30} input={first_input!r}")

# Capture the exampleId for the first example (needed for update/delete)
first_example_id = examples[0]["exampleId"] if examples else None

# ── Step 7: Update existing examples in the DRAFT ────────────────────────────
#
# update_examples_and_wait modifies existing examples in-place.
# exampleId is required in each entry (obtained from list_dataset_examples).
# All-or-nothing: if any exampleId is not found, the whole batch is rejected.
# exampleCount is unchanged — only the content is updated.

if first_example_id:
    print("\n[A7] Updating the first example (adding a metadata tag) ...")
    update_resp = client.update_examples_and_wait(
        datasetId=predefined_ds_id,
        examples=[
            {
                "exampleId": first_example_id,
                "scenario_id": "pto-balance-check",
                "turns": [
                    {
                        "input": "What is the current PTO balance for employee EMP-001?",
                        "expected_response": "Employee EMP-001 has 10 remaining PTO days out of 15 total (5 days used).",
                    }
                ],
                "expected_trajectory": {"toolNames": ["get_pto_balance"]},
                "assertions": [
                    {"text": "Agent called get_pto_balance with employee_id=EMP-001"},
                    {"text": "Agent reported 10 remaining PTO days"},
                    {"text": "Agent mentioned 15 total days and 5 used"},
                ],
                "metadata": {"difficulty": "easy", "category": "pto"},
            }
        ],
    )
    print(f"  status : {update_resp.get('status', '?')}")
    # Verify the update by reading the example back
    updated_examples_resp = client.list_dataset_examples(datasetId=predefined_ds_id)
    updated_ex = next(
        (e for e in updated_examples_resp.get("examples", []) if e.get("exampleId") == first_example_id),
        None,
    )
    if updated_ex:
        print(f"  Updated assertions count : {len(updated_ex.get('assertions', []))}")
        print(f"  metadata                 : {updated_ex.get('metadata', {})}")

# ── Step 8: Delete specific examples from the DRAFT ─────────────────────────
#
# delete_examples_and_wait removes examples by exampleId.
# All-or-nothing: if any exampleId is not found, nothing is deleted.
# exampleCount decreases by the number of deleted examples.

print("\n[A8] Deleting the last example from the DRAFT ...")
all_examples = client.list_dataset_examples(datasetId=predefined_ds_id).get("examples", [])
last_example_id = all_examples[-1]["exampleId"] if all_examples else None
if last_example_id:
    del_resp = client.delete_examples_and_wait(
        datasetId=predefined_ds_id,
        exampleIds=[last_example_id],
    )
    print(f"  status       : {del_resp.get('status', '?')}")
    got = client.get_dataset(datasetId=predefined_ds_id)
    print(f"  exampleCount : {got.get('exampleCount', '?')} (after deleting 1 example)")

# ── Step 9: Publish the DRAFT as an immutable version ────────────────────────
#
# create_dataset_version_and_wait copies the current DRAFT to a numbered
# immutable version (1, 2, 3...). The DRAFT is preserved unchanged after publish.
#
# Use published versions for:
#   - CI/CD pipelines (pin to a known-good version)
#   - Sharing datasets with teammates who need a stable snapshot
#   - Pre/post comparisons (run evaluations against version N, optimize, re-run)

print("\n[A9] Publishing DRAFT as version 1 ...")
version_resp = client.create_dataset_version_and_wait(datasetId=predefined_ds_id)
print(f"  status      : {version_resp.get('status', '?')}")
print(f"  draftStatus : {version_resp.get('draftStatus', '?')} (UNMODIFIED = DRAFT matches version 1)")

# ── Step 10: List published versions ─────────────────────────────────────────
#
# list_dataset_versions returns all published versions (newest first).
# DRAFT is not included — it is not a versioned snapshot.

print("\n[A10] Listing published versions ...")
versions_resp = client.list_dataset_versions(datasetId=predefined_ds_id)
versions = versions_resp.get("versions", [])
print(f"  Published versions: {len(versions)}")
for v in versions:
    print(
        f"    version={v.get('datasetVersion', '?')}  "
        f"exampleCount={v.get('exampleCount', '?')}  "
        f"createdAt={v.get('createdAt', '?')}"
    )

# ── Step 11: Read examples from a specific published version ──────────────────
#
# Pass datasetVersion="1" to list_dataset_examples to read version 1.
# The version's dataset.jsonl is immutable — it will never change.

print("\n[A11] Reading examples from published version 1 ...")
v1_examples_resp = client.list_dataset_examples(
    datasetId=predefined_ds_id,
    datasetVersion="1",
)
v1_examples = v1_examples_resp.get("examples", [])
print(f"  datasetVersion : {v1_examples_resp.get('datasetVersion', '?')}")
print(f"  Total examples : {len(v1_examples)}")
for ex in v1_examples:
    print(f"    scenario_id={ex.get('scenario_id', '?'):<35} assertions={len(ex.get('assertions', []))}")

# Also get_dataset with datasetVersion="1" returns that version's metadata and download URL
got_v1 = client.get_dataset(datasetId=predefined_ds_id, datasetVersion="1")
print(f"  Version 1 exampleCount : {got_v1.get('exampleCount', '?')}")
if "downloadUrl" in got_v1:
    print("  Version 1 download URL : <presigned URL for versions/1/dataset.jsonl>")

# Add more examples to the DRAFT (version 1 stays unchanged)
print("\n  Adding example to DRAFT after publishing v1 (v1 stays unchanged) ...")
client.add_examples_and_wait(
    datasetId=predefined_ds_id,
    source={
        "inlineExamples": {
            "examples": [
                {
                    "scenario_id": "benefits-health",
                    "turns": [
                        {
                            "input": "Can you walk me through the health insurance options?",
                            "expected_response": "The company covers 90% of premiums for employee-only coverage.",
                        }
                    ],
                    "expected_trajectory": {"toolNames": ["get_benefits_summary"]},
                    "assertions": [{"text": "Agent called get_benefits_summary with benefit_type=health"}],
                }
            ]
        }
    },
)
got_draft = client.get_dataset(datasetId=predefined_ds_id)
got_v1_after = client.get_dataset(datasetId=predefined_ds_id, datasetVersion="1")
print(f"  DRAFT exampleCount      : {got_draft.get('exampleCount', '?')} (increased)")
print(f"  Version 1 exampleCount  : {got_v1_after.get('exampleCount', '?')} (unchanged)")
print(f"  draftStatus             : {got_draft.get('draftStatus', '?')} (MODIFIED = DRAFT ahead of v1)")

# Publish version 2 for the updated DRAFT
print("\n  Publishing DRAFT as version 2 ...")
client.create_dataset_version_and_wait(datasetId=predefined_ds_id)
versions_resp2 = client.list_dataset_versions(datasetId=predefined_ds_id)
print(f"  Total published versions: {len(versions_resp2.get('versions', []))}")

# ── Step 12: Delete a specific published version ──────────────────────────────
#
# Use delete_dataset_version (via the underlying CP client) to remove a single
# published version. This is an async operation: it transitions the dataset to
# UPDATING then back to ACTIVE. The DRAFT and other versions are unaffected.
# Note: delete_dataset_and_wait is for full-dataset deletion — do not pass
# datasetVersion to it, as it polls for the dataset to disappear entirely.

print("\n[A12] Deleting published version 1 ...")
# Call delete_dataset with datasetVersion to remove only that version.
# We do NOT use delete_dataset_and_wait here because that helper polls for the
# entire dataset to disappear — but version-specific deletion only removes one
# version and leaves the dataset record intact.
client._cp_client.delete_dataset(datasetId=predefined_ds_id, datasetVersion="1")
# Poll list_dataset_versions until version 1 is gone (async operation)
for _ in range(30):
    time.sleep(5)
    _remaining = client.list_dataset_versions(datasetId=predefined_ds_id).get("versions", [])
    if not any(v.get("datasetVersion") == "1" for v in _remaining):
        break
versions_after = client.list_dataset_versions(datasetId=predefined_ds_id)
print(f"  Published versions remaining: {len(versions_after.get('versions', []))}")
for _v in versions_after.get("versions", []):
    print(f"    version={_v.get('datasetVersion', '?')}  exampleCount={_v.get('exampleCount', '?')}")

# ============================================================
# Part B — SIMULATED dataset lifecycle
# ============================================================

print("\n" + "=" * 60)
print("Part B — SIMULATED dataset lifecycle")
print("=" * 60)

# ── Step 13: Create a SIMULATED dataset ──────────────────────────────────────
#
# schemaType="AGENTCORE_EVALUATION_SIMULATED_V1" is for datasets where you use
# an LLM actor to drive multi-turn conversations. The actor receives the
# actor_profile and drives the conversation toward the goal.
#
# Required fields: scenario_id, actor_profile (with context and goal), input.
# Optional: scenario_description, max_turns (>= 1), assertions, metadata.

print("\n[B13] Creating SIMULATED dataset ...")
ds_name_simulated = f"hr_eval_simulated_{_SUFFIX}"
simulated_ds = client.create_dataset_and_wait(
    datasetName=ds_name_simulated,
    schemaType="AGENTCORE_EVALUATION_SIMULATED_V1",
    source={
        "inlineExamples": {
            "examples": [
                {
                    "scenario_id": "frustrated-refund-customer",
                    "scenario_description": "Impatient customer seeks refund for cancelled flight",
                    "actor_profile": {
                        "traits": {"personality": "impatient", "communication_style": "direct"},
                        "context": "Has been waiting 3 days for a refund on cancelled flight BK-98765",
                        "goal": "Get a full cash refund for cancelled flight BK-98765",
                    },
                    "input": "I want my money back for flight BK-98765 that was cancelled!",
                    "max_turns": 5,
                    "assertions": [
                        {"text": "Agent acknowledged the frustration and apologized"},
                        {"text": "Agent initiated the refund process for BK-98765"},
                        {"text": "Agent provided a refund confirmation or timeline"},
                    ],
                },
                {
                    "scenario_id": "new-account-setup",
                    "scenario_description": "New employee setting up HR accounts on first day",
                    "actor_profile": {
                        "traits": {"personality": "eager", "detail_oriented": True},
                        "context": "First day at the company, needs to set up PTO tracking and review benefits",
                        "goal": "Understand PTO policy and enroll in health insurance",
                    },
                    "input": "Hi, I just started today. Can you help me understand my benefits and PTO?",
                    "max_turns": 8,
                    "assertions": [
                        {"text": "Agent explained PTO accrual for new employees"},
                        {"text": "Agent covered health insurance enrollment options"},
                    ],
                },
                {
                    "scenario_id": "pay-discrepancy",
                    "scenario_description": "Employee questioning unexpected deduction in paycheck",
                    "actor_profile": {
                        "traits": {"personality": "concerned", "methodical": True},
                        "context": "Noticed a $500 deduction on last paycheck that was not expected",
                        "goal": "Understand the $500 deduction on paycheck for pay period 2026-04",
                    },
                    "input": "I have a question about my latest paycheck. There's a $500 deduction I don't recognize.",
                    "max_turns": 6,
                    "assertions": [
                        {"text": "Agent retrieved the pay stub for the relevant period"},
                        {"text": "Agent explained the deduction line item"},
                        {"text": "Agent offered to escalate if the deduction was incorrect"},
                    ],
                },
            ]
        }
    },
)
simulated_ds_id = simulated_ds["datasetId"]
_created_datasets.append(simulated_ds_id)
print(f"  datasetId    : {simulated_ds_id}")
print(f"  status       : {simulated_ds['status']}")
print(f"  exampleCount : {simulated_ds.get('exampleCount', '?')}")

# ── Step 14: List and inspect simulated examples ─────────────────────────────

print("\n[B14] Listing simulated examples ...")
sim_examples_resp = client.list_dataset_examples(datasetId=simulated_ds_id)
sim_examples = sim_examples_resp.get("examples", [])
print(f"  Total examples : {len(sim_examples)}")
for ex in sim_examples:
    profile = ex.get("actor_profile", {})
    print(
        f"    [{ex.get('exampleId', '?')[:8]}...] "
        f"scenario_id={ex.get('scenario_id', '?'):<30} "
        f"max_turns={ex.get('max_turns', '?'):<3} "
        f"goal={profile.get('goal', '?')[:50]!r}"
    )

# Capture the first example ID
sim_first_id = sim_examples[0]["exampleId"] if sim_examples else None

# ── Step 15: Update an actor-profile example ─────────────────────────────────

if sim_first_id:
    print("\n[B15] Updating actor-profile scenario (extending max_turns) ...")
    sim_update_resp = client.update_examples_and_wait(
        datasetId=simulated_ds_id,
        examples=[
            {
                "exampleId": sim_first_id,
                "scenario_id": "frustrated-refund-customer",
                "scenario_description": "Impatient customer seeks refund for cancelled flight (extended)",
                "actor_profile": {
                    "traits": {"personality": "impatient", "communication_style": "direct"},
                    "context": "Has been waiting 3 days for a refund on cancelled flight BK-98765",
                    "goal": "Get a full cash refund for cancelled flight BK-98765",
                },
                "input": "I want my money back for flight BK-98765 that was cancelled!",
                "max_turns": 8,  # extended from 5 to 8
                "assertions": [
                    {"text": "Agent acknowledged the frustration and apologized"},
                    {"text": "Agent initiated the refund process for BK-98765"},
                    {"text": "Agent provided a refund confirmation or timeline"},
                ],
            }
        ],
    )
    print(f"  status : {sim_update_resp.get('status', '?')}")
    # Verify update
    sim_updated_resp = client.list_dataset_examples(datasetId=simulated_ds_id)
    sim_updated_ex = next(
        (e for e in sim_updated_resp.get("examples", []) if e.get("exampleId") == sim_first_id),
        None,
    )
    if sim_updated_ex:
        print(f"  max_turns updated to : {sim_updated_ex.get('max_turns', '?')}")

# ── Step 16: Publish and inspect the simulated dataset ───────────────────────

print("\n[B16] Publishing SIMULATED dataset as version 1 ...")
client.create_dataset_version_and_wait(datasetId=simulated_ds_id)
sim_versions = client.list_dataset_versions(datasetId=simulated_ds_id)
print(f"  Published versions : {len(sim_versions.get('versions', []))}")
sim_v1 = client.get_dataset(datasetId=simulated_ds_id, datasetVersion="1")
print(f"  Version 1 exampleCount : {sim_v1.get('exampleCount', '?')}")

# ============================================================
# Part C — Using a managed dataset with AgentCore evaluations
# ============================================================

if _run_eval:
    print("\n" + "=" * 60)
    print("Part C — Using a managed dataset with AgentCore evaluations")
    print("=" * 60)

    # Agent invocation and EvaluationClient use AGENT_REGION (from agent_config.json).
    # DatasetClient uses REGION (passed via --region or default session region).
    # These can differ — e.g. dataset service in us-west-2, agent in us-east-1.
    agentcore_client = boto3.client("bedrock-agentcore", region_name=AGENT_REGION)

    # ── Step 17: Load examples from published version 1 ──────────────────────
    #
    # Load the pinned version to ensure reproducible evaluation results.
    # This guarantees the evaluation always runs against the same approved set,
    # regardless of any subsequent DRAFT changes.

    print("\n[C17] Loading examples from managed dataset version 2 ...")
    _managed_examples_resp = client.list_dataset_examples(
        datasetId=predefined_ds_id,
        datasetVersion="2",
    )
    _managed_examples = _managed_examples_resp.get("examples", [])
    print(f"  Loaded {len(_managed_examples)} examples from version 2")

    # ── Step 18: Build a Dataset object from managed dataset examples ─────────
    #
    # Convert the PREDEFINED_V1 examples into the Dataset/PredefinedScenario
    # format required by OnDemandEvaluationDatasetRunner and EvaluationClient.
    # This is the bridge between the managed dataset service and the evaluation SDK.

    print("\n[C18] Building Dataset object from managed examples ...")

    from bedrock_agentcore.evaluation import (  # noqa: E402
        Dataset,
        PredefinedScenario,
        Turn,
    )

    def _build_dataset_from_managed(examples: list) -> Dataset:
        """Convert managed PREDEFINED_V1 examples into a Dataset object."""
        scenarios = []
        for ex in examples:
            turns = [
                Turn(
                    input=t["input"],
                    expected_response=t.get("expected_response"),
                )
                for t in ex.get("turns", [])
            ]
            traj_obj = ex.get("expected_trajectory", {})
            trajectory = traj_obj.get("toolNames", []) if isinstance(traj_obj, dict) else []
            assertions_raw = ex.get("assertions", [])
            assertions = [a["text"] for a in assertions_raw if isinstance(a, dict)]
            scenarios.append(
                PredefinedScenario(
                    scenario_id=ex["scenario_id"],
                    turns=turns,
                    expected_trajectory=trajectory or None,
                    assertions=assertions or None,
                )
            )
        return Dataset(scenarios=scenarios)

    _eval_dataset = _build_dataset_from_managed(_managed_examples)
    print(f"  Dataset scenarios : {len(_eval_dataset.scenarios)}")
    for sc in _eval_dataset.scenarios:
        print(f"    scenario_id={sc.scenario_id:<35} turns={len(sc.turns)}  assertions={len(sc.assertions or [])}")

    # ── Step 19: Run EvaluationClient using ground truth from managed dataset ──
    #
    # We invoke the agent once and evaluate using the expected_response and
    # assertions loaded from the managed dataset version. This demonstrates that
    # managed datasets are a single source of truth for both agent invocation
    # and evaluation ground truth.

    print("\n[C19] Invoking agent and evaluating with managed dataset ground truth ...")

    from bedrock_agentcore.evaluation import EvaluationClient, ReferenceInputs  # noqa: E402

    def _invoke_agent(prompt: str, session_id: str) -> str:
        resp = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_ARN,
            qualifier="DEFAULT",
            runtimeSessionId=session_id,
            payload=json.dumps({"prompt": prompt}).encode("utf-8"),
        )
        raw = resp["response"].read().decode("utf-8")
        parts = []
        for line in raw.splitlines():
            if line.startswith("data: "):
                chunk = line[len("data: ") :]
                try:
                    chunk = json.loads(chunk)
                except Exception:
                    pass
                parts.append(str(chunk))
        return "".join(parts) if parts else raw

    eval_client = EvaluationClient(region_name=AGENT_REGION)
    _all_results = {}

    for scenario in _eval_dataset.scenarios:
        if not scenario.turns:
            continue

        print(f"\n  Evaluating scenario: {scenario.scenario_id}")
        _preview_session_id = str(uuid.uuid4())  # 36 chars — satisfies min-length
        for turn in scenario.turns:
            response = _invoke_agent(turn.input, _preview_session_id)
            print(f"    > {turn.input[:70]}")
            print(f"    < {response[:100]}")

    print("\n  Waiting 60s for CloudWatch log ingestion ...")
    time.sleep(60)

    # Now evaluate each session against the managed dataset's ground truth.
    # Use str(uuid.uuid4()) for session IDs — runtimeSessionId requires min 33 chars.
    _session_ids: dict[str, str] = {}
    for scenario in _eval_dataset.scenarios:
        if not scenario.turns:
            continue
        _session_id = str(uuid.uuid4())  # 36 chars, satisfies min-length constraint
        for turn in scenario.turns:
            _invoke_agent(turn.input, _session_id)
        _session_ids[scenario.scenario_id] = _session_id

    print("\n  Waiting 60s for CloudWatch ingestion ...")
    time.sleep(60)

    for scenario in _eval_dataset.scenarios:
        sid = _session_ids.get(scenario.scenario_id)
        if not sid:
            continue
        _ref = ReferenceInputs(
            expected_response=scenario.turns[0].expected_response if scenario.turns else None,
            assertions=scenario.assertions,
        )
        try:
            results = eval_client.run(
                evaluator_ids=["Builtin.Correctness", "Builtin.GoalSuccessRate"],
                session_id=sid,
                agent_id=AGENT_ID,
                look_back_time=timedelta(hours=1),
                reference_inputs=_ref,
            )
            _all_results[scenario.scenario_id] = results
            print(f"\n  --- {scenario.scenario_id} ---")
            for r in results:
                eid = r.get("evaluatorId", "")[-35:]
                val = str(r.get("value", r.get("score", "N/A")))
                lbl = str(r.get("label", r.get("rating", "")))[:20]
                print(f"    {eid:<35} {val:>5}  {lbl}")
        except Exception as e:
            print(f"  Evaluation failed for {scenario.scenario_id}: {e}")

    print(f"\n  Evaluated {len(_all_results)} scenario(s) using managed dataset v2 ground truth")
    print(
        "\n  Tip: pin CI/CD pipelines to datasetVersion='2' to ensure reproducible "
        "evaluation results even as the DRAFT evolves."
    )

else:
    print("\n[Part C skipped — no agent config]")
    print("  To run the evaluation integration demo:")
    print("    1. Deploy the HR Assistant: cd ../../utils && python deploy.py")
    print("    2. Re-run: python manage_datasets.py")

# ============================================================
# Part D — Cleanup
# ============================================================

print("\n" + "=" * 60)
print("Part D — Cleanup")
print("=" * 60)

print(f"\n[D20] Deleting {len(_created_datasets)} dataset(s) created by this script ...")
_failed = []
for ds_id in _created_datasets:
    try:
        client.delete_dataset_and_wait(datasetId=ds_id)
        print(f"  Deleted {ds_id}")
    except Exception as e:
        _failed.append(ds_id)
        print(f"  Failed to delete {ds_id}: {e}")

if _failed:
    print(f"\n  {len(_failed)} deletion(s) failed. Run cleanup manually:")
    for ds_id in _failed:
        print(f"    client.delete_dataset_and_wait(datasetId='{ds_id}')")
else:
    print(f"\n  All {len(_created_datasets)} dataset(s) cleaned up.")

print("\n" + "=" * 60)
print("Dataset Management Demo complete.")
print("=" * 60)
