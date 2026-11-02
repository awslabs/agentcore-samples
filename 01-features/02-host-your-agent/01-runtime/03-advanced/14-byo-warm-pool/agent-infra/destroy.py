#!/usr/bin/env python3
"""Delete the stub agent runtime, its execution role, and its ECR repository with boto3.

Only touches resources this sample created: the runtime is deleted when our
execution role exists (or with --force). Safe to run when pieces are already gone.

usage: python agent-infra/destroy.py [--force]
"""
import argparse
import time

from common import AGENT_NAME, EXECUTION_ROLE_NAME, REPO_NAME, clients, find_runtime, log


def ours(iam):
    try:
        iam.get_role(RoleName=EXECUTION_ROLE_NAME)
        return True
    except iam.exceptions.NoSuchEntityException:
        return False


def delete_runtime(acc):
    rt = find_runtime({"bedrock-agentcore-control": acc})
    if rt is None:
        log(f"runtime {AGENT_NAME} not found, skipping")
        return
    rid = rt["agentRuntimeId"]
    kwargs = {}
    while True:   # custom endpoints first; DEFAULT goes away with the runtime
        resp = acc.list_agent_runtime_endpoints(agentRuntimeId=rid, **kwargs)
        for ep in resp.get("runtimeEndpoints", []):
            if ep["name"] != "DEFAULT":
                log(f"deleting endpoint {ep['name']}")
                try:
                    acc.delete_agent_runtime_endpoint(agentRuntimeId=rid, endpointName=ep["name"])
                except acc.exceptions.ResourceNotFoundException:
                    pass
        if not resp.get("nextToken"):
            break
        kwargs["nextToken"] = resp["nextToken"]
    for _ in range(60):
        resp = acc.list_agent_runtime_endpoints(agentRuntimeId=rid)
        if all(ep["name"] == "DEFAULT" for ep in resp.get("runtimeEndpoints", [])):
            break
        time.sleep(5)
    log(f"deleting runtime {AGENT_NAME} ({rid})")
    acc.delete_agent_runtime(agentRuntimeId=rid)
    for _ in range(60):
        try:
            acc.get_agent_runtime(agentRuntimeId=rid)
        except acc.exceptions.ResourceNotFoundException:
            return
        time.sleep(5)
    log("runtime still deleting after 5 minutes, continuing")


def delete_role(iam):
    try:
        for name in iam.list_role_policies(RoleName=EXECUTION_ROLE_NAME)["PolicyNames"]:
            iam.delete_role_policy(RoleName=EXECUTION_ROLE_NAME, PolicyName=name)
        iam.delete_role(RoleName=EXECUTION_ROLE_NAME)
        log(f"deleted role {EXECUTION_ROLE_NAME}")
    except iam.exceptions.NoSuchEntityException:
        log(f"role {EXECUTION_ROLE_NAME} not found, skipping")


def delete_repo(ecr):
    try:
        ecr.delete_repository(repositoryName=REPO_NAME, force=True)   # force also deletes the images
        log(f"deleted ECR repository {REPO_NAME}")
    except ecr.exceptions.RepositoryNotFoundException:
        log(f"repository {REPO_NAME} not found, skipping")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="delete the runtime even if this sample did not create it")
    a = ap.parse_args()
    c = clients()
    iam = c["iam"]
    if not (a.force or ours(iam)):
        log(f"role {EXECUTION_ROLE_NAME} not found: this sample did not create {AGENT_NAME}, leaving it alone")
        return
    delete_runtime(c["bedrock-agentcore-control"])   # runtime first, it uses the role and the image
    delete_role(iam)
    delete_repo(c["ecr"])
    log("agent destroyed")


if __name__ == "__main__":
    main()
