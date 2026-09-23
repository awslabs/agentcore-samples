# Dataset Management with Amazon Bedrock AgentCore

## Overview

AgentCore Dataset Management lets you create, version, and manage evaluation datasets entirely within Amazon Bedrock AgentCore. A **dataset** is a named, account-scoped collection of typed examples used as inputs to evaluation jobs.

```
┌──────────────────────────────────────────────────────────────────┐
│  manage_datasets.py (DatasetClient)                              │
│                                                                  │
│  create_dataset ──► DRAFT ──► add_examples ──► update_examples  │
│                        │                                         │
│                        ▼                                         │
│             create_dataset_version ──► versions/1/  (immutable) │
│                        │                                         │
│                        ▼                                         │
│             create_dataset_version ──► versions/2/  (immutable) │
│                                                                  │
│  list_dataset_examples(datasetVersion="1") ──► ground truth     │
│       │                                                          │
│       ▼                                                          │
│  EvaluationClient / OnDemandEvaluationDatasetRunner              │
│       │                                                          │
│       ▼                                                          │
│  evaluation scores                                               │
└──────────────────────────────────────────────────────────────────┘
```

Datasets are versioned snapshots of evaluation examples. The managed service handles storage, so your evaluation code always has a single source of truth for ground truth data.

---

## What You'll Learn

| Concept | Description |
|:--------|:------------|
| **DatasetClient** | The Python SDK client for creating and managing datasets |
| **PREDEFINED schema** | Datasets with known expected responses, tool trajectories, and assertions |
| **SIMULATED schema** | Datasets with actor-profile scenarios for LLM-driven simulation |
| **DRAFT / Publish model** | How the mutable DRAFT and immutable published versions work together |
| **Version management** | Publishing, reading, and deleting specific dataset versions |
| **Evaluation integration** | Loading a versioned dataset and using it directly with AgentCore evaluation APIs |

---

## Key Concepts

### DRAFT / Publish Model

Every dataset has exactly one mutable **DRAFT** working copy. All example mutations (add, update, delete) operate on the DRAFT in place. When you are ready for a stable snapshot, call `create_dataset_version_and_wait` to publish the DRAFT as an immutable numbered version (1, 2, 3...). The DRAFT is never destroyed after publishing — it persists as the starting point for the next round of edits.

```
CreateDataset ──► DRAFT (mutable, always present)
                     │
             add / update / delete examples
                     │
           create_dataset_version ──► versions/1/ (immutable)
                     │
             add / update / delete examples
                     │
           create_dataset_version ──► versions/2/ (immutable)
```

**Use versions for:**
- CI/CD pipelines — pin to a known-good version for reproducible evaluation
- Pre/post comparisons — evaluate against version N, improve the agent, evaluate again
- Sharing stable snapshots with teammates

### Schema Types

| Schema Type | Use Case | Required Fields |
|:------------|:---------|:----------------|
| `AGENTCORE_EVALUATION_PREDEFINED_V1` | Known expected responses and trajectories | `scenario_id`, `turns` (each with `input`) |
| `AGENTCORE_EVALUATION_SIMULATED_V1` | LLM actor drives multi-turn conversations | `scenario_id`, `actor_profile` (with `context` and `goal`), `input` |

### Dataset Status

| Status | Description |
|:-------|:------------|
| `CREATING` | Initial ingestion in progress (async) |
| `ACTIVE` | Dataset is stable and ready |
| `UPDATING` | Example mutation in progress (async) |
| `DELETING` | Deletion in progress (async) |
| `CREATE_FAILED` | Initial ingestion failed |
| `UPDATE_FAILED` | Last mutation failed |

`draftStatus` tracks whether the DRAFT is ahead of the latest published version:
- `MODIFIED` — DRAFT has unpublished changes
- `UNMODIFIED` — DRAFT matches the latest version exactly (just after publishing)

### API Operations

| Operation | Pattern | Description |
|:----------|:--------|:------------|
| `create_dataset_and_wait` | Async | Create dataset from inline examples or S3; polls until `ACTIVE` |
| `get_dataset` | Sync | Get metadata + presigned download URL for dataset.jsonl |
| `list_datasets` | Sync | Paginated list of all datasets in the account |
| `update_dataset` | Sync | Update description and tags (name and schema are immutable) |
| `add_examples_and_wait` | Async | Add examples to the DRAFT; polls until `ACTIVE` |
| `list_dataset_examples` | Sync | Paginated list of examples with full content |
| `update_examples_and_wait` | Async | Update existing examples in the DRAFT by exampleId |
| `delete_examples_and_wait` | Async | Delete examples from the DRAFT by exampleId |
| `create_dataset_version_and_wait` | Async | Publish DRAFT as next immutable version; polls until `ACTIVE` |
| `list_dataset_versions` | Sync | Paginated list of all published versions |
| `delete_dataset_and_wait` | Async | Delete a specific version or the entire dataset |

---

## Prerequisites

- Python 3.10+
- AWS CLI configured with credentials (`aws configure` or environment variables)
- Permissions for: `bedrock-agentcore:*`, `bedrock-agentcore-control:*`
- For Part C (evaluation integration): the HR Assistant agent must be deployed (`../utils/deploy.py`)

---

## Step 1: Install Dependencies

```bash
cd dataset-management
pip install -r requirements.txt
```

---

## Step 2: Run the Dataset Management Script

```bash
python manage_datasets.py [--region us-east-1]
```

To skip the evaluation integration demo (no agent required):

```bash
python manage_datasets.py --skip-eval
```

The script runs in four parts:

| Part | Steps | What it demonstrates |
|:-----|:------|:---------------------|
| **A — PREDEFINED lifecycle** | A1–A12 | Create, update, add/update/delete examples, publish versions, read pinned versions |
| **B — SIMULATED lifecycle** | B13–B16 | Create actor-profile scenarios, update and publish |
| **C — Evaluation integration** | C17–C19 | Load a versioned dataset, build a `Dataset` object, run `EvaluationClient` |
| **D — Cleanup** | D20 | Delete all datasets created by the script |

---

## DatasetClient Reference

### Creating a Dataset

```python
from bedrock_agentcore.evaluation import DatasetClient

client = DatasetClient(region_name="us-east-1")

# PREDEFINED: known expected responses and trajectories
ds = client.create_dataset_and_wait(
    datasetName="my_eval_dataset",        # letters, numbers, underscores only; max 48 chars
    schemaType="AGENTCORE_EVALUATION_PREDEFINED_V1",
    source={
        "inlineExamples": {
            "examples": [
                {
                    "scenario_id": "pto-check",
                    "turns": [
                        {
                            "input": "What is the PTO balance for EMP-001?",
                            "expected_response": "EMP-001 has 10 remaining PTO days.",
                        }
                    ],
                    "expected_trajectory": {"toolNames": ["get_pto_balance"]},
                    "assertions": [
                        {"text": "Agent called get_pto_balance"},
                        {"text": "Agent reported 10 remaining days"},
                    ],
                }
            ]
        }
    },
)
dataset_id = ds["datasetId"]
print(ds["status"])      # ACTIVE
print(ds["exampleCount"])  # 1
```

For files larger than 5 MB, upload to S3 first and use the `s3` source:

```python
ds = client.create_dataset_and_wait(
    datasetName="large_eval_dataset",
    schemaType="AGENTCORE_EVALUATION_PREDEFINED_V1",
    source={"s3": {"s3Uri": "s3://my-bucket/my-dataset.jsonl"}},
)
```

### Reading a Dataset

```python
# Read DRAFT metadata (default)
got = client.get_dataset(datasetId=dataset_id)
print(got["status"])        # ACTIVE
print(got["exampleCount"])  # current DRAFT count
print(got["draftStatus"])   # MODIFIED or UNMODIFIED
print(got["downloadUrl"])   # presigned URL for dataset.jsonl (5 min TTL)

# Read a specific published version's metadata and download URL
got_v1 = client.get_dataset(datasetId=dataset_id, datasetVersion="1")
print(got_v1["exampleCount"])   # count for version 1 (immutable)
print(got_v1["downloadUrl"])    # presigned URL for versions/1/dataset.jsonl
```

### Listing Datasets

```python
# Paginate through all datasets in the account
next_token = None
all_datasets = []
while True:
    kwargs = {"maxResults": 50}
    if next_token:
        kwargs["nextToken"] = next_token
    resp = client.list_datasets(**kwargs)
    all_datasets.extend(resp.get("datasets", []))
    next_token = resp.get("nextToken")
    if not next_token:
        break

for d in all_datasets:
    print(f"{d['datasetName']:<40} status={d['status']}")
```

### Updating Dataset Metadata

```python
# Only description and tags can be updated. datasetName, schemaType, kmsKeyArn are immutable.
client.update_dataset(
    datasetId=dataset_id,
    description="Updated description for my evaluation dataset",
)
```

### Adding Examples

```python
add_resp = client.add_examples_and_wait(
    datasetId=dataset_id,
    source={
        "inlineExamples": {
            "examples": [
                {
                    "scenario_id": "new-scenario",
                    "turns": [{"input": "What is the dental plan?"}],
                    "expected_trajectory": {"toolNames": ["get_benefits_summary"]},
                }
            ]
        }
    },
)
# add_resp contains status, addedCount, and exampleIds (auto-generated UUIDs)
print(add_resp["status"])      # ACTIVE
```

### Listing Examples

```python
# Read DRAFT examples (default)
resp = client.list_dataset_examples(datasetId=dataset_id)
for ex in resp["examples"]:
    print(ex["exampleId"], ex["scenario_id"])

# Read examples from a specific published version
resp_v1 = client.list_dataset_examples(datasetId=dataset_id, datasetVersion="1")

# Paginate through large datasets
next_token = None
all_examples = []
while True:
    kwargs = {"datasetId": dataset_id, "maxResults": 100}
    if next_token:
        kwargs["nextToken"] = next_token
    resp = client.list_dataset_examples(**kwargs)
    all_examples.extend(resp.get("examples", []))
    next_token = resp.get("nextToken")
    if not next_token:
        break
```

### Updating Examples

```python
# exampleId is required — get it from list_dataset_examples
resp = client.list_dataset_examples(datasetId=dataset_id)
example_id = resp["examples"][0]["exampleId"]

update_resp = client.update_examples_and_wait(
    datasetId=dataset_id,
    examples=[
        {
            "exampleId": example_id,
            "scenario_id": "pto-check",
            "turns": [
                {
                    "input": "What is the PTO balance for EMP-001?",
                    "expected_response": "EMP-001 has 10 remaining PTO days out of 15 total (5 used).",
                }
            ],
            "expected_trajectory": {"toolNames": ["get_pto_balance"]},
            "assertions": [
                {"text": "Agent called get_pto_balance with employee_id=EMP-001"},
                {"text": "Agent reported 10 remaining days, 15 total, 5 used"},
            ],
            "metadata": {"difficulty": "easy", "reviewed": True},
        }
    ],
)
```

### Deleting Examples

```python
# All-or-nothing: if any exampleId is not found, nothing is deleted
del_resp = client.delete_examples_and_wait(
    datasetId=dataset_id,
    exampleIds=["uuid-1", "uuid-2"],
)
print(del_resp["status"])  # ACTIVE
```

### Publishing a Version

```python
# Publish the DRAFT as the next immutable version (1, 2, 3...)
version_resp = client.create_dataset_version_and_wait(datasetId=dataset_id)
print(version_resp["status"])      # ACTIVE
print(version_resp["draftStatus"]) # UNMODIFIED (DRAFT now matches version 1)
```

### Listing Versions

```python
resp = client.list_dataset_versions(datasetId=dataset_id)
for v in resp["versions"]:
    print(f"version={v['datasetVersion']}  examples={v['exampleCount']}  created={v['createdAt']}")
```

### Deleting a Version or Dataset

```python
# Delete a specific published version (DRAFT and other versions are unaffected).
# Use the underlying CP client directly — delete_dataset_and_wait is for full
# dataset deletion only (it polls until the dataset record disappears).
client._cp_client.delete_dataset(datasetId=dataset_id, datasetVersion="1")
# Poll until the version is gone (async operation)
import time
for _ in range(30):
    time.sleep(5)
    remaining = client.list_dataset_versions(datasetId=dataset_id).get("versions", [])
    if not any(v.get("datasetVersion") == "1" for v in remaining):
        break

# Delete the entire dataset (all versions, DRAFT, and the dataset record)
client.delete_dataset_and_wait(datasetId=dataset_id)
```

---

## Schema Reference

### PREDEFINED_V1 Example Format

Required fields: `scenario_id`, `turns` (non-empty; each turn must have `input`).
Optional: `expected_trajectory`, `assertions`, `metadata`.

```json
{
  "scenario_id": "pto-balance-check",
  "turns": [
    {
      "input": "What is the current PTO balance for employee EMP-001?",
      "expected_response": "Employee EMP-001 has 10 remaining PTO days out of 15 total."
    }
  ],
  "expected_trajectory": {
    "toolNames": ["get_pto_balance"]
  },
  "assertions": [
    {"text": "Agent called get_pto_balance with employee_id=EMP-001"},
    {"text": "Agent reported 10 remaining PTO days"}
  ],
  "metadata": {
    "difficulty": "easy",
    "category": "pto"
  }
}
```

### SIMULATED_V1 Example Format

Required fields: `scenario_id`, `actor_profile` (with `context` and `goal`), `input`.
Optional: `scenario_description`, `max_turns` (>= 1), `assertions`, `metadata`.

```json
{
  "scenario_id": "frustrated-refund-customer",
  "scenario_description": "Impatient customer seeks refund for cancelled flight",
  "actor_profile": {
    "traits": {"personality": "impatient"},
    "context": "Has been waiting 3 days for a refund on cancelled flight BK-98765",
    "goal": "Get a full cash refund for cancelled flight BK-98765"
  },
  "input": "I want my money back for flight BK-98765 that was cancelled!",
  "max_turns": 8,
  "assertions": [
    {"text": "Agent acknowledged the frustration and apologized"},
    {"text": "Agent initiated the refund process for BK-98765"}
  ]
}
```

---

## Integration with Evaluation

The primary value of managed datasets is that they serve as a **version-pinned ground truth source** for your evaluation workflows. Instead of hardcoding scenarios in your evaluation script, load them from a published dataset version:

```python
from bedrock_agentcore.evaluation import (
    DatasetClient, EvaluationClient, ReferenceInputs,
    Dataset, PredefinedScenario, Turn,
)
from datetime import timedelta

client = DatasetClient(region_name="us-east-1")

# Load the approved ground truth from version 1
examples_resp = client.list_dataset_examples(
    datasetId="my-dataset-id",
    datasetVersion="1",          # pin to immutable version 1
)

# Convert to Dataset object for use with OnDemandEvaluationDatasetRunner
scenarios = []
for ex in examples_resp["examples"]:
    turns = [Turn(input=t["input"], expected_response=t.get("expected_response")) for t in ex["turns"]]
    traj = ex.get("expected_trajectory", {}).get("toolNames", [])
    assertions = [a["text"] for a in ex.get("assertions", []) if isinstance(a, dict)]
    scenarios.append(PredefinedScenario(
        scenario_id=ex["scenario_id"],
        turns=turns,
        expected_trajectory=traj or None,
        assertions=assertions or None,
    ))
dataset = Dataset(scenarios=scenarios)

# Use with EvaluationClient — pull ground truth directly from managed dataset
eval_client = EvaluationClient(region_name="us-east-1")
first_ex = examples_resp["examples"][0]
results = eval_client.run(
    evaluator_ids=["Builtin.Correctness", "Builtin.GoalSuccessRate"],
    session_id="my-session-id",
    agent_id="my-agent-id",
    look_back_time=timedelta(hours=2),
    reference_inputs=ReferenceInputs(
        expected_response=first_ex["turns"][0].get("expected_response"),
        assertions=[a["text"] for a in first_ex.get("assertions", []) if isinstance(a, dict)],
    ),
)
```

### CI/CD Pattern

A common pattern for CI/CD pipelines:

```
Dataset review cycle:
  1. Curate examples in DRAFT (add/update/delete)
  2. Run evaluations against DRAFT to validate quality
  3. Approve: create_dataset_version_and_wait → version N
  4. Update CI/CD to pin to version N

Evaluation pipeline:
  1. Load examples from version N (immutable — never changes)
  2. Run OnDemandEvaluationDatasetRunner or BatchEvaluationRunner
  3. Compare scores against baseline
  4. If agent improved, proceed. If not, investigate.
```

---

## Naming Constraints

| Field | Constraint |
|:------|:-----------|
| `datasetName` | Letters, numbers, underscores only. Must start with a letter. Max 48 characters. Case-insensitive uniqueness within the account. Immutable after creation. |
| `schemaType` | Immutable after creation. |
| `kmsKeyArn` | Immutable after creation. |
| Max examples per mutation call | 1,000 |
| Max inline content | 5 MB |
| Max published versions per dataset | 50 |

---

## Example Output

```
============================================================
AgentCore Dataset Management Demo
============================================================
  Region : us-east-1
  Part C : enabled

============================================================
Part A — PREDEFINED dataset lifecycle
============================================================

[A1] Creating PREDEFINED dataset ...
  datasetId    : abc123-def456-ghi789
  status       : ACTIVE
  exampleCount : 3
  draftStatus  : MODIFIED

[A2] Getting dataset metadata ...
  datasetName          : hr_eval_predefined_dm_1716000000
  schemaType           : AGENTCORE_EVALUATION_PREDEFINED_V1
  status               : ACTIVE
  exampleCount (DRAFT) : 3
  downloadUrl          : <presigned URL — use to download dataset.jsonl>
  downloadUrlExpiresAt : 2026-05-21T12:05:00Z

[A3] Listing datasets (first page, max 5) ...
  Found 3 dataset(s) on this page
    hr_eval_predefined_dm_1716000000         status=ACTIVE        examples=3

[A4] Updating dataset metadata ...
  description updated to: 'HR evaluation dataset — PTO, policy, and benefits scenarios'

[A5] Adding new examples to the DRAFT ...
  status       : ACTIVE
  exampleCount : 5 (DRAFT — after adding 2 examples)

[A9] Publishing DRAFT as version 1 ...
  status      : ACTIVE
  draftStatus : UNMODIFIED (UNMODIFIED = DRAFT matches version 1)

[A10] Listing published versions ...
  Published versions: 1
    version=1  exampleCount=4  createdAt=2026-05-21T12:01:00Z

[A11] Reading examples from published version 1 ...
  datasetVersion : 1
  Total examples : 4
    scenario_id=pto-balance-check              assertions=3
    scenario_id=pto-policy-lookup              assertions=2
    scenario_id=401k-info                      assertions=2
    scenario_id=submit-pto-request             assertions=2
```

---

## Comparison with Inline Dataset Construction

| Approach | Where stored | Versioned | Shareable | Persistent |
|:---------|:-------------|:----------|:----------|:-----------|
| **Managed dataset (DatasetClient)** | AgentCore service (S3 + DDB) | Yes | Yes | Yes |
| **Inline Dataset object** | In-memory (Python) | No | No | No |
| **S3 JSONL file** | Your S3 bucket | Manual | Yes | Yes |

Use managed datasets when you want versioning, sharing, and a single source of truth. Use inline `Dataset` objects for quick prototyping.

---

## Clean Up

All datasets created by `manage_datasets.py` are deleted at the end of the script. To clean up manually:

```python
from bedrock_agentcore.evaluation import DatasetClient

client = DatasetClient(region_name="us-east-1")
client.delete_dataset_and_wait(datasetId="your-dataset-id")
```

To delete all datasets whose names start with a prefix:

```python
next_token = None
while True:
    resp = client.list_datasets(maxResults=100, **({"nextToken": next_token} if next_token else {}))
    for d in resp.get("datasets", []):
        if d["datasetName"].startswith("hr_eval_"):
            client.delete_dataset_and_wait(datasetId=d["datasetId"])
            print(f"Deleted {d['datasetName']}")
    next_token = resp.get("nextToken")
    if not next_token:
        break
```

---

## Additional Resources

- [Amazon Bedrock AgentCore Dataset Management](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/dataset-management.html)
- [Amazon Bedrock AgentCore Evaluations](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations.html)
- [Amazon Bedrock AgentCore Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/)

---

## Files

| File | Description |
|:-----|:------------|
| `manage_datasets.py` | Full dataset management demo — PREDEFINED, SIMULATED, versioning, and evaluation integration |
| `requirements.txt` | Python dependencies (`bedrock-agentcore>=1.8.0`, `boto3`) |
| `../evaluate.py` | Ground truth evaluation script — shows how managed datasets integrate with EvaluationClient and DatasetRunner |
| `../utils/deploy.py` | Deploys the HR Assistant agent (required for Part C) |
