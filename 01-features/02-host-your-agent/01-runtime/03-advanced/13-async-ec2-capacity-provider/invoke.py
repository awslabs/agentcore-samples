"""
Prove that HealthyBusy is what keeps a CapacityProvider instance alive.

Run `python deploy.py` first.

    python invoke.py              # the full demonstration (~8 min)
    python invoke.py --session-test   # two session ids share no state
    python invoke.py --prompt "list jobs"

THE EXPERIMENT
--------------
The CapacityProvider was created with `idleInstanceTimeout=60`. So an instance
with nothing to do is reclaimed about a minute after its last invoke.

  1. Start a job that takes ~5 minutes (10 steps x 30s).
  2. Then STOP INVOKING, and just watch EC2 from the outside.
  3. Five minutes is five idle timeouts. If the instance is still there, the
     only thing that saved it is the agent's own /ping handler returning
     HealthyBusy while the job runs.
  4. When the job finishes, ping goes back to Healthy — and the instance is
     reclaimed shortly after.

The EC2 polling deliberately uses the EC2 API, not the agent, so that watching
does not itself count as activity.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "cp_config.json"
SESSION_MIN_LEN = 33


def data_client(region: str | None):
    """
    The data-plane client, straight from boto3 — but not with default settings.

    Note the service name: the data plane is `bedrock-agentcore`, a different
    service from the `bedrock-agentcore-control` client deploy.py uses.

    The long read timeout is NOT optional. A cold invoke waits for an EC2 instance
    to be launched, booted and seeded with your artifact before your agent sees
    the request — 146 s in the measured run in the README. botocore's default read
    timeout is 60 s, which would abort a perfectly healthy cold start.

    Retries are disabled on purpose, and it matters more here than in the other
    samples. InvokeAgentRuntime is not idempotent, so a botocore-level retry
    during a slow cold start would open a SECOND session on a SECOND instance —
    which in this sample would silently start a SECOND five-minute job and ruin
    the measurement. The retry loop in `invoke()` below is the deliberate one; it
    reuses the same session id.
    """
    region = (
        region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or boto3.Session().region_name
    )
    if not region:
        sys.exit("No AWS region configured. Set AWS_REGION.")
    return boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(
            read_timeout=900,
            connect_timeout=30,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    )


def new_session_id(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex}".ljust(SESSION_MIN_LEN, "0")[:64]


def extract_text(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:4000]
    result = payload.get("result", payload)
    if isinstance(result, dict) and "content" in result:
        parts = [
            b["text"] for b in result["content"] if isinstance(b, dict) and "text" in b
        ]
        if parts:
            return "\n".join(parts)
    return json.dumps(result, indent=2, default=str)


def invoke(data, arn: str, session_id: str, payload: dict, attempts: int = 6) -> tuple[str, float]:
    """
    Invoke the agent, retrying the transient `InternalServerException`.

    That error comes back in 1–3 seconds, before any instance is launched — see
    samples/01-basic-http-zip-and-container/README.md → "Transient errors on invoke".
    A slow call is a real cold start and is left alone; only fast failures are
    retried, and always on the SAME session id so we stay on one instance.

    Backoff grows because the failures arrive in windows lasting minutes, not as
    isolated blips — retrying every 15s for a minute tends to spend all its
    attempts inside the same window.
    """
    started = time.time()
    last = None
    for attempt in range(1, attempts + 1):
        try:
            response = data.invoke_agent_runtime(
                agentRuntimeArn=arn,
                runtimeSessionId=session_id,
                payload=json.dumps(payload).encode(),
                contentType="application/json",
            )
            return response["response"].read().decode(), time.time() - started
        except Exception as exc:  # noqa: BLE001 — transient service-side errors
            name = type(exc).__name__
            if "InternalServerException" not in name and "RuntimeClientError" not in name:
                raise
            last = exc
            if attempt < attempts:
                delay = min(15 * 2 ** (attempt - 1), 120)
                print(f"    ({name} — transient, retry {attempt}/{attempts - 1}"
                      f" in {delay}s)", flush=True)
                time.sleep(delay)
    raise last


def instances_for(ec2, cp_id: str) -> list[dict]:
    """
    Live instances belonging to this CapacityProvider, read from EC2 directly.

    `IncludeManagedResources=True` is load-bearing, and this is the one place in
    the samples where omitting it does not just hide information but inverts the
    conclusion. Since EC2 Managed Resource Visibility shipped (April 2026),
    service-provisioned instances are hidden from `DescribeInstances` by default —
    and a CapacityProvider's instances are service-provisioned. Without the flag
    this function returns `[]` for a fleet that is very much running, so the
    experiment below would print "instances: none" throughout and read as
    "HealthyBusy did not work" when in fact nothing was ever visible.

    Hidden instances still run and still bill. `deploy.py` sets the account to
    visible so the console agrees with this, but the flag makes this call correct
    either way.
    """
    resp = ec2.describe_instances(
        IncludeManagedResources=True,
        Filters=[
            {"Name": "tag:bedrock-agentcore:capacity-provider-id", "Values": [cp_id]},
            {"Name": "instance-state-name", "Values": ["pending", "running"]},
        ],
    )
    out = []
    for reservation in resp["Reservations"]:
        for inst in reservation["Instances"]:
            tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
            out.append(
                {
                    "id": inst["InstanceId"],
                    "state": inst["State"]["Name"],
                    "session": tags.get("bedrock-agentcore:runtime-session-id"),
                }
            )
    return out


def task_log_lines(config: dict, session_id: str) -> list[str]:
    """
    The agent's async-task log lines for one session, read from CloudWatch.

    The SDK logs "Async task started/completed" around add_async_task and
    complete_async_task, with the measured duration. That is proof the job ran
    to completion on the instance, independent of whether any invoke succeeded.
    """
    logs = boto3.client("logs", region_name=config["region"])
    group = f"/aws/bedrock-agentcore/runtimes/{config['runtimes']['zip']['id']}-DEFAULT"
    try:
        events = logs.get_log_events(
            logGroupName=group, logStreamName=session_id, startFromHead=True
        )["events"]
    except logs.exceptions.ResourceNotFoundException:
        return [f"no log stream {session_id} in {group}"]
    out = []
    for event in events:
        message = event["message"]
        if "Async task" in message:
            out.append(message.strip())
    return out or ["no async-task lines yet"]


def demo(config: dict) -> None:
    data = data_client(config.get("region"))
    ec2 = boto3.client("ec2", region_name=config["region"])
    arn = config["runtimes"]["zip"]["arn"]
    cp_id = config["capacityProviderId"]
    idle = config["idleInstanceTimeout"]
    job_seconds = config["totalSteps"] * config["secondsPerStep"]

    session_id = new_session_id("asyncjob")
    print(f"session               {session_id}")
    print(f"idleInstanceTimeout   {idle}s")
    print(f"job duration          ~{job_seconds}s  ({job_seconds // idle}x the idle timeout)\n")

    print("── 1. Start the job ─────────────────────────────────────────────────")
    print("The first invoke is a cold start: it waits for an EC2 instance to be")
    print("launched and seeded. Minutes, not seconds.\n", flush=True)
    body, elapsed = invoke(
        data, arn, session_id, {"prompt": "Start a job analysing the Q3 support backlog."}
    )
    print(f"[{elapsed:.0f}s] {extract_text(body)}\n")

    print("── 2. Confirm the agent reports HealthyBusy ──────────────────────────")
    body, elapsed = invoke(data, arn, session_id, {"action": "status"})
    status = json.loads(body)
    print(json.dumps(status, indent=2))
    if status.get("ping_status") != "HealthyBusy":
        print("\n!! Expected HealthyBusy while a job runs. The idle timer is running.")
    print()

    print("── 3. Stop invoking. Watch EC2 only. ─────────────────────────────────")
    print(f"No invokes from here on. With a {idle}s idle timeout, an idle instance")
    print("would be gone within about a minute. Anything that survives is being")
    print("kept alive by the agent's HealthyBusy ping.\n", flush=True)

    deadline = time.time() + job_seconds + 60
    while time.time() < deadline:
        live = instances_for(ec2, cp_id)
        mins = (job_seconds + 60 - (deadline - time.time())) / 60
        summary = ", ".join(f"{i['id']} ({i['state']})" for i in live) or "none"
        print(f"  t+{mins:4.1f} min   instances: {summary}", flush=True)
        time.sleep(30)

    print("\n── 4. Job should be done; check progress and state ───────────────────")
    # The agent's own log is the authoritative record of the async task, and it
    # does not depend on the invoke path working. If the data plane is in one of
    # its InternalServerException windows we read the log instead, rather
    # than losing the result of a demo that already ran correctly server-side.
    final = None
    try:
        body, elapsed = invoke(data, arn, session_id, {"action": "status"})
        final = json.loads(body)
        print(json.dumps(final, indent=2))
    except Exception as exc:  # noqa: BLE001
        print(f"  invoke unavailable ({type(exc).__name__}) — reading CloudWatch instead.")
        for line in task_log_lines(config, session_id):
            print(f"  {line}")

    print("\n── 5. Did both invokes land on the same instance? ─────────────────────")
    if final:
        same = status.get("process_id") == final.get("process_id")
        print("process_id is generated once per process at start-up, so matching ids")
        print("mean both invokes hit the same process — and a mismatch means the")
        print("second one was routed elsewhere, where the in-memory dict is empty.")
        print("A session id routes a request; it does not pin one.\n")
        print(f"  step 2 process_id: {status.get('process_id')}")
        print(f"  step 4 process_id: {final.get('process_id')}")
        print(f"  same instance:     {same}")
        if not same:
            print("\n  Moved instances mid-session — expected behaviour, not a bug.")
            print("  The job still completed; CloudWatch is the durable record.")
    else:
        print("  Skipped — needs the invoke path. Run `python invoke.py --session-test`.")

    print("\n── 6. Now that ping is Healthy again, the instance is reclaimed ──────")
    print(f"Watching for up to {idle + 120}s.\n", flush=True)
    end = time.time() + idle + 120
    while time.time() < end:
        live = instances_for(ec2, cp_id)
        if not live:
            print("  instances: none — reclaimed. The idle timer did its job.")
            break
        print(f"  instances: {', '.join(i['id'] for i in live)}", flush=True)
        time.sleep(30)
    else:
        print("  still running — give it another minute or two.")


def session_test(config: dict) -> None:
    """Two session ids see none of each other's state."""
    data = data_client(config.get("region"))
    arn = config["runtimes"]["zip"]["arn"]

    print("Two session ids, and neither can see the other's jobs.")
    print("(The same session id is not a guarantee of one instance either —")
    print(" see the README. In-memory state is a fast path, not a store.)\n")
    first, second = new_session_id("sessA"), new_session_id("sessB")

    body, elapsed = invoke(
        data, arn, first, {"prompt": "Start a job called alpha, then report the session."}
    )
    print(f"── session A [{elapsed:.0f}s]\n{extract_text(body)}\n")

    body, elapsed = invoke(data, arn, first, {"action": "status"})
    a_status = json.loads(body)
    print(f"session A: process_id={a_status['process_id']} jobs={list(a_status['jobs'])}\n")

    body, elapsed = invoke(
        data, arn, second, {"prompt": "List the jobs you know about on this instance."}
    )
    print(f"── session B [{elapsed:.0f}s]\n{extract_text(body)}\n")

    body, elapsed = invoke(data, arn, second, {"action": "status"})
    b_status = json.loads(body)
    print(f"session B: process_id={b_status['process_id']} jobs={list(b_status['jobs'])}\n")

    print(f"different process:  {a_status['process_id'] != b_status['process_id']}")
    print(f"different hostname: {a_status['hostname'] != b_status['hostname']}")
    print("Session B cannot see session A's job — different instance, different disk.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-test", action="store_true",
                        help="show that two session ids share no state, instead of the job demo")
    parser.add_argument("--prompt", help="just send one prompt and print the reply")
    args = parser.parse_args()

    if not CONFIG.is_file():
        sys.exit(f"{CONFIG.name} not found — run `python deploy.py` first.")
    config = json.loads(CONFIG.read_text())

    if args.prompt:
        data = data_client(config.get("region"))
        body, elapsed = invoke(
            data,
            config["runtimes"]["zip"]["arn"],
            new_session_id("oneshot"),
            {"prompt": args.prompt},
        )
        print(f"[{elapsed:.0f}s]\n{extract_text(body)}")
    elif args.session_test:
        session_test(config)
    else:
        demo(config)


if __name__ == "__main__":
    main()
