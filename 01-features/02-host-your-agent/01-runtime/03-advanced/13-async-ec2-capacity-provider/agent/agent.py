"""
A long-running async agent on an AgentCore CapacityProvider.

WHY A CAPACITYPROVIDER FOR THIS
-------------------------------
Serverless AgentCore Runtime caps how long a session can live. A
CapacityProvider does not have that ceiling: `maxLifetime` goes up to
1209600 seconds (14 days), so an agent can keep working on a job for hours
or days while you poll it.

That only works if the platform knows you are still busy. Two lifecycle
knobs decide whether your instance survives:

  * `idleInstanceTimeout` (on the CapacityProvider) — how long an instance may
    sit with every agent idle before it is terminated.
  * `idleRuntimeSessionTimeout` (on the runtime) — the same idea for a session.

"Idle" is not "no HTTP requests". It is what YOUR `/ping` handler reports. Return
`HealthyBusy` and the platform keeps the instance alive; return `Healthy` and the
idle clock runs. That is the contract this sample demonstrates:

    @app.ping
    def ping():
        return PingStatus.HEALTHY_BUSY if _active else PingStatus.HEALTHY

The job here is deliberately dull — a chunked "analysis" that sleeps between
steps — because the interesting part is the lifecycle, not the work. Each step
records progress under /tmp on the instance, so you can watch a single long job
advance across many short invokes.

SESSION PARAMETERS
------------------
Every `InvokeAgentRuntime` carries a `runtimeSessionId`, which routes the request
and scopes the log stream. A different session id gets a different instance, with
none of this instance's state.

What a session id does NOT do is guarantee you come back to the same instance:
measured on a deployed runtime, repeated calls under one session id were served
by several different processes on different hosts. So treat local state as a
fast path, not a source of truth — `report_session()` returns the instance
identity next to the job state precisely so you can see when you have moved.
"""

import json
import os
import platform
import threading
import time
import uuid
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp, PingStatus
from strands import Agent, tool

app = BedrockAgentCoreApp()

MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")

# How many steps a job has, and how long each takes. Small by default so a demo
# finishes while you watch; raise SECONDS_PER_STEP to hold an instance for hours.
TOTAL_STEPS = int(os.environ.get("TOTAL_STEPS", "10"))
SECONDS_PER_STEP = int(os.environ.get("SECONDS_PER_STEP", "30"))

# Job state lives on the instance's local disk, which outlives a single invoke
# but is not shared between instances — a later invoke routed elsewhere will not
# find it. Fine for a demo; use AgentCore Memory or a store for real state.
STATE_DIR = Path(os.environ.get("STATE_DIR", "/tmp/async-jobs"))
STATE_DIR.mkdir(parents=True, exist_ok=True)

# The instance's identity, fixed at process start. If two invokes report the
# same value they ran on the same instance, in the same process.
PROCESS_ID = uuid.uuid4().hex[:8]
STARTED_AT = time.time()

_lock = threading.Lock()


def _job_file(job_id: str) -> Path:
    return STATE_DIR / f"{job_id}.json"


def _read_job(job_id: str) -> dict | None:
    try:
        return json.loads(_job_file(job_id).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_job(job: dict) -> None:
    _job_file(job["job_id"]).write_text(json.dumps(job, indent=2))


def _run_job(job_id: str, task: str) -> None:
    """
    The long-running work, on a background thread.

    The `add_async_task` / `complete_async_task` pair is what flips the ping
    status. Note the `finally` — if this thread dies, the task MUST still be
    completed, or the agent reports HealthyBusy forever and the instance is
    never reclaimed. That is a real bill.
    """
    task_ref = app.add_async_task("analysis", {"job_id": job_id})
    try:
        for step in range(1, TOTAL_STEPS + 1):
            time.sleep(SECONDS_PER_STEP)
            with _lock:
                job = _read_job(job_id) or {}
                job.update(
                    job_id=job_id,
                    task=task,
                    status="running",
                    step=step,
                    total_steps=TOTAL_STEPS,
                    findings=job.get("findings", [])
                    + [f"step {step}: processed chunk {step} of {TOTAL_STEPS}"],
                    process_id=PROCESS_ID,
                    hostname=platform.uname().node,
                    updated_at=time.time(),
                )
                _write_job(job)
        with _lock:
            job = _read_job(job_id) or {}
            job.update(status="complete", completed_at=time.time())
            _write_job(job)
    except Exception as exc:  # noqa: BLE001 — record the failure, never leak the task
        with _lock:
            job = _read_job(job_id) or {"job_id": job_id}
            job.update(status="failed", error=str(exc))
            _write_job(job)
    finally:
        app.complete_async_task(task_ref)


@tool
def start_job(task: str) -> str:
    """Start a long-running analysis job in the background and return its id."""
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    _write_job(
        {
            "job_id": job_id,
            "task": task,
            "status": "queued",
            "step": 0,
            "total_steps": TOTAL_STEPS,
            "findings": [],
            "process_id": PROCESS_ID,
            "hostname": platform.uname().node,
            "started_at": time.time(),
        }
    )
    threading.Thread(target=_run_job, args=(job_id, task), daemon=True).start()
    eta = TOTAL_STEPS * SECONDS_PER_STEP
    return (
        f"Started {job_id}: {task}\n"
        f"{TOTAL_STEPS} steps x {SECONDS_PER_STEP}s ≈ {eta}s total.\n"
        f"The agent now reports HealthyBusy, so the instance will not be "
        f"reclaimed while this runs."
    )


@tool
def check_job(job_id: str) -> str:
    """Report the progress of a job started earlier in this session."""
    job = _read_job(job_id)
    if job is None:
        known = sorted(p.stem for p in STATE_DIR.glob("job-*.json"))
        return (
            f"No job {job_id} on this instance. Known jobs here: {known or 'none'}.\n"
            "If you expected one, you are probably on a different instance — "
            "check that you reused the same runtimeSessionId."
        )
    return json.dumps(
        {
            "job_id": job["job_id"],
            "status": job["status"],
            "progress": f"{job.get('step', 0)}/{job.get('total_steps')}",
            "findings": job.get("findings", [])[-3:],
            "process_id": job.get("process_id"),
        },
        indent=2,
    )


@tool
def list_jobs() -> str:
    """List every job on this instance, with its status."""
    jobs = []
    for path in sorted(STATE_DIR.glob("job-*.json")):
        job = _read_job(path.stem) or {}
        jobs.append(
            f"{job.get('job_id')}: {job.get('status')} "
            f"({job.get('step', 0)}/{job.get('total_steps')})"
        )
    return "\n".join(jobs) if jobs else "No jobs on this instance yet."


@tool
def report_session() -> str:
    """Report which instance and process is serving this session."""
    info = app.get_async_task_info()
    return "\n".join(
        [
            f"process_id:     {PROCESS_ID}",
            f"hostname:       {platform.uname().node}",
            f"architecture:   {platform.uname().machine}",
            f"process_uptime: {time.time() - STARTED_AT:.0f}s",
            f"active_tasks:   {info.get('active_count', 0)}",
            f"ping_status:    {'HealthyBusy' if info.get('active_count') else 'Healthy'}",
            f"jobs_on_disk:   {len(list(STATE_DIR.glob('job-*.json')))}",
        ]
    )


# ── The lifecycle contract ────────────────────────────────────────────────────
# This is the whole point of the sample. AgentCore polls /ping. While this
# returns HealthyBusy the instance is considered in use and the idle timers do
# not fire, however long the job takes. The SDK tracks that for us: any
# outstanding add_async_task makes active_count non-zero.
@app.ping
def ping() -> PingStatus:
    if app.get_async_task_info().get("active_count", 0) > 0:
        return PingStatus.HEALTHY_BUSY
    return PingStatus.HEALTHY


agent = Agent(
    model=MODEL_ID,
    tools=[start_job, check_job, list_jobs, report_session],
    system_prompt=(
        "You manage long-running analysis jobs on an AWS Bedrock AgentCore "
        "CapacityProvider instance. Use start_job to begin work, check_job to "
        "report progress, list_jobs to enumerate, and report_session to show "
        "which instance is serving this session. Be concise and always include "
        "the exact tool output."
    ),
)


@app.entrypoint
def invoke(payload):
    """
    HTTP entrypoint.

    Accepts a plain prompt for the agent, and also a small direct protocol
    (`{"action": "status"}`) so a poller can check progress without paying for
    a model round-trip on every poll.
    """
    action = payload.get("action")
    if action == "status":
        info = app.get_async_task_info()
        jobs = {}
        for path in sorted(STATE_DIR.glob("job-*.json")):
            job = _read_job(path.stem) or {}
            jobs[job.get("job_id", path.stem)] = {
                "status": job.get("status"),
                "step": job.get("step"),
                "total_steps": job.get("total_steps"),
            }
        return {
            "process_id": PROCESS_ID,
            "hostname": platform.uname().node,
            "active_tasks": info.get("active_count", 0),
            "ping_status": "HealthyBusy" if info.get("active_count") else "Healthy",
            "jobs": jobs,
        }

    prompt = payload.get("prompt", "Report which instance is serving this session.")
    result = agent(prompt)
    return {"result": result.message, "process_id": PROCESS_ID}


if __name__ == "__main__":
    app.run()
