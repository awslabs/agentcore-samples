#!/usr/bin/env python3
"""Deploy: CloudFormation stack -> generated data into S3 -> agent image -> AgentCore runtime.

    python infra/deploy.py                # everything (re-running updates what changed)
    python infra/deploy.py --skip-stack   # agent code changed: new image + runtime update
"""

import argparse
import base64
import hashlib
import subprocess
import time

from pathlib import Path

import data_gen
from config import (
    MODEL_ID, NAME, REGION, ROOT, RUNTIME_NAME, STACK_NAME, client, die,
    find_runtime, load_state, log, save_state, session_id, stack_outputs,
)

AGENT_DIR = ROOT / "agent"


def deploy_stack() -> dict:
    cfn = client("cloudformation")
    params = {"Name": NAME, "ModelId": MODEL_ID, "FoundationModelId": MODEL_ID.split(".", 1)[1]}
    args = dict(StackName=STACK_NAME, TemplateBody=(ROOT / "infra" / "cfn.yaml").read_text(), Capabilities=["CAPABILITY_IAM"],
                Parameters=[{"ParameterKey": k, "ParameterValue": v} for k, v in params.items()])
    try:
        status = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"][0]["StackStatus"]
    except cfn.exceptions.ClientError:
        status = None
    if status == "ROLLBACK_COMPLETE":  # a failed first create can only be deleted
        log(f"1/4 stack {STACK_NAME} is ROLLBACK_COMPLETE; deleting it first")
        cfn.delete_stack(StackName=STACK_NAME)
        cfn.get_waiter("stack_delete_complete").wait(StackName=STACK_NAME)
        status = None
    try:
        if status:
            log(f"1/4 stack {STACK_NAME}: updating")
            cfn.update_stack(**args)
            waiter = "stack_update_complete"
        else:
            log(f"1/4 stack {STACK_NAME}: creating")
            cfn.create_stack(**args, OnFailure="ROLLBACK")
            waiter = "stack_create_complete"
    except cfn.exceptions.ClientError as e:
        if "No updates are to be performed" not in str(e):
            raise
        log("1/4 stack up to date")
        return stack_outputs()
    try:
        cfn.get_waiter(waiter).wait(StackName=STACK_NAME, WaiterConfig={"Delay": 10, "MaxAttempts": 180})
    except Exception:  # noqa: BLE001
        for ev in cfn.describe_stack_events(StackName=STACK_NAME)["StackEvents"][:20]:
            if "FAILED" in ev["ResourceStatus"]:
                log(f"  {ev['LogicalResourceId']}: {ev.get('ResourceStatusReason', '')}")
        die("stack failed")
    log("1/4 stack ready")
    return stack_outputs()


def _sync(s3, bucket: str, prefix: str, files: dict[str, Path]) -> int:
    """Make s3://bucket/prefix hold exactly `files` (key suffix -> local file): upload what changed
    (each object carries a SHA-256 of its content in its metadata), delete what's no longer there."""
    have = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
            for o in page.get("Contents", [])]
    want = {prefix + rel: local for rel, local in files.items()}
    for key, local in want.items():
        digest = hashlib.sha256(local.read_bytes()).hexdigest()
        if key in have and s3.head_object(Bucket=bucket, Key=key)["Metadata"].get("sha256") == digest:
            continue
        log(f"2/4 uploading s3://{bucket}/{key} ({local.stat().st_size / 1e6:,.1f} MB)")
        s3.upload_file(str(local), bucket, key, ExtraArgs={"Metadata": {"sha256": digest}})
    stale = sorted(set(have) - set(want))
    for i in range(0, len(stale), 1000):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in stale[i:i + 1000]], "Quiet": True})
    return len(want)


def upload_data(bucket: str) -> None:
    """Generate the data (skipped when .cache/ has it) and sync it to the bucket:

        datasets/market/                 shared market data, read by every session
        users/<session id>/data/         each agency's private portfolio, under the prefix of the session
                                         ID the backend maps it to (config.session_id)

    Seeding a user's prefix is the backend's job at sign-up; it runs with administrator credentials,
    which carry no session context key, so the bucket policy's per-session deny doesn't apply to it."""
    s3 = client("s3")
    market = data_gen.generate()
    n = _sync(s3, bucket, "datasets/market/", {str(f.relative_to(data_gen.OUT)): f for f in market})
    log(f"2/4 market data ready: {n} files in s3://{bucket}/datasets/market/")
    for agency, files in data_gen.generate_portfolios().items():
        root = data_gen.USERS_OUT / agency
        prefix = f"users/{session_id(agency)}/data/"
        n = _sync(s3, bucket, prefix, {str(f.relative_to(root)): f for f in files})
        log(f"2/4 {agency}: {n} private file(s) in s3://{bucket}/{prefix}")


def build_image(repo_uri: str) -> str:
    """Tag = hash of agent/, so an unchanged agent is never rebuilt and a changed one always is."""
    h = hashlib.sha256()
    for p in sorted(AGENT_DIR.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(AGENT_DIR)).encode() + p.read_bytes())
    tag = h.hexdigest()[:12]
    uri = f"{repo_uri}:{tag}"
    ecr = client("ecr")
    try:
        ecr.describe_images(repositoryName=repo_uri.split("/", 1)[1], imageIds=[{"imageTag": tag}])
        log(f"3/4 image {tag}: already pushed")
        return uri
    except ecr.exceptions.ImageNotFoundException:
        pass
    log(f"3/4 image {tag}: building linux/arm64")
    user, password = base64.b64decode(ecr.get_authorization_token()["authorizationData"][0]["authorizationToken"]).decode().split(":", 1)
    subprocess.run(["docker", "login", "--username", user, "--password-stdin", repo_uri.split("/", 1)[0]],
                   input=password, text=True, check=True, capture_output=True)
    subprocess.run(["docker", "buildx", "build", "--platform", "linux/arm64", "--provenance=false",
                    "-t", uri, "--push", str(AGENT_DIR)], check=True)
    return uri


def deploy_runtime(outputs: dict, image_uri: str) -> dict:
    account = client("sts").get_caller_identity()["Account"]
    bucket = outputs["DataBucketName"]
    mount = {"bucket": bucket, "s3BucketOwner": account, "s3BucketRegion": REGION}
    body = {
        "filesystemConfigurations": [  # {runtimeSessionId} makes the mount root the session's own prefix
            {"s3BucketMount": {**mount, "prefix": "datasets/market/", "mountPath": "/mnt/data"}},
            {"s3BucketMount": {**mount, "prefix": "users/{runtimeSessionId}/", "mountPath": "/mnt/private"}},
        ],
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": image_uri}},
        "roleArn": outputs["AgentExecutionRoleArn"],
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "platformVersion": "V2",  # required for s3BucketMount
        "environmentVariables": {"DATA_BUCKET": bucket, "MODEL_ID": MODEL_ID, "AWS_REGION": REGION},
    }
    ctl = client("bedrock-agentcore-control")
    existing = find_runtime(ctl)
    if existing:
        rid = existing["agentRuntimeId"]
        log(f"4/4 runtime {rid}: updating")
        ctl.update_agent_runtime(agentRuntimeId=rid, **body)
    else:
        log(f"4/4 runtime {RUNTIME_NAME}: creating")
        rid = ctl.create_agent_runtime(agentRuntimeName=RUNTIME_NAME, **body)["agentRuntimeId"]
    t0 = time.time()
    while (rt := ctl.get_agent_runtime(agentRuntimeId=rid))["status"] != "READY":
        if rt["status"].endswith("FAILED"):
            die(f"runtime {rid}: {rt['status']} -- {rt.get('failureReason', '')}")
        log(f"  [{time.time() - t0:.0f}s] {rt['status']}")
        time.sleep(15)
    log(f"4/4 runtime READY in {time.time() - t0:.0f}s")
    return rt


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--skip-stack", action="store_true", help="reuse the stack and dataset; rebuild the image and update the runtime")
    a = ap.parse_args()
    if a.skip_stack:
        outputs = stack_outputs() or die(f"stack {STACK_NAME} not found -- run without --skip-stack first")
    else:
        outputs = deploy_stack()
        upload_data(outputs["DataBucketName"])
    image_uri = build_image(outputs["RepositoryUri"])
    rt = deploy_runtime(outputs, image_uri)
    state = load_state()  # read again: upload_data may have added the agencies' session IDs
    state.update(outputs=outputs, image_uri=image_uri, runtime_id=rt["agentRuntimeId"], runtime_arn=rt["agentRuntimeArn"])
    save_state(state)
    log('done. next: python infra/invoke.py --agency acme "How do my listings compare with the market in my neighborhoods?"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
