#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Build the console SPA and deploy it to Amplify Hosting.

Called by Terraform after the API and Cognito pool exist, because the built
bundle needs their identifiers.

Config is injected as a `public/config.json` fetched at runtime rather than
baked in via Vite `VITE_*` env vars. That way the same build artifact can be
redeployed against a different stack, and rotating the API URL does not require
a rebuild.

Steps: write config.json -> npm run build -> zip dist/ -> CreateDeployment ->
upload to the returned presigned URL -> StartDeployment -> poll to SUCCEED.
"""

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import boto3
from botocore.exceptions import ClientError

REPO = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = REPO / "frontend"

DEPLOY_TIMEOUT_SECONDS = 600
POLL_INTERVAL_SECONDS = 5


def log(message: str) -> None:
    print(f"[frontend] {message}", flush=True)


def run(command: list[str], cwd: pathlib.Path) -> None:
    """Run a subprocess, surfacing its output on failure."""
    # nosemgrep: dangerous-subprocess-use-audit — list-form (no shell); only ever called with the static npm commands below
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)  # noqa: S603
    if result.returncode != 0:
        log(f"command failed: {' '.join(command)}")
        sys.stderr.write(result.stdout[-4000:])
        sys.stderr.write(result.stderr[-4000:])
        raise SystemExit(1)


def build(args) -> pathlib.Path:
    """Write runtime config, build the SPA, and return the output directory."""
    config = {
        "apiBase": args.api_url.rstrip("/"),
        "region": args.region,
        "userPoolId": args.user_pool_id,
        "userPoolClientId": args.client_id,
        "identityPoolId": args.identity_pool_id,
    }

    public = FRONTEND / "public"
    public.mkdir(exist_ok=True)
    (public / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    log(f"wrote {public / 'config.json'}")

    log("installing dependencies")
    run(["npm", "install", "--silent"], FRONTEND)

    log("building")
    run(["npm", "run", "build"], FRONTEND)

    dist = FRONTEND / "dist"
    if not (dist / "index.html").is_file():
        log(f"ERROR: build produced no index.html in {dist}")
        raise SystemExit(1)
    return dist


def package(dist: pathlib.Path) -> pathlib.Path:
    """Zip the built assets for Amplify's manual-deployment upload."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    archive = shutil.make_archive(str(tmp / "console"), "zip", root_dir=dist)
    size_mb = pathlib.Path(archive).stat().st_size / 1_048_576
    log(f"packaged {archive} ({size_mb:.1f} MB)")
    return pathlib.Path(archive)


def deploy(client, app_id: str, branch: str, archive: pathlib.Path) -> str:
    """Upload the bundle and start a deployment. Returns the job ID."""
    created = client.create_deployment(appId=app_id, branchName=branch)
    upload_url = created["zipUploadUrl"]

    log("uploading bundle")
    request = urllib.request.Request(
        upload_url,
        data=archive.read_bytes(),
        method="PUT",
        headers={"Content-Type": "application/zip"},
    )
    # nosec B310 — the URL is an AWS-issued presigned S3 URL.
    with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
        if response.status not in (200, 204):
            log(f"ERROR: upload returned HTTP {response.status}")
            raise SystemExit(1)

    started = client.start_deployment(
        appId=app_id, branchName=branch, jobId=created["jobId"]
    )
    job_id = started["jobSummary"]["jobId"]
    log(f"started deployment job {job_id}")
    return job_id


def wait(client, app_id: str, branch: str, job_id: str) -> str:
    """Poll a deployment until it reaches a terminal state."""
    deadline = time.time() + DEPLOY_TIMEOUT_SECONDS
    status = "PENDING"

    while time.time() < deadline:
        status = client.get_job(appId=app_id, branchName=branch, jobId=job_id)[
            "job"
        ]["summary"]["status"]
        if status in ("SUCCEED", "FAILED", "CANCELLED"):
            return status
        # nosemgrep: arbitrary-sleep — poll interval inside a bounded deployment wait
        time.sleep(POLL_INTERVAL_SECONDS)

    log(f"WARNING: deployment still {status} after {DEPLOY_TIMEOUT_SECONDS}s")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--user-pool-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--identity-pool-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    dist = build(args)
    archive = package(dist)

    client = boto3.client("amplify", region_name=args.region)
    try:
        job_id = deploy(client, args.app_id, args.branch, archive)
        status = wait(client, args.app_id, args.branch, job_id)
    except ClientError as exc:
        log(f"ERROR: {exc}")
        return 1

    if status != "SUCCEED":
        log(f"ERROR: deployment finished as {status}")
        return 1

    url = f"https://{args.branch}.{args.app_id}.amplifyapp.com"
    log(f"deployed — {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
