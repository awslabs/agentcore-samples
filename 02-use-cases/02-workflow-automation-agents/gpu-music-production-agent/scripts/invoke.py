#!/usr/bin/env python3
"""Run the music production workflow against the deployed runtimes.

All three agents are invoked with the same runtimeSessionId on the same capacity
provider, which is what places them on one EC2 instance sharing one volume. Each
response reports the host that served it, so collocation is observable.

    python scripts/invoke.py                  # prepare, compose, master, comply
    python scripts/invoke.py --with-catalogue # also render a back-catalogue and a
                                              # deliberate near-copy of it, so the
                                              # similarity screen has something real
                                              # to catch
    python scripts/invoke.py --resume         # stop the previous session, resume it,
                                              # and re-run only the compliance step
    python scripts/invoke.py --track my-song --session <33-100 chars>

Rendered audio is downloaded from S3 into runs/<track>/. The agents write to the
capacity provider's EBS volume inside a managed instance with no shell, and that
volume is destroyed when the session is deleted, so the S3 copy is the only one
that outlives the fleet.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

STATE_FILE = Path(__file__).resolve().parent.parent / "deployment_state.json"

# A capacity failure is fast (~13 s) and is bound to the placement AgentCore
# chose, so retrying on a fresh session id is what buys another attempt.
CAPACITY_ATTEMPTS = 8


def load_state() -> dict:
    if not STATE_FILE.exists():
        sys.exit(f"No {STATE_FILE.name}. Run scripts/deploy.py first.")
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def data_client(region: str):
    return boto3.client(
        "bedrock-agentcore",
        region_name=region,
        # A cold start provisions a GPU instance and the first invoke also builds
        # the model stack, so the default 60 s read timeout is far too short.
        # 900 s is the service's own synchronous request ceiling. Retries are
        # limited because InvokeAgentRuntime is not idempotent.
        config=Config(read_timeout=900, retries={"max_attempts": 2, "mode": "standard"}),
    )


def record_session(state: dict, session_id: str) -> None:
    """Record every session id before it is used.

    Even a session whose placement FAILED exists as a session record, and its
    half-created EBS volume survives both DeleteCapacityProviderSession and
    DeleteCapacityProvider. Recording the id is the only way cleanup can try.
    """
    if session_id not in state.setdefault("sessions", []):
        state["sessions"].append(session_id)
        save_state(state)


def invoke(client, state: dict, runtime: dict, session_id: str, payload: dict, label: str) -> tuple[dict, str]:
    """Invoke one runtime, retrying capacity failures on a fresh session id."""
    for attempt in range(1, CAPACITY_ATTEMPTS + 1):
        sid = session_id if attempt == 1 else f"{session_id[:-3]}{attempt:03d}"
        record_session(state, sid)
        started = time.time()
        try:
            response = client.invoke_agent_runtime(
                agentRuntimeArn=runtime["arn"],
                qualifier="DEFAULT",
                runtimeSessionId=sid,
                payload=json.dumps(payload).encode(),
            )
            # The response body member is named "response", not "body".
            body = json.loads(response["response"].read())
            print(f"    {label}: {time.time() - started:.1f}s")
            return body, sid
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            message = exc.response["Error"]["Message"]
            elapsed = time.time() - started
            if "Insufficient EC2 capacity" in message:
                az = message.split("requested (")[-1].split(")")[0] if "requested (" in message else "?"
                print(
                    f"    {label}: no {state['instance_type']} capacity in {az} "
                    f"after {elapsed:.0f}s - retry {attempt}/{CAPACITY_ATTEMPTS} "
                    f"on a fresh session"
                )
                time.sleep(5)
                continue
            if code in ("InternalServerException", "RetryableConflictException", "ThrottlingException"):
                print(f"    {label}: {code} after {elapsed:.1f}s - retry {attempt}/{CAPACITY_ATTEMPTS}")
                time.sleep(5 * attempt)
                continue
            raise
    sys.exit(
        f"{label}: no capacity after {CAPACITY_ATTEMPTS} attempts. GPU capacity "
        f"for {state['instance_type']} is exhausted in every AZ of this capacity "
        f"provider. Try again later, choose another Region, or reserve capacity "
        f"with an On-Demand Capacity Reservation."
    )


def download_artifacts(body: dict, out_dir: Path, region: str) -> list[Path]:
    """Fetch what the agent produced.

    Prefers the caller's own credentials over the presigned URL the agent
    returned. The URL is genuinely useful for handing a render to someone without
    AWS access, but depending on it here would make this script fail for reasons
    that have nothing to do with the agents -- a presigned URL is only valid while
    the credentials that signed it are, and AgentCore rotates the execution role
    credentials it vends to the agent.
    """
    saved: list[Path] = []
    s3 = boto3.client("s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com")
    for art in body.get("artifacts") or []:
        uri = art.get("s3_uri")
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / art["name"]
        if uri:
            bucket, _, key = uri[len("s3://") :].partition("/")
            try:
                s3.download_file(bucket, key, str(dest))
                saved.append(dest)
                continue
            except ClientError as exc:
                print(
                    f"    ! s3 download of {art['name']} failed: "
                    f"{exc.response['Error']['Code']}; trying the presigned URL"
                )
        url = art.get("url")
        if not url:
            continue
        try:
            with urllib.request.urlopen(url, timeout=300) as r, open(dest, "wb") as fh:
                fh.write(r.read())
            saved.append(dest)
        except Exception as exc:  # noqa: BLE001
            print(f"    ! could not download {art['name']}: {exc}")
    return saved


def show_measurements(label: str, m: dict | None) -> None:
    if not m:
        return
    print(
        f"    {label:9s} {m.get('duration_s')}s  {m.get('sample_rate')}Hz  "
        f"{m.get('channels')}ch  {m.get('integrated_lufs')} LUFS  "
        f"peak {m.get('true_peak_dbtp')} dBTP  LRA {m.get('loudness_range_lu')} LU"
    )


def report(step: str, body: dict, out_dir: Path | None = None, region: str = "us-east-2") -> dict:
    host = body.get("host") or {}
    print(f"  {step}")
    print(f"    status   : {body.get('status')}")
    if body.get("status") != "ok":
        print(f"    error    : {body.get('error')}")
        return host
    print(f"    host     : {host.get('hostname')} (process {host.get('process_id')}, {host.get('architecture')})")
    if body.get("read_from"):
        print(f"    read     : {body['read_from']}  <- written by another agent")

    render = body.get("render") or {}
    if render:
        t = render.get("timings", {})
        print(
            f"    rendered : {render.get('device')} in {t.get('generate_s')}s "
            f"(load {t.get('pipeline_ctor_s')}s, peak VRAM {t.get('peak_vram_gib')} GiB)"
        )
    if body.get("measurements") and "before" not in body["measurements"]:
        show_measurements("audio", body["measurements"])
    if body.get("measurements") and "before" in body["measurements"]:
        show_measurements("before", body["measurements"]["before"])
        show_measurements("after", body["measurements"]["after"])
    if body.get("targets_met"):
        tm = body["targets_met"]
        print(
            f"    targets  : loudness {'met' if tm['loudness'] else 'MISSED'}, "
            f"true peak {'held' if tm['true_peak'] else 'EXCEEDED'}"
        )

    if "validation_passed" in body:
        label = {"cleared": "CLEARED", "review_required": "REVIEW REQUIRED", "not_cleared": "NOT CLEARED"}.get(
            body.get("outcome"), "CLEARED" if body["validation_passed"] else "NOT CLEARED"
        )
        print(f"    verdict  : {label}")
        qc = body.get("delivery_qc") or {}
        for c in qc.get("checks", []):
            if not c["pass"]:
                print(f"               FAIL {c['check']}: {c['detail']}")
        sim = body.get("similarity") or {}
        if sim.get("references"):
            closest = sim.get("closest") or {}
            print(
                f"    screen   : {sim['references']} reference(s), closest "
                f"{closest.get('reference')} distance {closest.get('distance')} "
                f"({closest.get('verdict')})"
            )
            if sim.get("standout_ratio") is not None:
                print(
                    f"    standout : {sim['standout_ratio']}x the next-closest "
                    f"(flag below {sim['thresholds']['standout_ratio_below']})"
                )
            if sim.get("flag_reason"):
                print(f"    flagged  : {sim['flag_reason']}")
        elif sim.get("note"):
            print(f"    screen   : {sim['note']}")
        if body.get("master_is_stale"):
            print("    stale    : master.wav predates the screened render - re-run mastering")
        if (body.get("verdict") or {}).get("remediation_requested"):
            print("               remediation was requested from the composition agent")

    if body.get("model_stack"):
        ms = body["model_stack"]
        print(
            f"    stack    : ready={ms.get('ready')} "
            f"{'in ' + str(ms.get('prepared_in_seconds')) + 's' if ms.get('prepared_in_seconds') else ''}"
        )

    print(f"    files    : {body.get('workspace_files')}")
    if out_dir:
        for p in download_artifacts(body, out_dir, region):
            print(f"    saved    : {p}  ({p.stat().st_size / 1e6:.2f} MB)")
    return host


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--track", default=None, help="defaults to a fresh track id")
    parser.add_argument("--session", default=None, help="33-100 characters")
    parser.add_argument(
        "--with-catalogue",
        action="store_true",
        help="render a back-catalogue, then a deliberate near-copy of "
        "it, so the similarity screen has real material to flag",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="stop the session recorded by the previous run and resume it, running only the compliance step",
    )
    parser.add_argument("--duration", type=float, default=30.0, help="seconds of audio to render")
    args = parser.parse_args()

    state = load_state()
    region = state["region"]
    runtimes = state["runtimes"]
    client = data_client(region)

    if args.resume:
        last = state.get("last_run") or {}
        session_id = args.session or last.get("session_id")
        track = args.track or last.get("track")
        if not (session_id and track):
            sys.exit(
                "--resume needs a previous run. Run 'python scripts/invoke.py' "
                "first, or pass --session and --track explicitly."
            )
    else:
        session_id = args.session or f"music-production-{uuid.uuid4()}"
        track = args.track or f"track-{int(time.time())}"

    # InvokeAgentRuntime accepts 33-256 characters, but DeleteCapacityProviderSession
    # caps sessionId at 100 - a longer id can be invoked and then never deleted,
    # stranding its EBS volumes. Hold the whole tool to the deletable range.
    if not 33 <= len(session_id) <= 100:
        sys.exit(f"--session must be 33-100 characters (got {len(session_id)}).")

    state["last_run"] = {"session_id": session_id, "track": track}
    record_session(state, session_id)

    print(f"track   : {track}")
    print(f"session : {session_id}")
    print(f"instance: {state['instance_type']}  region: {region}")
    if args.resume:
        print("Resuming the session above - only the compliance step runs.\n")
    else:
        print(
            f"The first invoke provisions a {state['instance_type']} and builds the "
            "generative stack on the models volume, so expect several minutes.\n"
        )

    run_dir = STATE_FILE.parent / "runs" / track
    hosts: dict[str, dict] = {}
    active = session_id

    if args.resume:
        print("  -- stopping every agent in the session --")
        for name, runtime in runtimes.items():
            try:
                client.stop_runtime_session(agentRuntimeArn=runtime["arn"], runtimeSessionId=session_id)
                print(f"    stopped {name}")
            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                # Normal, not a failure: that runtime was never invoked in this
                # session, so there is no session of its own to stop.
                print(f"    {name}: {'nothing to stop' if code == 'ResourceNotFoundException' else code}")
        # StopRuntimeSession stops one agent in a session; the instance goes away
        # only once every agent on it has been idle for idleInstanceTimeout.
        print("    waiting 30s before resuming\n")
        time.sleep(30)
    else:
        # 1. Build the generative stack on the models volume. Per-session,
        #    because the volume is per-session, unless MODELS_SNAPSHOT_ID was set
        #    at deploy time.
        body, active = invoke(client, state, runtimes["composition"], active, {"mode": "prepare"}, "prepare")
        hosts["prepare"] = report("1. prepare model stack (GPU instance + torch + weights)", body)
        if body.get("status") != "ok":
            sys.exit("model stack preparation failed; see the error above")

        step = 2
        if args.with_catalogue:
            body, active = invoke(
                client,
                state,
                runtimes["composition"],
                active,
                {"mode": "catalogue", "track_id": track, "duration_s": min(args.duration, 20.0)},
                "catalogue",
            )
            hosts["catalogue"] = report(f"{step}. render back-catalogue", body, run_dir, region)
            step += 1

        # 2. Compose. With --with-catalogue this deliberately imitates a
        #    catalogue entry so the compliance screen has something real to find.
        compose_payload: dict = {
            "mode": "compose",
            "track_id": track,
            "prompt": "Create an upbeat electronic track with heavy bass and synth melodies.",
            "duration_s": args.duration,
            "seed": 42,
        }
        if args.with_catalogue:
            compose_payload.update(
                imitate_catalogue="catalogue_00.wav",
                reference_strength=0.85,
                prompt="Create a melodic techno track with analog bass and warm pads, close to our catalogue sound.",
            )
        body, active = invoke(client, state, runtimes["composition"], active, compose_payload, "compose")
        hosts["composition"] = report(f"{step}. compose (renders audio on the GPU)", body, run_dir, region)
        step += 1

        # 3. Master, reading the audio the composition agent rendered.
        body, active = invoke(
            client,
            state,
            runtimes["mastering"],
            active,
            {"track_id": track, "platform": "spotify", "prompt": "Master this for streaming."},
            "master",
        )
        hosts["mastering"] = report(f"{step}. master (real DSP, verified by measurement)", body, run_dir, region)
        step += 1

    body, active = invoke(
        client,
        state,
        runtimes["compliance"],
        active,
        {"track_id": track, "prompt": "Screen this master for release."},
        "compliance",
    )
    hosts["compliance"] = report(
        "compliance (resumed session)" if args.resume else "5. compliance screen", body, run_dir, region
    )

    # Close the loop. A remediation replaces the composition, which leaves the
    # master describing audio that no longer exists -- the compliance agent says so
    # via master_is_stale. Without this the run ends holding a master of the
    # rejected material, which is the one artifact a producer would actually ship.
    if body.get("status") == "ok" and body.get("master_is_stale"):
        print("\n  -- remediation happened, so the master is stale: re-mastering --")
        body, active = invoke(
            client,
            state,
            runtimes["mastering"],
            active,
            {"track_id": track, "platform": "spotify", "prompt": "Master the replacement for streaming."},
            "re-master",
        )
        hosts["re-master"] = report("6. re-master the replacement", body, run_dir, region)

        body, active = invoke(
            client,
            state,
            runtimes["compliance"],
            active,
            {
                "track_id": track,
                # The replacement has already been screened once; this
                # pass is about the new master, so do not remediate again.
                "auto_remediate": False,
                "prompt": "Screen the re-mastered replacement.",
            },
            "re-screen",
        )
        hosts["re-screen"] = report("7. re-screen the new master", body, run_dir, region)

    state["last_run"] = {"session_id": active, "track": track}
    save_state(state)

    print("\n  -- collocation --")
    names = [h.get("hostname") for h in hosts.values() if h.get("hostname")]
    for agent, host in hosts.items():
        print(f"    {agent:12} {host.get('hostname')}")
    if len(names) < 2:
        print("    Only one step ran. Collocation is visible on a full pipeline.")
    elif len(set(names)) == 1:
        print(f"    All {len(names)} steps were served by one instance, as intended.")
    else:
        print("    Steps landed on different hosts - the shared volume still carried")
        print("    the artifacts, but a capacity retry started a new session.")

    if run_dir.exists():
        produced = sorted(p for p in run_dir.iterdir() if p.is_file())
        print("\n  -- what the agents produced (downloaded from S3) --")
        for p in produced:
            print(f"    {p}  ({p.stat().st_size / 1e6:.2f} MB)")
        print("    The originals are on the instance's volume and go away with the")
        print("    session; these copies and the S3 objects are what you keep.")

    print("\nDone. Delete the session and the fleet with: python scripts/cleanup.py")
    print("Deleting the SESSION is what stops EC2 and EBS charges.")


if __name__ == "__main__":
    main()
