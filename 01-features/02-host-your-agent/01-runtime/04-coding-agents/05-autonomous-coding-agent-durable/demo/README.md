# Demo Console

Live visualization of the autonomous coding-agent workflow on AgentCore. Architecture
diagram + stage timeline + per-component CloudWatch logs + live coding-agent reasoning,
all driven off the deployed system on account 123456789012.

## Run it

```bash
bash demo/start_demo.sh        # one command: ensures venv, checks runtimes, syncs SSM, starts server
# → open http://localhost:8792
```

`start_demo.sh` ensures the python3.13 venv + deps, verifies AWS creds, checks all four
runtimes are READY, (re)publishes their ARNs to SSM, then launches the console. Override
the port with `PORT=9000 bash demo/start_demo.sh`.

## Runtime ARNs come from SSM (no orchestrator redeploy on recreate)

The orchestrator resolves runtime ARNs at invocation time from SSM Parameter Store
(`/cagent/runtime/{coding_agent,sandbox,sandbox_swift,evaluator}`), with a 60s cache. So when
you rebuild an image and recreate a runtime (which changes its ARN), `deploy/30_create_runtime.sh`
updates the SSM parameter automatically — and the orchestrator picks up the new ARN within a
minute. **No Lambda redeploy, no EventBridge repoint.** (Falls back to env vars if SSM is
unavailable, which keeps unit tests hermetic.)

Click **Fire Ticket 1** → watch admission → hydrate → recall → coder (SUSPENDED $0)
→ test gate → review → finalize light up. Click any component box for its live,
ticket-scoped CloudWatch logs; click the **Coding Agent** box for its live
reasoning + tool-call stream. Then **Fire Ticket 2** on the same repo to show the
recall step surfacing lessons written by ticket 1.

## Demo hygiene (important)

- **Use a FRESH ticket id for each live run** (RAINBOW-1, then RAINBOW-3, RAINBOW-5…).
  Each ticket gets its own `/mnt/shared/<id>/` work dir, so a fresh id = clean repo.
- **Do NOT `aws s3 rm work/<id>/`** to reset — the sandbox holds that dir on the NFS
  mount, and deleting the S3 objects underneath leaves orphan marker files that can
  confuse hydration. Just use a new ticket id.
- To reset the **memory** learning-story baseline, run `python demo/clear_memory.py`
  (clears the `lessons/rainbow` + `lessons/shared` namespaces via the data plane and
  verifies the recall path is empty), or launch with `CLEAR_MEMORY=1 bash demo/start_demo.sh`.

## Security note

`serve.py` runs **unauthenticated on localhost** using your local AWS credentials. Anyone
with access to the machine (or any process that can reach `127.0.0.1:<port>`) can read the
exposed S3 progress state and CloudWatch logs and **fire tickets** against the live account.
It is intended as a local presenter tool only — do **not** bind it to a public interface or
expose the port. For anything shared, put it behind an authenticating reverse proxy.

## Endpoints (serve.py)

| Route | Purpose |
|---|---|
| `/` | the visualization page |
| `/fire?ticket=ID` | emit the ticket → durable orchestrator |
| `/state?ticket=ID` | stage timeline (from s3://bucket/demo-progress/ID.json) |
| `/logs?component=X&ticket=ID` | prettified ticket-scoped CW log tail + console deep-link |
| `/reasoning?ticket=ID` | coding-agent narration + tool-call stream (from CW logs) |
| `/memory?repo=rainbow` | current per-repo lessons in AgentCore Memory |
| `/config` | account/region + per-component console deep-links |

## How stages are sourced

The durable orchestrator (`orchestrator/handler.py`) calls `_emit_stage()` inside each
durable step, appending to `s3://<bucket>/demo-progress/<ticket>.json`. Because step
bodies are cached on replay, each stage is emitted exactly once → a monotonic timeline,
including the **SUSPENDED** marker (emitted in the callback submitter) and **resumed**
(emitted when the test-gate step runs after the callback).
