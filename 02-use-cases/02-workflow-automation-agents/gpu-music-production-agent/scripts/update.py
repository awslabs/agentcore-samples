#!/usr/bin/env python3
"""Ship a new version of one agent, without touching the others.

This is the independent-deployment story made real: each agent has its own
artifact and its own runtime version, so one team can release while the other two
keep serving. The capacity provider, the volumes and the session are untouched.

    python scripts/update.py composition            # rebuild + update one agent
    python scripts/update.py composition mastering  # or several
    python scripts/update.py --all
    python scripts/update.py composition --restart-session

Two behaviours worth knowing:

* ``UpdateAgentRuntime`` is a replace, not a merge. Omitting a member drops it, so
  the current configuration is read back with ``GetAgentRuntime`` and re-sent with
  only the artifact changed.
* A new version does NOT reach a session that is already running. AgentCore keeps
  serving the code the session started with, with no error anywhere, so a fix can
  look deployed and have no effect. ``--restart-session`` stops the recorded
  sessions so the next invoke picks up the new version.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

PROJECT = Path(__file__).resolve().parent.parent
STATE_FILE = PROJECT / "deployment_state.json"

BUILD_PLATFORM = "linux/amd64"
WHEEL_PLATFORM = "x86_64-manylinux_2_28"
PYTHON_VERSION = "3.12"
CONTAINER_AGENTS = ("composition", "mastering")
ALL_AGENTS = ("composition", "mastering", "compliance")


def log(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"    ! {msg}", flush=True)


def die(msg: str) -> None:
    sys.exit(f"ERROR: {msg}")


def run(cmd: list[str], **kwargs) -> None:
    print(f"    $ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def container_cli() -> str:
    cli = next((c for c in ("finch", "docker", "nerdctl", "podman") if shutil.which(c)), None)
    if not cli:
        die("no container CLI found")
    if subprocess.run([cli, "info"], capture_output=True, check=False).returncode != 0:
        die(f"{cli} is installed but not usable (start its VM or daemon)")
    return cli


def vendor_agent_deps() -> Path:
    target = PROJECT / "build" / "agentdeps"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    log("vendoring agent dependencies")
    run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            WHEEL_PLATFORM,
            "--python-version",
            PYTHON_VERSION,
            "--target",
            str(target),
            "--only-binary",
            ":all:",
            "-r",
            str(PROJECT / "requirements.txt"),
        ]
    )
    return target


def push_image(agent: str, account: str, region: str, cli: str, tag: str) -> str:
    registry = f"{account}.dkr.ecr.{region}.amazonaws.com"
    uri = f"{registry}/music-production/{agent}-agent:{tag}"
    ecr = boto3.client("ecr", region_name=region)
    token = ecr.get_authorization_token()["authorizationData"][0]["authorizationToken"]
    password = base64.b64decode(token).decode().split(":", 1)[1]
    subprocess.run(
        [cli, "login", "--username", "AWS", "--password-stdin", registry],
        input=password.encode(),
        check=True,
        stdout=subprocess.DEVNULL,
    )
    log(f"building {agent} for {BUILD_PLATFORM}")
    run(
        [
            cli,
            "build",
            "--platform",
            BUILD_PLATFORM,
            "-f",
            str(PROJECT / f"Dockerfile.{agent}"),
            "-t",
            uri,
            str(PROJECT),
        ]
    )
    run([cli, "push", uri])
    size = subprocess.run(
        [cli, "images", "--format", "{{.Size}}", uri], capture_output=True, text=True, check=False
    ).stdout.strip()
    log(f"{agent} image {size or '<unknown>'} (limit 2 GB)")
    return uri


def push_zip(state: dict, tag: str) -> str:
    bucket = state["s3"]["bucket"]
    key = f"compliance/{tag}/compliance_agent.zip"
    build_dir = PROJECT / "build" / "compliance"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)
    log("vendoring compliance dependencies")
    run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            WHEEL_PLATFORM,
            "--python-version",
            PYTHON_VERSION,
            "--target",
            str(build_dir),
            "--only-binary",
            ":all:",
            "-r",
            str(PROJECT / "requirements.txt"),
        ]
    )
    shutil.copy(PROJECT / "compliance_agent.py", build_dir)
    shutil.copy(PROJECT / "audio_dsp.py", build_dir)
    archive = PROJECT / "build" / "compliance_agent.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(build_dir.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                zf.write(p, p.relative_to(build_dir))
    mb = archive.stat().st_size / 1e6
    raw = sum(p.stat().st_size for p in build_dir.rglob("*") if p.is_file()) / 1e6
    log(f"zip {mb:.1f} MB compressed, {raw:.1f} MB uncompressed (limits 250 / 750)")
    if mb > 250 or raw > 750:
        die("compliance zip exceeds the direct-code-deploy limits")
    boto3.client("s3", region_name=state["region"]).upload_file(str(archive), bucket, key)
    log(f"uploaded s3://{bucket}/{key}")
    state["s3"]["key"] = key
    return key


def update_runtime(region: str, runtime_id: str, artifact: dict) -> str:
    """Re-send the whole configuration with only the artifact changed."""
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    cur = control.get_agent_runtime(agentRuntimeId=runtime_id)
    kwargs: dict = {
        "agentRuntimeId": runtime_id,
        "agentRuntimeArtifact": artifact,
        "roleArn": cur["roleArn"],
        "protocolConfiguration": cur.get("protocolConfiguration") or {"serverProtocol": "HTTP"},
        "capacityProviderConfiguration": cur["capacityProviderConfiguration"],
        "filesystemConfigurations": cur["filesystemConfigurations"],
        "environmentVariables": cur.get("environmentVariables") or {},
    }
    if cur.get("lifecycleConfiguration"):
        kwargs["lifecycleConfiguration"] = cur["lifecycleConfiguration"]
    control.update_agent_runtime(**kwargs)
    while True:
        got = control.get_agent_runtime(agentRuntimeId=runtime_id)
        if got["status"] == "READY":
            return got.get("agentRuntimeVersion", "?")
        if "FAILED" in got["status"]:
            die(f"update left runtime {runtime_id} in {got['status']}")
        time.sleep(5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("agents", nargs="*", choices=ALL_AGENTS, default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument(
        "--restart-session",
        action="store_true",
        help="stop recorded sessions so the next invoke picks up the new "
        "version; without this a running session keeps serving the old code",
    )
    args = ap.parse_args()

    agents = list(ALL_AGENTS) if args.all else args.agents
    if not agents:
        ap.error("name at least one agent, or pass --all")

    if not STATE_FILE.exists():
        die(f"no {STATE_FILE.name}; run scripts/deploy.py first")
    state = json.loads(STATE_FILE.read_text())
    region, account = state["region"], state["account"]
    tag = f"v2-{int(time.time())}"

    if any(a in CONTAINER_AGENTS for a in agents):
        vendor_agent_deps()
        cli = container_cli()

    for agent in agents:
        runtime = state["runtimes"][agent]
        if agent in CONTAINER_AGENTS:
            uri = push_image(agent, account, region, cli, tag)
            artifact = {"containerConfiguration": {"containerUri": uri}}
        else:
            key = push_zip(state, tag)
            artifact = {
                "codeConfiguration": {
                    "code": {"s3": {"bucket": state["s3"]["bucket"], "prefix": key}},
                    "runtime": "PYTHON_3_12",
                    "entryPoint": ["compliance_agent.py"],
                }
            }
        version = update_runtime(region, runtime["id"], artifact)
        log(f"{agent} runtime is now version {version}")

    STATE_FILE.write_text(json.dumps(state, indent=2))

    if args.restart_session:
        data = boto3.client("bedrock-agentcore", region_name=region)
        for session_id in state.get("sessions", []):
            if len(session_id) < 33:
                continue
            for name in agents:
                try:
                    data.stop_runtime_session(
                        agentRuntimeArn=state["runtimes"][name]["arn"], runtimeSessionId=session_id
                    )
                    log(f"stopped {name} in {session_id}")
                except ClientError as exc:
                    code = exc.response["Error"]["Code"]
                    if code != "ResourceNotFoundException":
                        warn(f"stop {name}: {code}")
        print(
            "\nSessions stopped. The next invoke provisions fresh compute on the same "
            "session and picks up the new version. Volumes are retained, so a prepared "
            "model stack survives."
        )
    else:
        print(
            "\nUpdated. NOTE: a session that is already running keeps serving the "
            "previous version with no error. Re-run with --restart-session, or use a "
            "new session id, to actually exercise the new code."
        )


if __name__ == "__main__":
    main()
