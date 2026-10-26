# Long-running async jobs on a CapacityProvider

An agent that starts a job, returns immediately, and keeps working in the
background for minutes — while its EC2 instance stays alive **because the agent
says it is busy**.

This is the sample about `HealthyBusy`. On a CapacityProvider, idle instances are
reclaimed to save you money. An agent doing background work looks idle from the
outside: no invokes are arriving. If it does not say otherwise, the instance is
taken away mid-job. The `/ping` handler is how it says otherwise.

## The contract, in six lines

```python
@app.ping
def ping() -> PingStatus:
    if app.get_async_task_info().get("active_count", 0) > 0:
        return PingStatus.HEALTHY_BUSY
    return PingStatus.HEALTHY
```

`HealthyBusy` means *alive, working, do not reclaim me*. `Healthy` means *alive,
idle, reclaim me when the timer expires*. The service polls `/ping`; your answer
drives the idle timer.

Registering work is two calls, and the completion **must** be in a `finally`:

```python
task_id = app.add_async_task("analysis", {"job_id": job_id})
try:
    ...                                   # the actual long job
finally:
    app.complete_async_task(task_id)      # never skip this
```

If a task is never completed, `active_count` never returns to zero, `/ping` answers
`HealthyBusy` forever, and the instance is never reclaimed. That is a bill, not a
bug.

## Files

| File | What it does |
|---|---|
| [agent/agent.py](agent/agent.py) | The agent: `start_job`, `check_job`, `list_jobs`, `report_session`, and the `@app.ping` handler. |
| [deploy.py](deploy.py) | IAM → zip→S3 → CapacityProvider (60 s idle timeout) → runtime. |
| [invoke.py](invoke.py) | The experiment below, plus `--session-test`. |
| [cleanup.py](cleanup.py) | Deletes everything, including the EC2 fleet. |

Zip only, no helper module and no service model to install. Three clients:
`bedrock-agentcore-control` for the control plane, `bedrock-agentcore` for the data
plane, and plain `ec2` for the observation half of the experiment. No `endpoint_url`
anywhere — endpoints resolve from the region.

## The experiment

`deploy.py` sets `idleInstanceTimeout=60` — the API minimum. Absurd for production,
deliberate here: it makes the contract observable in minutes instead of hours.

Then: start a 5-minute job, **stop invoking entirely**, and watch EC2 from the
outside with `describe-instances`. Five minutes is five idle timeouts. If the
instance is still there, only the `HealthyBusy` ping can explain it.

Watching via the EC2 API matters. Polling the agent would itself be activity, and
would prove nothing.

### One flag the experiment depends on

`invoke.py` passes `IncludeManagedResources=True` on every `DescribeInstances` call,
and here that flag is the difference between a demonstration and a false negative.

CapacityProvider instances are [EC2 managed
resources](../01-basic-http-zip-and-container/README.md#your-instances-are-hidden-from-describe-instances-by-default),
so they are hidden from `DescribeInstances` by default. Without the flag the polling
loop returns an empty list for a fleet that is very much running, step 3 prints
`instances: none` all the way through, and the whole thing reads as *HealthyBusy did
not work* — the one observation the sample exists to make, inverted silently, with
no error anywhere.

`deploy.py` therefore also sets the account's default visibility to `visible`, so
the console and your own `aws ec2 describe-instances` agree with what `invoke.py`
reports. That is an **account-wide** change and `cleanup.py` does not revert it. Set
`CP_MANAGED_VISIBILITY=skip` to leave the account alone; `invoke.py` still works,
because it asks for managed resources explicitly.

### Measured result

One live run on `m6g.large` with `idleInstanceTimeout=60`. Timestamps are from the
ASG scaling activity and the agent's own CloudWatch log stream — not from the
client:

| Time (UTC) | Event | Source |
|---|---|---|
| 10:31:42 | instance `i-091ebf9b0f341fcef` launched | ASG activity |
| 10:34:05 | `Async task started: analysis (ID: 9201240371187683104)` | agent log |
| 10:34:08 | first invoke returns — **146 s cold start** | agent log |
| 10:34:09 | last invoke of the run (`status`, 0.001 s) | agent log |
| *(no invokes for the next 5 minutes)* | | |
| 10:39:05 | `Async task completed: analysis (Duration: 300.01s)` | agent log |
| 10:42:05 | ASG begins terminating the instance | ASG activity |

The number that matters:

> **296 seconds with zero invokes, against a 60 second idle timeout — 4.9×.**

The instance was not reclaimed at 10:35:09 as an idle instance would have been. It
survived to 10:39:05, finished the job (`Duration: 300.01s`, matching the requested
10 × 30 s exactly), and was terminated three minutes later once `/ping` had gone
back to `Healthy`. Total lifetime 623 s for a 300 s job.

Two things to take from that beyond the headline:

* The reclamation lag after the job ended was ~180 s, not 60 s. Treat
  `idleInstanceTimeout` as the point at which an instance becomes *eligible* for
  reclamation, not a stopwatch — the poll interval and the ASG action add to it.
* The instance passed briefly through EC2 state `stopped` before `terminated`, and
  its 16 GiB gp3 root volume was deleted with it. Do not treat a `stopped` sighting
  as a leak; re-poll before concluding anything.

## Verifying it yourself

```bash
python deploy.py           # ~3-4 min
python invoke.py           # the experiment above, ~11 min
python invoke.py --session-test
python cleanup.py
```

`invoke.py` polls the agent for `ping_status` only at the start, then goes quiet. A
run that works looks like this:

```
── 2. Confirm the agent reports HealthyBusy ──
{ "process_id": "fc5f6c35",
  "hostname": "ip-172-31-35-53.<region>.compute.internal",
  "active_tasks": 1, "ping_status": "HealthyBusy",
  "jobs": { "job-3fea7204": { "status": "queued", "step": 0, "total_steps": 10 } } }

── 3. Stop invoking. Watch EC2 only. ──
  t+ 0.0 min   instances: i-091ebf9b0f341fcef (running)
  ...
  t+ 5.6 min   instances: i-091ebf9b0f341fcef (running)
```

You can confirm the same thing without the SDK at all:

```bash
aws logs get-log-events --region "$AWS_REGION" \
  --log-group-name /aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT \
  --log-stream-name <your-session-id> --start-from-head \
  --query 'events[?contains(message, `Async task`)].message' --output text

aws autoscaling describe-scaling-activities --region "$AWS_REGION" \
  --auto-scaling-group-name bedrock-agentcore-runtime-instances-<cp-id> \
  --query 'Activities[].[StartTime,Description]' --output text
```

Step 4 of `invoke.py` falls back to reading that log group if the invoke path is
unavailable, because the job's outcome is recorded server-side either way.

## Session id → instance

`agent.py` generates `PROCESS_ID` once at import, so every response identifies the
process that produced it:

```python
PROCESS_ID = uuid.uuid4().hex[:8]
```

Same `process_id` across invokes ⇒ same process, same instance. A different
`process_id` ⇒ you are on another machine, and **none of your in-memory job state is
there**. In the measured run above, every invoke reported `process_id: fc5f6c35` on
host `ip-172-31-35-53` — the async task and the invokes that polled it all landed on
one instance, which is why the in-memory job dict worked.

### Do not treat that as a guarantee

Observing one process id for one session is not the service promising affinity, and
**the promise does not hold**. Sample 2 tested it directly: seven `whoami` calls on
a *single* `runtimeSessionId` came back from seven different processes on seven
different hosts, including calls two seconds apart. See [Sample 2's
README](../02-basic-mcp/README.md#the-load-balancer-is-real-one-session-two-instances).

So a session id routes a request; it does not pin one. What made the run above work
is that `HealthyBusy` kept the *serving instance* alive while its own async task ran
on it — not a routing guarantee that later invokes return to it.

The safe reading: **in-memory state is an optimisation, never a correctness
requirement.** If losing it would break the workload, put it in AgentCore Memory or
your own store. `--session-test` demonstrates the easy half of this — a *different*
session id cannot see the first session's jobs — but the harder half is that the
*same* session id may not either.

## Session and instance timeouts are different things

```python
# on the CapacityProvider — when to reclaim the EC2 instance
"lifecycleConfiguration": {"idleInstanceTimeout": 60, "maxLifetime": 86400}

# on the runtime — when to discard an idle session
"lifecycleConfiguration": {"idleRuntimeSessionTimeout": 60, "maxLifetime": 86400}
```

`idleInstanceTimeout` governs the **instance**; `idleRuntimeSessionTimeout` governs
the **session**. An instance hosts sessions, so a session can end while the instance
persists for another session. Both fields are min 60 / max 1209600 s (14 days), and
`maxLifetime` must be ≥ the idle timeout.

**Set `maxLifetime` comfortably above your longest job.** By its name and position
it is a hard ceiling on instance age, which `HealthyBusy` would not override — but
this run did not test that (`maxLifetime` was 86400 s and the job took 300 s). If
your jobs can approach your `maxLifetime`, measure it before relying on it.

For production, drop the theatrics: `idleInstanceTimeout` back to the 900 s default
(or higher, to amortise the cold start), and `maxLifetime` above your worst-case job
duration.

## Prerequisites

Same as [Sample 1](../01-basic-http-zip-and-container/README.md): credentials for a
**role** that can create EC2 instances, IAM roles and S3 buckets, `AWS_REGION` set
(there is no default), a boto3 with the CapacityProvider APIs, `uv` on PATH, and
Bedrock access to the model. That README also covers the execution-role trust policy
and the [transient
`InternalServerException`](../01-basic-http-zip-and-container/README.md#transient-errors-on-invoke)
on `InvokeAgentRuntime` — both apply here unchanged. `invoke.py` retries fast
failures with exponential backoff, because they arrive in windows lasting minutes
rather than as isolated blips.

`deploy.py` also shares the S3 bucket and the runtime IAM role with the other
samples, so `cleanup.py` here removes them from under the others too — harmless,
since each `deploy.py` recreates them.

Configuration, all optional except the region:

| Variable | Default | Notes |
|---|---|---|
| `AWS_REGION` | *none — required* | The region. No fallback default. |
| `MODEL_ID` | `global.anthropic.claude-sonnet-4-5-20250929-v1:0` | Passed to the agent as a runtime env var, so switching models needs no rebuild. |
| `CP_OS` | `LINUX_ARM64` | `LINUX_ARM64` or `LINUX_X86_64`. Drives the wheel platform. |
| `CP_INSTANCE_TYPE` | `m6g.large` | Must match `CP_OS`. |
| `CP_SUBNET_ID` / `CP_SECURITY_GROUP_ID` | default VPC's first subnet + default SG | Set both together to place the fleet in a VPC of your choosing. |
| `CP_OPERATOR_ROLE_ARN` | the caller's own role | Override only if the calling role cannot be the operator role. |
| `CP_MANAGED_VISIBILITY` | *unset* | Set to `skip` to leave managed resource visibility untouched. |
| `IDLE_INSTANCE_TIMEOUT` / `IDLE_SESSION_TIMEOUT` | `60` | Seconds, and **60 is the API minimum**. Deliberately low here so the experiment runs in minutes. |
| `MAX_LIFETIME` | `86400` | Seconds, ceiling 1209600 (14 days). Must be ≥ `IDLE_INSTANCE_TIMEOUT`. |
| `TOTAL_STEPS` / `SECONDS_PER_STEP` | `10` / `30` | The job's length: 10 × 30 s = 5 minutes, five idle timeouts. |

## Cost warning

Real EC2 instances in your account, billed while they run — and a stuck async task
keeps one running indefinitely. Run `python cleanup.py`, then verify:

```bash
aws ec2 describe-instances --include-managed-resources --region "$AWS_REGION" \
  --filters "Name=tag:bedrock-agentcore:capacity-provider-id,Values=<cp-id>" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output table
```

`--include-managed-resources` is [not
optional](../01-basic-http-zip-and-container/README.md#your-instances-are-hidden-from-describe-instances-by-default):
without it a still-running fleet prints as an empty table, which reads exactly like
"cleanup worked".

No production data, no production workloads, and not in an account where a service
bug creating or deleting EC2 instances could affect real workloads.
