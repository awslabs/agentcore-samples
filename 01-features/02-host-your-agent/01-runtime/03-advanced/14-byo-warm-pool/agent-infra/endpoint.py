#!/usr/bin/env python3
"""Manage AgentCore Runtime endpoints with boto3.

  list                        endpoints with their live and target versions
  create NAME [--version V]   create an endpoint (default: the runtime's latest version)
  update NAME [--version V]   point an endpoint at a version (blue/green, canary)
  delete NAME                 delete an endpoint
  versions                    the runtime's versions, newest first

DEFAULT is managed by the service and always tracks the latest version, so
create/update/delete refuse it. The pool manager reads each endpoint's live
version and retires pooled sessions from older versions, so promoting an
endpoint drains and refills its pool by itself.
"""
import argparse
import time

from common import AGENT_NAME, clients, find_runtime, log

BUSY = {"CREATING", "UPDATING", "DELETING"}


def runtime_id(acc):
    rt = find_runtime({"bedrock-agentcore-control": acc})
    if rt is None:
        raise SystemExit(f"runtime {AGENT_NAME} not found: run './deploy.sh agent' first")
    return rt["agentRuntimeId"]


def latest_version(acc, rid):
    return str(acc.get_agent_runtime(agentRuntimeId=rid)["agentRuntimeVersion"])


def all_versions(acc, rid):
    out, kwargs = [], {}
    while True:
        resp = acc.list_agent_runtime_versions(agentRuntimeId=rid, **kwargs)
        out += resp.get("agentRuntimes", [])
        if not resp.get("nextToken"):
            return sorted(out, key=lambda v: int(v["agentRuntimeVersion"]), reverse=True)
        kwargs["nextToken"] = resp["nextToken"]


def all_endpoints(acc, rid):
    out, kwargs = [], {}
    while True:
        resp = acc.list_agent_runtime_endpoints(agentRuntimeId=rid, **kwargs)
        out += resp.get("runtimeEndpoints", [])
        if not resp.get("nextToken"):
            return out
        kwargs["nextToken"] = resp["nextToken"]


def wait(acc, rid, name, gone=False, tries=90):
    for _ in range(tries):
        try:
            ep = acc.get_agent_runtime_endpoint(agentRuntimeId=rid, endpointName=name)
        except acc.exceptions.ResourceNotFoundException:
            if gone:
                return None
            raise
        if ep["status"] == "READY" and not gone:
            return ep
        if ep["status"].endswith("FAILED"):
            raise SystemExit(f"endpoint {name} is {ep['status']}: {ep.get('failureReason', '')}")
        log(f"endpoint {name}: {ep['status']}")
        time.sleep(5)
    raise SystemExit(f"timed out waiting for endpoint {name}")


def guard(name):
    if name == "DEFAULT":
        raise SystemExit("DEFAULT is managed by the service and always points at the latest version")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("versions")
    for c in ("create", "update"):
        s = sub.add_parser(c)
        s.add_argument("name")
        s.add_argument("--version", help="runtime version (default: latest)")
    sub.add_parser("delete").add_argument("name")
    a = ap.parse_args()

    acc = clients()["bedrock-agentcore-control"]
    rid = runtime_id(acc)

    if a.cmd == "list":
        print(f"{'endpoint':24} {'live':>6} {'target':>7}  status")
        for ep in sorted(all_endpoints(acc, rid), key=lambda e: e["name"]):
            print(f"{ep['name']:24} {ep.get('liveVersion') or '-':>6} {ep.get('targetVersion') or '-':>7}  {ep['status']}")
        return
    if a.cmd == "versions":
        for v in all_versions(acc, rid):
            print(f"{v['agentRuntimeVersion']:>4}  {v.get('status', '')}  {v.get('lastUpdatedAt', '')}")
        return

    guard(a.name)
    if a.cmd == "delete":
        log(f"deleting endpoint {a.name}")
        acc.delete_agent_runtime_endpoint(agentRuntimeId=rid, endpointName=a.name)
        wait(acc, rid, a.name, gone=True)
        log(f"endpoint {a.name} deleted; run './deploy.sh unregister --arn <arn> --endpoint {a.name}'")
        return

    version = a.version or latest_version(acc, rid)
    exists = any(e["name"] == a.name for e in all_endpoints(acc, rid))
    if a.cmd == "create" and exists:
        raise SystemExit(f"endpoint {a.name} exists; use 'update'")
    if a.cmd == "update" and not exists:
        raise SystemExit(f"endpoint {a.name} not found; use 'create'")
    if a.cmd == "create":
        log(f"creating endpoint {a.name} -> version {version}")
        acc.create_agent_runtime_endpoint(agentRuntimeId=rid, name=a.name, agentRuntimeVersion=version,
                                          description="AgentCore warm pool sample",
                                          tags={"created-by": "agentcore-warm-pool"})
    else:
        log(f"pointing endpoint {a.name} at version {version}")
        acc.update_agent_runtime_endpoint(agentRuntimeId=rid, endpointName=a.name, agentRuntimeVersion=version)
    ep = wait(acc, rid, a.name)
    log(f"endpoint {a.name} READY, live version {ep.get('liveVersion')}")
    print(ep["agentRuntimeEndpointArn"])


if __name__ == "__main__":
    main()
