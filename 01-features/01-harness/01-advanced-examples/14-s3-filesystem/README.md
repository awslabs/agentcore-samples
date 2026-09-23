# S3 Filesystem Mount

| Information         | Details                                                                  |
|:--------------------|:-------------------------------------------------------------------------|
| Tutorial type       | Advanced Example                                                         |
| Agent type          | Assistant with persistent storage                                        |
| Agentic Framework   | None (direct boto3)                                                      |
| LLM model           | Anthropic Claude Haiku 4.5                                               |
| Tutorial components | AgentCore harness — `filesystemConfigurations`, S3 Files access point    |
| Example complexity  | Intermediate                                                             |

## Overview

A harness session runs in an isolated microVM with an **ephemeral** disk — when
the session ends, anything written to the VM is gone. Mount an **S3 Files access
point** into the VM and the agent gets a normal POSIX path (e.g. `/mnt/data`)
backed by S3, so artifacts persist past the session and are shared across
sessions.

## What's in this folder

| File | What it shows |
|---|---|
| [`s3_filesystem.py`](s3_filesystem.py) | **The mechanism.** Session A writes a file under the mount; Session B (a brand-new microVM) reads it back — only possible because the file lives in S3, not on the VM disk. |
| [`s3_llm_wiki.py`](s3_llm_wiki.py) | **The use case: a persistent LLM wiki.** The agent builds and maintains a compounding markdown wiki on the S3 mount across sessions (ingest → query → lint). |
| [`provision_s3_filesystem.py`](provision_s3_filesystem.py) | **Optional setup.** Creates the prerequisites below (bucket, file system, access point, mount targets) and prints the command line to paste into either script. `--teardown` removes exactly what it created. |

The first script proves the persistence boundary; the second shows *why you'd
want it*. Both mount an **existing** access point — if you don't have one yet,
[`provision_s3_filesystem.py`](provision_s3_filesystem.py) will make one.

## Configuration

An S3 Files mount requires the harness to run in **VPC network mode** — the
microVM reaches the access point's mount target over your VPC. So the
environment carries both a `networkConfiguration` and the `filesystemConfigurations`:

```python
environment={
    "agentCoreRuntimeEnvironment": {
        "networkConfiguration": {
            "networkMode": "VPC",
            "networkModeConfig": {
                "subnets": ["subnet-0abc1234"],
                "securityGroups": ["sg-0def5678"],
            },
        },
        "filesystemConfigurations": [
            {
                "s3FilesAccessPoint": {
                    "accessPointArn": "arn:aws:s3files:us-west-2:111122223333:file-system/fs-abc/access-point/fsap-def",
                    "mountPath": "/mnt/data",
                }
            }
        ],
    }
}
```

`mountPath` must look like `/mnt/<name>`. The execution role must be allowed to
mount the access point — when this script creates the role, it attaches the
required `s3files` permissions for you: `s3files:GetAccessPoint` **and
`s3files:ListMountTargets`** (the runtime validates both at create time, so they
stay unscoped — omitting `ListMountTargets` puts the harness straight into
`CREATE_FAILED`) plus `s3files:ClientMount` and `s3files:ClientWrite` (scoped to
the file system with an `AccessPointArn` condition, used when the microVM mounts
the access point).

## Prerequisites

- An **S3 Files access point** backed by a **versioned** bucket, with a **mount
  target** in the subnet you pass. Its ARN looks like:
  `arn:aws:s3files:<region>:<account>:file-system/fs-xxxx/access-point/fsap-xxxx`
  Bucket **versioning is required** — `CreateFileSystem` rejects an unversioned
  bucket with `Your bucket must have versioning enabled to create a file system.`
- The **subnet(s) and security group(s)** that reach the mount target. The Harness
  must be in the **same VPC** as the mount target, the subnet(s) you pass must be
  in an **Availability Zone that has a mount target**, and the security group(s)
  must allow **NFS (port 2049)** between the Harness and the mount target (a
  self-referencing security group is the simplest setup).
- **Use private subnets with egress** (a route to a NAT gateway). VPC-mode
  Harnesses run in private networking; public subnets do not give the microVM the
  connectivity it needs and the invoke will fail. See
  [Configure AgentCore for VPC](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html).
- **AWS credentials** for a region where AgentCore Harness is available, and
  **model access** to `global.anthropic.claude-haiku-4-5-20251001-v1:0` (or pass
  another model with `--model`).
- If you bring your own execution role (`--role-arn`), it must already have the
  `s3files` mount permissions above. A role you supply is never deleted on
  cleanup; the shared `HarnessExecutionRole` this script creates itself is.

Creating the harness in VPC mode takes noticeably longer than the default network
mode — expect **roughly 2–3 minutes** of `CREATING` before it reports `READY`, so
the wait doesn't read as a hang.

## Sample Prompts

**Prompt (Session A)**: "Write a short markdown travel note about Amsterdam to /mnt/data/harness-note.md."
**Expected Behavior**: Agent writes the file under the mounted path and confirms the absolute path.

**Prompt (Session B, fresh VM)**: "Read the file /mnt/data/harness-note.md and show me its contents verbatim."
**Expected Behavior**: Agent reads back the note written in Session A — the S3-backed mount persisted it.

## Key Concepts

**Persistence boundary**: A different `session_id` means a different VM disk. Surviving that boundary is what proves the mount is S3-backed.

**Mount path format**: `mountPath` must match `/mnt/<name>` (validated by the script before the call).

**IAM scope**: The mount permissions (`ClientMount`/`ClientWrite`) are scoped to the single access point with an `AccessPointArn` condition. The two the runtime checks at create time (`GetAccessPoint`, `ListMountTargets`) have to stay on `"*"`: `ListMountTargets` is authorized against the *file system* ID rather than the access point, so scoping it to the access point ARN denies it.

## Optional: provisioning the prerequisites

Both sample scripts mount an **existing** access point — they don't create
infrastructure, so on a fresh account there is nothing for `--access-point-arn`
to point at. [`provision_s3_filesystem.py`](provision_s3_filesystem.py) closes
that gap:

```bash
# Create the S3 Files layer and print the command line to run the sample with
python provision_s3_filesystem.py

# See what it would do first, without creating anything
python provision_s3_filesystem.py --dry-run

# Delete everything it created
python provision_s3_filesystem.py --teardown
```

It creates a bucket (versioning enabled), the IAM service role S3 Files assumes,
a file system and access point over that bucket, a mount target per subnet, and a
security group allowing NFS 2049. Each resource is written to
`provision_state.json` as it is created, and `--teardown` deletes **only** what is
recorded there — a bucket or VPC you brought yourself is never touched.

**Networking: bring your own by default.** The script does *not* create a VPC. It
looks for private subnets that already have NAT-gateway egress and uses those,
because a NAT gateway bills hourly whether or not you're using it and is the
resource people forget to delete. Pass `--create-vpc` only if the account has no
suitable subnets; it then builds the VPC, subnets, internet gateway and NAT
gateway too, and `--teardown` removes them.

| Flag | What it does |
|---|---|
| `--bucket NAME` | Reuse a bucket you already have. It must have versioning enabled, and it is **never** deleted on teardown. |
| `--prefix PREFIX` | Key prefix the file system is scoped to (default: `harness-sample/`). |
| `--subnet-ids` | Use these subnets instead of discovering private ones with NAT egress. |
| `--security-group-ids` | Use these security groups instead of creating one that allows NFS 2049. |
| `--create-vpc` | Also create a VPC, subnets and a **NAT gateway** (bills hourly). |
| `--dry-run` | Report what would be created, and what was discovered, without creating it. Combine with `--teardown` to preview a deletion. |
| `--teardown` | Delete everything in `provision_state.json`, then remove the file. |

Two things worth knowing about teardown:

- **Expect to run it twice.** AgentCore reclaims the harness microVM's network
  interfaces on its own schedule *after* the harness is deleted — well over an
  hour in testing — and the security group can't be deleted until they're
  released. Rather than make you wait, the first run deletes everything that
  bills, reports the security group as still held, and exits 0. Re-run it later
  and it removes just what's left.
- It is **safe to re-run** as often as you like. Anything already gone is skipped,
  and the state file shrinks as each resource is deleted.

`--teardown --dry-run` lists what would be deleted, and what it would leave alone,
without touching anything.

## Use case: a persistent LLM wiki

[`s3_llm_wiki.py`](s3_llm_wiki.py) turns the S3 mount into a
**persistent, compounding LLM wiki**, following the pattern Andrej Karpathy
describes in [this gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f):
rather than re-deriving answers from raw documents on every query (classic RAG),
the agent **builds and maintains a markdown wiki once and keeps it current**, so
knowledge becomes a compounding artifact.

> This is a self-maintained markdown wiki on the agent's filesystem — it is
> unrelated to the **Amazon Bedrock Knowledge Bases** feature.

The S3 mount is what makes this possible — the wiki must outlive any single
session and be shared across invocations. Three layers live under the mount:

```
/mnt/wiki/
  sources/   raw, immutable inputs (the agent reads, never edits)
  pages/     LLM-owned markdown: summaries, entity pages, concept pages ([[cross-linked]])
  AGENTS.md  the schema (how the wiki is organized)
  index.md   catalog of pages
  log.md     append-only history
```

Three operations, **each run in its own session** to prove the wiki persists
across the microVM boundary:

- **ingest** — read a raw source and integrate it across the wiki (create/update pages)
- **query** — answer from the wiki, then file the answer back as a new page so explorations compound
- **lint** — health-check: contradictions, stale claims, orphan pages, broken links

Re-run with `--op query` later and the wiki is still there in S3 — the agent
picks up exactly where it left off.

## Clean Up

```python
control.delete_harness(harnessId=harness_id)
from utils.iam import delete_harness_role
delete_harness_role()
```

The script deletes the harness on exit (pass `--skip-cleanup` to keep it). It
**leaves your S3 bucket and access point intact**.

## Running the Python Scripts

```bash
pip install -r ../../requirements.txt
```

```bash
# 1) The mechanism — prove persistence across sessions
python s3_filesystem.py \
    --access-point-arn arn:aws:s3files:us-west-2:111122223333:file-system/fs-abc/access-point/fsap-def \
    --subnet-ids subnet-0abc1234 \
    --security-group-ids sg-0def5678

# Custom mount path + filename
python s3_filesystem.py \
    --access-point-arn arn:aws:s3files:... \
    --subnet-ids subnet-0abc1234 --security-group-ids sg-0def5678 \
    --mount-path /mnt/shared \
    --filename trip-notes.md
```

```bash
# 2) The LLM wiki — full demo (bootstrap, ingest, query, lint)
python s3_llm_wiki.py \
    --access-point-arn arn:aws:s3files:us-west-2:111122223333:file-system/fs-abc/access-point/fsap-def \
    --subnet-ids subnet-0abc1234 --security-group-ids sg-0def5678

# Query the existing wiki (it compounds — answers get filed back)
python s3_llm_wiki.py --access-point-arn arn:aws:s3files:... \
    --subnet-ids subnet-0abc1234 --security-group-ids sg-0def5678 \
    --op query -m "How does the LLM wiki pattern differ from RAG?"
```

Other options (shared unless marked otherwise):

| Flag | What it does |
|---|---|
| `--model MODEL_ID` | Use a different Bedrock model (default: Claude Haiku 4.5). |
| `--message`/`-m` (wiki only) | The question for the `query` operation. |
| `--op` (wiki only) | Run a single stage — `ingest`, `query` or `lint` — instead of `all`. |
| `--filename` (mechanism only) | Name of the file written under the mount. |
| `--role-arn ARN` | Reuse an existing execution role instead of creating one. It must already carry the `s3files` permissions, and it is **not** deleted on cleanup. |
| `--skip-cleanup` | Keep the harness (and the role, if this run created it) after the demo. |
| `--raw-events` | Print the raw JSON streaming events instead of formatted output — useful when debugging the stream. |

`--help` lists every option for either script.
