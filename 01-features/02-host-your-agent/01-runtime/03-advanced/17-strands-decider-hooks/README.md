# Strands Decider with a build warmup hook

Runs [Strands Decider](https://strandsagents.com/blog/introducing-strands-decider/)
(`strands-decider-2B`, a 2B-parameter decision model) on Amazon Bedrock
AgentCore Runtime, CPU only, and loads it through a **build warmup hook**
(`hooksConfiguration.build.warmup`) so a new session starts with the model
already in memory.

Everything is in `infra/common.sh`: `platformVersion` V2,
`minimumMemoryInMiB` 8192, the warmup hook on `POST /warmup:8080`. The only
setting is the region, `us-east-1` by default:

```bash
REGION=us-east-1 ./deploy.sh    # pass the same REGION to invoke.sh and destroy.sh
```

The scripts use the `python3` of the active environment, which needs the
packages in `infra/requirements.txt` (boto3 1.44.0 or later).

## New AgentCore Runtime features used

This sample depends on three recent AgentCore Runtime capabilities:

- **Minimum memory per session** ([documentation](TODO-link-resource-configurations)).
  `infra/common.sh` sends:

  ```json
  "resourceConfigurations": [{"minimumMemoryInMiB": 8192}]
  ```

  A session starts with a baseline of 8 GB of memory and 2 vCPU, and can grow
  to a peak of 32 GB of memory and 8 vCPU. The model needs that headroom:
  about 4.7 GB right after the restore, and about 12.4 GB once it is
  converted to fp32 on the first request.

- **Container images up to 10 GB** ([service quotas](TODO-link-quotas)).
  This image is about 4.2 GB compressed, because the model weights are
  inside it.

- **Lifecycle hooks** ([documentation](TODO-link-hooks)). `hooksConfiguration`
  registers the build warmup hook described in
  [What the hook does](#what-the-hook-does). With boto3 1.44.0 or later, both
  new fields are regular parameters of `create_agent_runtime` (and
  `update_agent_runtime`):

  ```python
  import boto3

  client = boto3.client("bedrock-agentcore-control", region_name="us-east-1")

  client.create_agent_runtime(
      agentRuntimeName="strandsDeciderHooks",
      agentRuntimeArtifact={"containerConfiguration": {"containerUri": "<account>.dkr.ecr.us-east-1.amazonaws.com/strands-decider-hooks:latest"}},
      roleArn="arn:aws:iam::<account>:role/decider-hooks-agentcore-role-us-east-1",
      networkConfiguration={"networkMode": "PUBLIC"},
      platformVersion="V2",  # hooks need V2
      resourceConfigurations=[{"minimumMemoryInMiB": 8192}],
      hooksConfiguration={
          "build": {
              "warmup": {
                  # The platform calls POST /warmup:8080 twice while it builds the runtime,
                  # with X-Amzn-Bedrock-AgentCore-Runtime-Warmup-Type: memory, then disk.
                  "action": {"httpPost": {"path": "/warmup", "port": 8080}}
              }
          }
      },
  )
  ```

  The hook has no timeout parameter: the platform gives both calls 120 s in
  total.

## Run it

```bash
uv venv && source .venv/bin/activate && uv pip install -r infra/requirements.txt
# or: python3 -m venv .venv && source .venv/bin/activate && pip install -r infra/requirements.txt
cd infra
./deploy.sh      # build + push the image, create the runtime, wait for READY
./invoke.sh      # one request on a new session, with the time it took
./usage.sh       # CPU, memory and estimated cost per session
./destroy.sh     # delete the usage logs, the runtime, the IAM role and the ECR repository
```

`./deploy.sh --skip-build` reuses the image already in ECR.

### The example: support ticket triage

`infra/payload.json` is a support ticket. One call answers six business
questions about it, using all three question types:

| Question | Type | Decision it drives |
|---|---|---|
| `category` | `choice` | Which team gets the ticket (billing, technical, account, sales) |
| `urgency` | `score` | Priority, on low / normal / high / critical |
| `sentiment` | `score` | How the customer feels, on calm / annoyed / frustrated / angry |
| `churn_risk` | `noul` | Whether the customer is signaling they may leave |
| `refund` | `noul` | Whether resolving it needs a refund |
| `auto_reply` | `noul` | Whether a template reply can close it without a human |

`choice` and `score` answers carry a `confidence` from 0 to 1, and `noul`
answers a probability. That lets a caller act automatically on confident
answers and send the rest to a person or a larger model.

The state is encoded once and every question is scored against it, so
adding questions costs little. `./invoke.sh my-payload.json` sends any other
payload: `state` is text or JSON, and each question is
`{"type": "noul" | "choice" | "score", "instructions": ..., "criteria": ...}`
(`choice`: 2–255 options; `score`: 2–10 levels, lowest first). A `state`
longer than the model's context window is truncated without an error.

The response also has `hooks_seen`, the hook calls the process saw before
the snapshot. A session restored from the post-warmup snapshot shows
`["memory"]`: the `disk` call runs on another VM whose state isn't
snapshotted. An empty list means the hook wasn't called.

### CPU, memory and cost per session

`deploy.sh` turns on the runtime's `USAGE_LOGS`, which AgentCore writes once
per second per session to
`/aws/vendedlogs/bedrock-agentcore/runtimes/USAGE_LOGS/<runtime-id>`.
`./usage.sh` sums them per session; `./usage.sh <session-id>` shows one.
Records arrive a few minutes after the session runs.

Illustrative output (a synthetic 100 s session: 20 s at 4 vCPU, 80 s at
0.2 vCPU, 12 GB throughout):

```
baseline 8 GB / 2 vCPU; V2 consumption $0.1276/vCPU-h $0.0169/GB-h; committed $0.0997/vCPU-h $0.0132/GB-h
session                                start     secs avg vCPU  avg GB peak GB  consumption  committed
test-session                           08:00:00   100     0.96   12.00   12.00 $    0.00904 $  0.01105
```

It estimates each session's cost both ways, with the
[microVM v2 list prices](https://aws.amazon.com/bedrock/agentcore/pricing/):

- **consumption** (no resource configuration): the vCPU and memory the
  session actively uses, billed per second at the consumption rate ($0.1276
  per vCPU-hour, $0.0169 per GB-hour).
- **committed** (with `resourceConfigurations`, as this sample deploys): the
  baseline (8 GB and 2 vCPU, set in `infra/common.sh`) for
  as long as the session runs, plus usage above the baseline, billed per
  second. Both are billed at the committed baseline rate ($0.0997 per
  vCPU-hour, $0.0132 per GB-hour).

The baseline is paid even while the session idles, so committed comes out
cheaper only when a session uses most of its baseline. This agent idles
between requests, which leaves most of the 2 vCPU unused. These are
estimates; your bill comes from metered usage.

## What the hook does

With `platformVersion` V2, AgentCore Runtime starts the agent once while it
builds the runtime, takes a snapshot, and starts every session from it. The
build calls `POST /warmup` twice, with the call type in the
`X-Amzn-Bedrock-AgentCore-Runtime-Warmup-Type` header:

| Call | Where | What `agent/app.py` does | Why |
|---|---|---|---|
| `memory` | Build VM, before the snapshot | Waits until the model is loaded and its weights are copied into process memory (up to 50 s) | The snapshot is taken only after the hook answers, so it always holds the full model |
| `disk` | Validate VM | Reads every native library under `site-packages`, largest first (up to 50 s) | These are the files the first inference reads cold from the image |

If the model fails to load, the `memory` call answers 500 and the build
fails with the reason in the logs, instead of producing a snapshot without
a model. Both calls share a 120 s budget; a non-2xx answer or a timeout
fails the create.

The model also starts loading in a background thread at import, so `/ping`
answers right away and the agent still works if the hook is never called.

## Why the model is loaded this way

- **Weights copied out of the file mapping.** `load_engine` only memory-maps
  the weight files, and the page cache is dropped before the snapshot. Left
  mapped, the first request reads ~4 GiB cold from the image; in one session
  that took 877 s. Copied into process memory, the weights travel inside the
  snapshot.
- **bf16 in the snapshot, fp32 after the restore.** The library converts the
  model to fp32 on CPU (~7.5 GiB). The CPU the sessions ran on has no native bf16,
  so bf16 inference is emulated and too slow. The snapshot holds bf16
  (~4.7 GiB) and the first invocation converts it to fp32 (under 3 s
measured).
- **First response within 120 s.** A new session that doesn't answer its
  first request within 120 s fails with `Runtime initialization time exceeded`.

## Build details

- `--provenance=false --sbom=false` in `deploy.sh`: the default `buildx`
  image index with an attestation manifest fails runtime creation with
  `LayersNotFoundException`.
- torch comes from the CPU-only index; the default arm64 wheel pulls ~4 GB
  of CUDA libraries.
- The model cache directory is created and owned before the download; a
  `chown -R` afterwards copies the whole cache into a second image layer.
- `hooksConfiguration` and `resourceConfigurations` need boto3 1.44.0 or
  later. The hook has no timeout field: the platform fixes the budget at
  120 s for both calls together, and boto3 rejects `timeoutSeconds`.
- Versions are pinned in `agent/requirements.txt` and the Dockerfile.
- Both models are pinned to a commit: the decider checkpoint
  (`216d1cc…`, downloaded to `/app/model`) and the base model it is built on
  (`Qwen/Qwen3.5-2B-Base` at `b1485b2…`, the revision the checkpoint's
  `provenance.json` records). After the download the image sets
  `HF_HUB_OFFLINE=1`, so neither the build nor a session contacts the
  Hugging Face Hub again, and the build fails if the pinned files don't load
  offline. To move to a newer checkpoint, change both commits in the
  Dockerfile.
- The execution role only trusts AgentCore resources in this account and
  region (`aws:SourceAccount`, `aws:SourceArn`), and is named per region.

## Delete everything

From `infra/`, with the same `REGION` the deploy used:

```bash
REGION=us-east-1 ./destroy.sh
```

It deletes, in order: the `USAGE_LOGS` delivery and its log group, the
runtime, the IAM role `decider-hooks-agentcore-role-<region>` and the ECR
repository `strands-decider-hooks` with its images. It finds the runtime by
name, so it works even without the local state file.

Two things are left behind on purpose or by history; delete them by hand if
you want a fully clean account:

```bash
# The runtime's own log group, created by AgentCore (build and session logs).
aws logs delete-log-group --region us-east-1 \
  --log-group-name /aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT

# Only if you deployed before the role name included the region.
aws iam delete-role-policy --role-name decider-hooks-agentcore-role --policy-name decider-hooks-agentcore-role-policy
aws iam delete-role --role-name decider-hooks-agentcore-role
```

The runtime stays in `DELETING` for a little while. Wait until it's gone
before deploying again, or `deploy.sh` finds it by name and tries to update
it:

```bash
aws bedrock-agentcore-control list-agent-runtimes --region us-east-1 \
  --query "agentRuntimes[?agentRuntimeName=='strandsDeciderHooks'].[agentRuntimeId,status]" --output text
# empty output = gone; then: REGION=us-east-1 ./deploy.sh
```

## When something fails

| Symptom | Where to look |
|---|---|
| `A lifecycle hook failed…` or `…didn't finish within its timeoutSeconds` | `deploy.sh` prints the hook's lines from the `build-logs-*` streams. The agent logs one `{"event": "warmup", ...}` line per call; the platform logs `[LIFECYCLE-HOOK …]` lines with the time against the 120 s budget |
| `ValidationException` naming `hooksConfiguration` on create | Hooks aren't available for this account or region |
| `needs boto3 >= 1.44.0` when a script starts | The active `python3` has an older boto3: `uv pip install -r infra/requirements.txt` (or `pip install`) in that environment |
| `Runtime initialization time exceeded` | `runtime-logs-<session-id>` in `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`. No stream at all means the container never started for that session |
| `Session operation in progress` | Transient while a session provisions; `agentcore_boto3.py` retries it for up to 2 minutes |

## Layout

```
agent/
  app.py             model loading, POST /warmup, /invocations
  Dockerfile         linux/arm64, CPU-only torch, weights baked in
  requirements.txt   pinned versions
infra/
  common.sh          fixed configuration, IAM policies, runtime settings
  agentcore_boto3.py boto3 wrapper for the control and data plane calls
  deploy.sh          image -> IAM role -> runtime
  invoke.sh          one request on a new session
  usage.sh           CPU, memory and estimated cost per session (USAGE_LOGS)
  destroy.sh         deletes what deploy.sh created
  payload.json       default request (support ticket triage)
  requirements.txt   boto3 >= 1.44.0 for the scripts
```
