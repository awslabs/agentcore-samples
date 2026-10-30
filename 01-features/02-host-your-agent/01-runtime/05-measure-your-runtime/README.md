# 05-measure-your-runtime

An AgentCore Runtime **cold-start latency** benchmark: two deployment paths (zip, container),
each testable under two platform versions (Original Runtime `V1`, New Runtime
`V2`), and the container path testable at five image sizes. Derived from a
larger benchmark that also covered other runtimes and scenarios — those are
not here; this copy focuses purely on AgentCore's own paths and settings. See
`infrastructure/README.md` and `benchmarks/README.md` for the reasoning
behind each cut.

```
05-measure-your-runtime/
  base-agent/       the app under test (FastAPI echo server, no LLM call)
  infrastructure/   deploy scripts: build the image, create the AgentCore runtimes
  benchmarks/        load-ramp harness: drive traffic, write results-*.json, parse them
```

## Prerequisites

- AWS credentials in the shell (`aws sts get-caller-identity` should work).
- `aws` CLI, `docker`, `zip`. AgentCore Runtime images are **arm64**, so on an
  x86 machine `build-and-push.sh` needs QEMU/binfmt for the cross-build
  (`docker run --privileged --rm tonistiigi/binfmt --install arm64`); Docker
  Desktop and Apple Silicon already have it. Only the container path is
  affected — the zip path builds no image.
- `python3` (3.12+ — the zip deploy fetches 3.13
  arm64 wheels via `uv` regardless of your local interpreter's version, so
  the local one only needs to run the benchmark harness itself).
- `uv` on PATH (used by `deploy-agentcore-zip.sh` to build the zip artifact
  with `uv pip install`).
- A venv at `05-measure-your-runtime/.venv` with
  `pip install -r benchmarks/requirements.txt` (boto3 + urllib3). Both
  `infrastructure/`'s deploy scripts and `benchmarks/run_scenario.sh` look
  for this same venv path (`VENV_PYTHON` / `PYTHON` to override), so
  `agentcore_boto3.py` always runs against a real, current boto3 rather than
  whatever `python3` happens to resolve to on PATH.

## Cost of running this experiment

Short version: the Step 4 rehearsal (`RAMP_TARGET=100`) costs cents, not dollars
— do that first. A *full* scenario-1 run across both legs is the expensive one,
on the order of $10–16 at the current defaults (the table below measured $9.68 at
a shorter idle timeout than ships today). Read the rest of this section before
running Step 4 at its default 5,000-unit target — scenario 1 is designed to ramp
*until the account ceiling stops it*, and nothing in this sample deletes what it
creates for you.

**How AgentCore Runtime bills** (per the [official pricing page](https://aws.amazon.com/bedrock/agentcore/pricing/)):
microVM compute is billed per second (1-second minimum) on actual CPU
consumed and peak memory used up to that second — no CPU charge during I/O
wait, 128 MB minimum memory billing. A session's bill covers its *entire*
lifecycle (microVM boot, init, active processing, idle time, until
termination), not just the time it spends answering a request. Rates differ
by platform version:

| Dimension | `V1` (Original Runtime) | `V2` (New Runtime) |
|-----------|-------------------------:|---------------------:|
| vCPU-hour | $0.0895 | $0.1276 |
| GB-hour   | $0.00945 | $0.0169 |

Those per-unit rates are higher on `V2` — about 43% more per vCPU-hour and 79%
more per GB-hour — but a rate is only half of a bill; the other half is how many
units get metered. `V2` loads memory on demand rather than holding your whole
container image resident for the life of the session, so for the same agent it
meters **fewer GB-hours**. In this sample's own runs `V2`'s billed footprint
stayed flat at ~1.2 GB across every image size from 500 MB to 2 GB, while `V1`'s
grew with the image — ~2.7 GB at a 500 MB image, ~8.4 GB at 2 GB.

> **This harness is not a cost benchmark, and shouldn't be used as one.** It is
> built to isolate one thing: cold-start latency. Every design choice serves
> that — `base-agent` is an **echo server that makes no model call**, each
> session does exactly one invoke and is then held open rather than released,
> and the ramp runs until the account ceiling stops it. That produces clean
> cold-start numbers and a deliberately unrepresentative billing profile:
> almost every second you pay for here is a session sitting idle after its one
> request, which is not how a real agent spends its time. Use the figures in this
> section to predict *what this experiment will cost you*, not to model your own
> workload's bill.

With that said, the direction is worth understanding, because which way a
`V1`-vs-`V2` comparison lands depends on your image: the more of it your agent
never touches, the further the GB-hour saving outruns the higher rate. On the zip
path `base-agent` has no image at all — nothing to load on demand — so that leg
shows the rate increase with none of the offset, which makes it close to the
least favorable case for `V2` that can be constructed rather than a typical one.
Push the ladder in Step 1 (`200mb` through `2gb`) and compare against something
shaped like your own image before drawing any conclusion about your bill, and
measure a real agent doing real work before drawing a firm one.

Because billing runs for the whole session lifetime, **`AGENTCORE_IDLE_TIMEOUT`
(default 900s, in `infrastructure/common.sh`) is the single biggest cost lever
in this sample**: every session here does exactly one invoke and then sits idle,
billing its full footprint, until either the run's teardown phase stops it or
that timeout reaps it. From an actual full scenario-1 run (both legs, 200mb
image, `V2`):

| Leg | Sessions | Wall | GB-hours | vCPU-hours | Cost @ `V2` rates |
|-----|---------:|-----:|---------:|-----------:|-------------------:|
| zip | 5,000 | 202 s | 153.1 | 5.49 | $3.29 |
| container 200mb | 5,010 | 753 s | 320.5 | 7.64 | $6.39 |
| **total** (incl. a 100-unit rehearsal) | | | **473.7** | **13.1** | **$9.68** |

> **Those numbers were measured at a 300s idle timeout, and the default is now
> 900s** — raised so the container leg's ~750s ramp stops outliving its own
> sessions (see the comment in `common.sh`). Budget above the table
> accordingly: on the container leg the 2,836 sessions that used to be reaped
> mid-ramp now survive to teardown, so expect roughly 1.5–2× that leg's cost at
> the default. `AGENTCORE_IDLE_TIMEOUT=300 ./deploy-agentcore.sh` reproduces
> the cheaper (and less accurate) configuration those figures came from.

A run that finishes does not leave sessions idling for 900s — teardown calls
`StopRuntimeSession` on every one, and that teardown is in a `finally`, so a
single Ctrl-C still cleans up after itself. The timeout therefore only bites for
sessions that would otherwise have been reaped part-way through the ramp.

**Three cases do leave a fleet to age out on its own**, and they are where the
900s default costs you: `--no-teardown` / `NO_TEARDOWN=true`, which skips
teardown by design; anything that kills the process without unwinding, such as
`SIGTERM`/`kill`, a closed terminal, a crash, or losing the network mid-run; and a
second Ctrl-C while teardown is still working. In those cases sessions age out on
their own — up to `AGENTCORE_IDLE_TIMEOUT` idle, then `AGENTCORE_MAX_LIFETIME`
(1200s) as a hard cap. With a 5,000-session fleet that is up to ~20 minutes of
billed idle memory, and because those sessions still count against the account's
session ceiling, long enough that an immediate retry can stall at the ceiling on
the fleet you just abandoned. Either wait it out, or run with a shorter timeout
(`AGENTCORE_IDLE_TIMEOUT=300 ./deploy-agentcore.sh`) while you are still
iterating and likely to kill runs part-way.

Two rules of thumb from those numbers:

- On the **zip** path a session held roughly 1 GB resident (echo app, no LLM
  call), so **session-hours ≈ GB-hours** is a fair estimate there — and memory
  dominates the bill (~$8 of the ~$9.68 above). Do **not** carry that 1 GB over
  to the container path on `V1`, where the footprint scales with the image
  (measured ~2.7 GB at a 500 MB image, ~8.4 GB at 2 GB): estimate that leg as
  session-hours × the footprint you actually measure, or a big container run will
  cost several times what this rule of thumb predicts.
- **vCPU-hours billed were 2–3× the wall time actually spent serving
  requests** — cold-start/session-boot CPU is billed the same as
  request-serving CPU, so a slow cold start costs money, not just latency.

Idle sessions, not requests, are what you pay for. Start with the rehearsal
(`RAMP_TARGET=100`, already called out in Step 4) to see the shape and rough
cost of a run before committing to the full 5,000-unit ceiling test.

**Checking what you actually got billed.** The figures above came from
CloudWatch, not from an estimate: namespace `AWS/Bedrock-AgentCore`, metrics
`MemoryUsed-GBHours` and `CPUUsed-vCPUHours`, dimensions `Resource` = the full
runtime ARN and `Service` = `AgentCore.Runtime`. Usage lands in a burst when
sessions are torn down, so query at 5-minute resolution or finer and you can pick
individual runs out of the day. Two things worth knowing before you trust a
number you pull this way: a daily total lumps every run against that runtime
together (a rehearsal and a real run will silently add up), and dividing
GB-hours by session count mixes footprint with how long your sessions lived —
divide by session-hours instead if what you want is the footprint.

**Cleanup:** `infrastructure/cleanup.sh` deletes everything this sample can
create — the AgentCore runtimes (both legs, all sizes/versions you deployed),
the ECR repository/images, the S3 code bucket, and the two IAM roles. It
defaults to a dry run; pass `--yes` to actually delete:

```bash
cd infrastructure
./cleanup.sh          # dry run — lists what would be deleted
./cleanup.sh --yes    # actually deletes it
```

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
`AGENTCORE_PLATFORM_VERSION` (Original Runtime `V1`, New Runtime `V2`) — the
name matches the API's own `platformVersion` field. (`AGENTCORE_MANAGED_COMPUTE_VERSION`
still works as a deprecated alias, with a warning, if you have scripts using
the old name.) Setting it also names the runtime for you:

```bash
# zip, both versions
AGENTCORE_PLATFORM_VERSION=V1 ./deploy-agentcore-zip.sh
AGENTCORE_PLATFORM_VERSION=V2 ./deploy-agentcore-zip.sh
# -> runtimes ac_zip_x_lambda_bench_agent_V1 and ..._V2

# container, one size, both versions
IMAGE_SIZE=750mb AGENTCORE_PLATFORM_VERSION=V1 ./deploy-agentcore.sh
IMAGE_SIZE=750mb AGENTCORE_PLATFORM_VERSION=V2 ./deploy-agentcore.sh
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

Check `AWS_REGION` in `.env` matches the region you deployed into. It defaults
to `us-east-1` in both `.env.example` and `infrastructure/common.sh`, and
because `run_scenario.sh` sources `.env` with `set -a` it **overrides an
`AWS_REGION` already exported in your shell**. `run_scenario.sh` now compares it
against the region inside each ARN and refuses the leg on a mismatch rather
than failing every invoke.

`.env` can hold an ARN for every size/version you've deployed at once —
`AGENTCORE_ARN_750MB_V2`, `AGENTCORE_ZIP_ARN_V1`, and so on (see
`benchmarks/.env.example`). Pass `IMAGE_SIZE` and/or
`AGENTCORE_PLATFORM_VERSION` to `run_scenario.sh` to pick which one a
given run uses, and its output filename picks up the same suffix
automatically (plus `-t<RAMP_TARGET>` if you set one), so runs against
different sizes/versions/targets never overwrite each other:

```bash
IMAGE_SIZE=750mb AGENTCORE_PLATFORM_VERSION=V2 ./run_scenario.sh 1 agentcore
# -> results-scenario1-agentcore-750mb-V2.json
AGENTCORE_PLATFORM_VERSION=V1 ./run_scenario.sh 1 agentcore-zip
# -> results-scenario1-agentcore-zip-V1.json
```

## Step 4 — run a scenario

```bash
RAMP_TARGET=100 ./run_scenario.sh 1   # small rehearsal first — do this before a full run
                                      # -> results-...-t100.json, so it can't clobber the full run
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

## What good looks like

Reference numbers from one real run of this sample, so you have something to
compare your own output against. **Cold-start p75**, in seconds — every session
is created fresh and invoked exactly once, so every measurement is a cold
start:

| Deployment path | `V1` (Original Runtime) | `V2` (New Runtime) |
|-----------------|------------------------:|-------------------:|
| direct code (zip)   | 2.85 | **1.96** |
| container, 200mb    | 5.37 | **1.94** |
| container, 500mb    | 7.41 | **2.13** |
| container, 750mb    | 11.46 | **2.11** |
| container, 1gb      | 15.48 | **2.13** |
| container, 2gb      | 29.72 | **2.16** |

The shape of that table is the point, not the individual numbers: on `V1` cold
start scales with image size (a 10× bigger image cost ~5.5× the cold start),
while on `V2` all six paths land in a 1.9–2.2 s band. Two more results from the
same run, for context:

- **A warm invoke on an already-open session measured 152 ms** — about 13× faster
  than the best cold start here, so session reuse (`runtimeSessionId`) is still
  the biggest latency lever you control. Cold start is what you pay when you
  *can't* reuse.
- **A short run reads slower than a full one, and that is expected.** Cold p75
  is highest in the first few hundred sessions of a ramp and drifts down as it
  proceeds — measured in 500-session buckets it starts at ~2.2 s and settles
  near ~1.9 s. So a quick `RAMP_TARGET=500` try lands around 2.2 s rather than
  the 1.96 s above, which is the early ramp being sampled, not a regression.
  Reproduced on two separate days in `us-east-1`, both starting at 2.18 s.
- **Cold start held flat under concurrency.** Ramping the zip/`V2` leg to 5,000
  simultaneous sessions, cold p75 went *down* across the ramp — 2.19 s for the
  first 500 sessions, 1.84 s for the last 500 — with 0 throttled requests in
  5,027 attempts. The ramp ended at the account's session ceiling, not at a
  latency knee.

Caveats, because a benchmark number without them is worthless: one AWS account,
`us-east-1`, September 2026, `PYTHON_3_13` on arm64, the `base-agent` echo
server with **no model call** (so this measures the runtime, not an LLM), the
load generator running outside AWS so every figure carries the same
public-internet round trip, and 500 sessions per container point / 5,000 for
zip. Your absolute numbers will differ with region, image contents, account
history and where you run the client from — the `V1`-vs-`V2` *ratio* is the part
that should reproduce.

## End-to-end example

```bash
cd infrastructure
./build-and-push.sh
AGENTCORE_PLATFORM_VERSION=V2 ./deploy-agentcore-zip.sh   # prints the ARN

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
