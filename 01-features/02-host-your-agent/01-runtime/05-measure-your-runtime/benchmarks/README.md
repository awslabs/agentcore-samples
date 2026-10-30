# benchmarks (0_public)

Load-ramp harness for the two AgentCore Runtime legs (zip, container) set up
by `../infrastructure/`. No preview-stage switch — see `../README.md` for why.

## Files

| File | Purpose |
|------|---------|
| `load_ramp.py` | The actual load generator: ramps sessions/requests, records every result, writes `results-scenario<N>-<leg>.json`. Trimmed from the original benchmark to the two AgentCore legs only. |
| `agentcore.py` | AgentCore Runtime backend `load_ramp.py` drives (`invoke_agent_runtime`, session create/stop). |
| `common.py` | Shared `Result` record type. |
| `run_scenario.sh` | Orchestrates one scenario across both legs, with the drain delay between them. |
| `analyze_results.py` | Parses a `results-scenario*.json` file and prints a summary table — no AWS calls, just reads the JSON. |
| `.env.example` | Copy to `.env` and fill in the two runtime ARNs. |

## Scenarios

Only two, renumbered from the original benchmark's four (see `run_scenario.sh`
header for the full rationale):

- **1 — account ceiling.** Ramp new sessions until the account's own ceiling
  stops the climb. Time-to-first-response on each session is the cold-start
  number.
- **2 — warm until break.** Build a fleet of 100, then send an increasing
  warm-throughput staircase until latency or a real error rate says stop.

`load_ramp.py` also fully implements a third mode, a **per-unit** staircase
(`--per-unit-rps-steps` / `RAMP_PER_UNIT_RPS_STEPS`) — the original
benchmark's scenario 4 — but `run_scenario.sh` never sets it, so it exists
and works if you call `load_ramp.py` directly, without being one of the two
numbered scenarios above.

## Usage

```bash
cp .env.example .env    # fill in AGENTCORE_ARN / AGENTCORE_ZIP_ARN

./run_scenario.sh 1                  # scenario 1, both legs
./run_scenario.sh 2 agentcore        # scenario 2, container leg only
RAMP_TARGET=100 ./run_scenario.sh 1  # small rehearsal — do this first

# then read the results:
python3 analyze_results.py results-scenario1-agentcore.json
python3 analyze_results.py results-scenario*-*.json
```

### Multiple sizes/versions at once

`.env` can hold an ARN for every size/version you've deployed at the same
time (see `.env.example`) — `IMAGE_SIZE` and
`AGENTCORE_PLATFORM_VERSION` (`AGENTCORE_MANAGED_COMPUTE_VERSION` still works
as a deprecated alias) pick which one a run actually uses:

```bash
IMAGE_SIZE=750mb AGENTCORE_PLATFORM_VERSION=V2 ./run_scenario.sh 1 agentcore
# -> reads AGENTCORE_ARN_750MB_V2 from .env, writes results-scenario1-agentcore-750mb-V2.json

AGENTCORE_PLATFORM_VERSION=V1 ./run_scenario.sh 1 agentcore-zip
# -> reads AGENTCORE_ZIP_ARN_V1, writes results-scenario1-agentcore-zip-V1.json
```

If the exact combination isn't in `.env`, it falls back to a less specific
name, then to the plain `AGENTCORE_ARN`/`AGENTCORE_ZIP_ARN` — so this is
fully backward compatible with a `.env` that only has the plain names. If
nothing matches at all, that leg is skipped with a clear message rather than
failing the whole run.

If a run stops with `stop_reason: "generator-limited"`, the load generator
itself, not AgentCore, was the bottleneck — raise `RAMP_STEP_WORKERS` (see
`.env.example`) and re-run.
