"""CloudFormation custom-resource handler that builds the agent image.

Deployed as the ``BuildTriggerFn`` Lambda in ``agent_stack.py``. On stack
Create/Update it starts the CodeBuild project that builds and pushes the agent
container image to Amazon ECR, then polls until the build finishes so the
AgentCore Runtime resource is created only after the image exists. On Delete it
no-ops (the ECR repository's lifecycle rule tears down the images).

Kept as a standalone module (loaded via ``Code.from_asset``) rather than an
inline string so it is covered by linting, formatting, and static analysis.
"""

from __future__ import annotations

import json
import time
import urllib.request

import boto3

# Terminal CodeBuild states that mean the build will not succeed.
FAILED_STATES = ("FAILED", "FAULT", "STOPPED", "TIMED_OUT")
# Poll for up to ~14 minutes (28 × 30s), inside the Lambda's 15-minute timeout.
POLL_ATTEMPTS = 28
POLL_INTERVAL_SECONDS = 30


def handler(event, context):
    props = event.get("ResourceProperties", {})
    project_name = props.get("ProjectName", "")

    # No rebuild on stack delete — ECR contents are torn down by the
    # repository's lifecycle rule.
    if event["RequestType"] == "Delete":
        return _respond(event, context, "SUCCESS", {"ImageBuilt": "skipped"})

    cb = boto3.client("codebuild")
    try:
        build = cb.start_build(projectName=project_name)
        build_id = build["build"]["id"]
        print(f"Started CodeBuild: {build_id}")

        for _ in range(POLL_ATTEMPTS):
            time.sleep(POLL_INTERVAL_SECONDS)
            result = cb.batch_get_builds(ids=[build_id])
            status = result["builds"][0]["buildStatus"]
            print(f"Build status: {status}")
            if status == "SUCCEEDED":
                return _respond(event, context, "SUCCESS", {"BuildId": build_id})
            if status in FAILED_STATES:
                return _respond(event, context, "FAILED", {"Error": f"CodeBuild {status}"})
        return _respond(event, context, "FAILED", {"Error": "Build timed out"})
    except Exception as exc:  # noqa: BLE001 - report any failure back to CloudFormation
        print(f"Error: {exc}")
        return _respond(event, context, "FAILED", {"Error": str(exc)})


def _respond(event, context, status, data):
    body = json.dumps(
        {
            "Status": status,
            "Reason": json.dumps(data),
            "PhysicalResourceId": context.log_stream_name,
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
            "Data": data,
        }
    )
    # event["ResponseURL"] is the pre-signed S3 URL CloudFormation provides for
    # the custom-resource response — it is service-supplied, not user input.
    # Reject any non-HTTPS scheme so we never open file:// or custom schemes.
    response_url = event["ResponseURL"]
    if not response_url.lower().startswith("https://"):
        raise ValueError("CloudFormation ResponseURL must be an HTTPS URL")
    req = urllib.request.Request(
        response_url,
        data=body.encode(),
        method="PUT",
        headers={"Content-Type": ""},
    )
    # nosec B310 / noqa: S310 — URL scheme validated as HTTPS just above.
    urllib.request.urlopen(req)  # nosec B310
