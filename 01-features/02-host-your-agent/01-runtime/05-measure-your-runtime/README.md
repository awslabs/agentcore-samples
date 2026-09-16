# 0_public

An AgentCore Runtime benchmark: two deployment paths (zip, container),
each testable under two managed-compute settings (warm pool `V1`, SnapStart
`V2`), and the container path testable at five image sizes. Derived from a
larger benchmark that also covered other runtimes and scenarios — those are
not here; this copy focuses purely on AgentCore's own paths and settings. See
`infrastructure/README.md` and `benchmarks/README.md` for the reasoning
behind each cut.

```
0_public/
  base-agent/       the app under test (FastAPI echo server, no LLM call)
  infrastructure/   deploy scripts: build the image, create the AgentCore runtimes
  benchmarks/        load-ramp harness: drive traffic, write results-*.json, parse them
```

## Prerequisites

- AWS credentials in the shell (`aws sts get-caller-identity` should work).
- `aws` CLI, `docker`, `zip`, `python3` (3.13).
- `uv` on PATH (used by `deploy-agentcore-zip.sh` to build the zip artifact
  with `uv pip install`).

## Step 1 — build and push the container image

```bash
cd infrastructure
./build-and-push.sh                # IMAGE_SIZE defaults to 200mb (baseline, unpadded)
IMAGE_SIZE=750mb ./build-and-push.sh   # push another size when you need it
```

Each `IMAGE_SIZE` becomes a separate ECR tag (`200mb`, `500mb`, `750mb`,
`1gb`, `2gb`), so pushing one doesn't overwrite another — deploy whichever
tags you actually want to compare.

## Step 2 — deploy the AgentCore runtimes

Zip needs no image; container needs the size you just pushed. Both accept
`AGENTCORE_MANAGED_COMPUTE_VERSION` (`V1` warm pool, `V2` SnapStart), which
also names the runtime for you:

```bash
# zip, both versions
AGENTCORE_MANAGED_COMPUTE_VERSION=V1 ./deploy-agentcore-zip.sh
AGENTCORE_MANAGED_COMPUTE_VERSION=V2 ./deploy-agentcore-zip.sh
# -> runtimes ac_zip_x_lambda_bench_agent_V1 and ..._V2

# container, one size, both versions
IMAGE_SIZE=750mb AGENTCORE_MANAGED_COMPUTE_VERSION=V1 ./deploy-agentcore.sh
IMAGE_SIZE=750mb AGENTCORE_MANAGED_COMPUTE_VERSION=V2 ./deploy-agentcore.sh
# -> runtimes ac_ctn_x_lambda_agent_750mb_V1 and ..._750mb_V2
```

Each deploy prints the runtime's ARN — copy it, you need it in step 3.
Re-running the same command **updates** that runtime in place; add
`AGENTCORE_NEW_DEPLOY=1` to force a brand-new one instead (useful if you want
a genuinely cold, never-invoked runtime rather than a warm one from a
previous run).

Full parameter reference: `infrastructure/README.md`.

## Step 3 — configure the benchmark

```bash
cd ../benchmarks
cp .env.example .env
```

Edit `.env`: paste the ARN(s) from step 2 into `AGENTCORE_ARN` (container) and
`AGENTCORE_ZIP_ARN` (zip). You only need to set the ARN for a leg you're
about to test — `run_scenario.sh` skips a leg whose ARN is unset rather than
failing.

`.env` can hold an ARN for every size/version you've deployed at once —
`AGENTCORE_ARN_750MB_V2`, `AGENTCORE_ZIP_ARN_V1`, and so on (see
`benchmarks/.env.example`). Pass `IMAGE_SIZE` and/or
`AGENTCORE_MANAGED_COMPUTE_VERSION` to `run_scenario.sh` to pick which one a
given run uses, and its output filename picks up the same suffix
automatically, so runs against different sizes/versions never overwrite each
other:

```bash
IMAGE_SIZE=750mb AGENTCORE_MANAGED_COMPUTE_VERSION=V2 ./run_scenario.sh 1 agentcore
# -> results-scenario1-agentcore-750mb-V2.json
AGENTCORE_MANAGED_COMPUTE_VERSION=V1 ./run_scenario.sh 1 agentcore-zip
# -> results-scenario1-agentcore-zip-V1.json
```

## Step 4 — run a scenario

```bash
RAMP_TARGET=100 ./run_scenario.sh 1   # small rehearsal first — do this before a full run
./run_scenario.sh 1                   # scenario 1: account ceiling / cold start
./run_scenario.sh 2                   # scenario 2: warm-throughput staircase
./run_scenario.sh 1 agentcore-zip     # a single leg instead of both
```

Full scenario definitions and flags: `benchmarks/README.md`.

## Step 5 — read the results

```bash
python3 analyze_results.py results-scenario1-agentcore.json
python3 analyze_results.py results-scenario*-*.json
```

Prints target/built/errors and latency percentiles for scenario 1, or the
per-step warm-throughput table (plus teardown outcome) for scenario 2 — no
AWS calls, it only reads the JSON `run_scenario.sh` already wrote.

## End-to-end example

```bash
cd infrastructure
./build-and-push.sh
AGENTCORE_MANAGED_COMPUTE_VERSION=V2 ./deploy-agentcore-zip.sh   # prints the ARN

cd ../benchmarks
cp .env.example .env
# paste the ARN into AGENTCORE_ZIP_ARN in .env
RAMP_TARGET=100 ./run_scenario.sh 1 agentcore-zip   # rehearsal
./run_scenario.sh 1 agentcore-zip
python3 analyze_results.py results-scenario1-agentcore-zip.json
```

## What's intentionally not here

- **Other runtimes.** This environment compares AgentCore Runtime's own
  paths and settings against each other, not against any other compute
  option.
- **Per-unit capacity (the original benchmark's scenario 4)** and the
  original scenario 2 (400 units/min, never used in this series). Only
  account-ceiling and warm-throughput are covered, renumbered 1 and 2.
- **Historical result files and reports.** This copy carries only the
  scripts; past `results-*.json` runs and their HTML writeups live in the
  original benchmark directory, not here.
