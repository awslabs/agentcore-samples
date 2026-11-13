#!/usr/bin/env python3
"""Create or update the stub agent on AgentCore Runtime V2 with boto3.

Creates the ECR repository and execution role if missing, builds and pushes
agent/ for linux/arm64 (docker buildx), creates or updates the runtime, and
waits for READY. The last stdout line is JSON: {"arn": "...", "updated": bool}.

usage: python agent-infra/deploy.py [--idle-timeout-s N] [--max-lifetime-s N]
"""
import argparse
import base64
import json
import time

from botocore.exceptions import ClientError
from common import (
    AGENT_DIR,
    AGENT_NAME,
    EXECUTION_ROLE_NAME,
    REPO_NAME,
    clients,
    find_runtime,
    image_tag,
    log,
    run,
    wait_until_ready,
)


def trust_policy(acct, region):
    return {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
        "Action": "sts:AssumeRole",
        "Condition": {
            "StringEquals": {"aws:SourceAccount": acct},
            "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{acct}:*"}}}]}


def execution_policy(acct, region):
    ac = f"arn:aws:bedrock-agentcore:{region}:{acct}"
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "PullImage", "Effect": "Allow",
         "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
         "Resource": f"arn:aws:ecr:{region}:{acct}:repository/{REPO_NAME}"},
        {"Sid": "EcrToken", "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        {"Sid": "Logs", "Effect": "Allow",
         "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                    "logs:DescribeLogStreams", "logs:DescribeLogGroups"],
         "Resource": f"arn:aws:logs:{region}:{acct}:log-group:/aws/bedrock-agentcore/runtimes/*"},
        {"Sid": "Traces", "Effect": "Allow",
         "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules", "xray:GetSamplingTargets"], "Resource": "*"},
        {"Sid": "Metrics", "Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*",
         "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}},
        {"Sid": "WorkloadIdentity", "Effect": "Allow",
         "Action": ["bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId"],
         "Resource": [f"{ac}:workload-identity-directory/default",
                      f"{ac}:workload-identity-directory/default/workload-identity/{AGENT_NAME}-*"]}]}


def ensure_repo(ecr):
    try:
        return ecr.describe_repositories(repositoryNames=[REPO_NAME])["repositories"][0]["repositoryUri"]
    except ecr.exceptions.RepositoryNotFoundException:
        pass
    log(f"creating ECR repository {REPO_NAME}")
    uri = ecr.create_repository(
        repositoryName=REPO_NAME, imageTagMutability="IMMUTABLE",
        imageScanningConfiguration={"scanOnPush": True},
        encryptionConfiguration={"encryptionType": "AES256"},
        tags=[{"Key": "created-by", "Value": "agentcore-warm-pool"}])["repository"]["repositoryUri"]
    ecr.put_lifecycle_policy(repositoryName=REPO_NAME, lifecyclePolicyText=json.dumps({"rules": [{
        "rulePriority": 1, "description": "keep the last 5 images",
        "selection": {"tagStatus": "any", "countType": "imageCountMoreThan", "countNumber": 5},
        "action": {"type": "expire"}}]}))
    return uri


def ensure_role(iam, acct, region):
    created = False
    try:
        arn = iam.get_role(RoleName=EXECUTION_ROLE_NAME)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        log(f"creating execution role {EXECUTION_ROLE_NAME}")
        arn = iam.create_role(
            RoleName=EXECUTION_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy(acct, region)),
            Description="Execution role for the AgentCore warm pool stub agent",
            Tags=[{"Key": "created-by", "Value": "agentcore-warm-pool"}])["Role"]["Arn"]
        created = True
    iam.put_role_policy(RoleName=EXECUTION_ROLE_NAME, PolicyName="agent-runtime",
                        PolicyDocument=json.dumps(execution_policy(acct, region)))
    return arn, created


def build_and_push(ecr, repo_uri, tag):
    uri = f"{repo_uri}:{tag}"
    try:
        ecr.describe_images(repositoryName=REPO_NAME, imageIds=[{"imageTag": tag}])
        log(f"image {tag} already in ECR, skipping build")
        return uri
    except ecr.exceptions.ImageNotFoundException:
        pass
    auth = ecr.get_authorization_token()["authorizationData"][0]
    user, token = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    run(["docker", "login", "--username", user, "--password-stdin", repo_uri.split("/")[0]], stdin=token)
    log(f"building and pushing {uri} (linux/arm64)")
    run(["docker", "buildx", "build", "--platform", "linux/arm64", "-t", uri, "--push", str(AGENT_DIR)])
    return uri


def create_or_update(acc, role_arn, image_uri, idle_s, max_s):
    spec = {
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": image_uri}},
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "lifecycleConfiguration": {"idleRuntimeSessionTimeout": idle_s, "maxLifetime": max_s},
        "platformVersion": "V2",
    }
    existing = find_runtime({"bedrock-agentcore-control": acc})
    if existing is None:
        log(f"creating runtime {AGENT_NAME} on platform version V2")
        rt = acc.create_agent_runtime(agentRuntimeName=AGENT_NAME,
                                      description="Stub agent for the AgentCore warm pool sample",
                                      tags={"created-by": "agentcore-warm-pool"}, **spec)
        return rt["agentRuntimeId"], False
    log(f"updating runtime {AGENT_NAME} ({existing['agentRuntimeId']})")
    acc.update_agent_runtime(agentRuntimeId=existing["agentRuntimeId"], **spec)
    return existing["agentRuntimeId"], True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle-timeout-s", type=int, default=14400)
    ap.add_argument("--max-lifetime-s", type=int, default=28800)
    a = ap.parse_args()
    if not 60 <= a.idle_timeout_s <= a.max_lifetime_s <= 28800:
        raise SystemExit("need 60 <= idle-timeout-s <= max-lifetime-s <= 28800")

    c = clients()
    acc, ecr, iam = c["bedrock-agentcore-control"], c["ecr"], c["iam"]
    region = acc.meta.region_name
    acct = c["sts"].get_caller_identity()["Account"]

    repo_uri = ensure_repo(ecr)
    role_arn, _role_created = ensure_role(iam, acct, region)
    image_uri = build_and_push(ecr, repo_uri, image_tag())

    # a new IAM role takes a few seconds to propagate; retry while the service can't assume it
    for attempt in range(1, 8):
        try:
            runtime_id, updated = create_or_update(acc, role_arn, image_uri, a.idle_timeout_s, a.max_lifetime_s)
            break
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if attempt == 7 or code not in ("ValidationException", "AccessDeniedException"):
                raise
            log(f"{code}: {exc.response['Error'].get('Message', '')} (retry {attempt}/6 in 10s)")
            time.sleep(10)

    log("waiting for READY (startup and snapshot take a few minutes)")
    rt = wait_until_ready(c, runtime_id)
    log(f"runtime ready: {rt['agentRuntimeArn']} (version {rt.get('agentRuntimeVersion')})")
    print(json.dumps({"arn": rt["agentRuntimeArn"], "updated": updated}))


if __name__ == "__main__":
    main()
