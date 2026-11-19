"""agentcore_boto3.py -- boto3 wrapper for AgentCore control+data plane
calls. --body-file for create/update is the raw create/update kwargs as
JSON, passed through as **kwargs (needs boto3 >= 1.44.0 for
hooksConfiguration and resourceConfigurations).
"""

import argparse
import json
import sys
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def _client(service: str, region: str, profile: str | None):
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    kwargs = {"region_name": region}
    if service == "bedrock-agentcore":
        # A first CPU inference can outlast botocore's default 60s read
        # timeout, and each automatic retry would re-invoke the session.
        kwargs["config"] = Config(read_timeout=900, retries={"max_attempts": 1})
    return session.client(service, **kwargs)


def _invoke_retrying_conflicts(client, args, payload: bytes, max_wait: float = 120.0):
    """Retry only RetryableConflictException (session still provisioning).

    General retries are off for the data plane (see _client): retrying a slow
    invocation would run it again.
    """
    start, delay = time.monotonic(), 2.0
    while True:
        try:
            return client.invoke_agent_runtime(
                agentRuntimeArn=args.agent_runtime_arn,
                runtimeSessionId=args.session_id,
                qualifier=args.qualifier,
                payload=payload,
                contentType="application/json",
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code != "RetryableConflictException" or time.monotonic() - start > max_wait:
                rid = exc.response.get("ResponseMetadata", {}).get("RequestId")
                print(f"[agentcore_boto3] {code} (RequestId: {rid})", file=sys.stderr)
                raise
            print(f"[agentcore_boto3] session still provisioning, retrying in {delay:.0f}s", file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, 30.0)


def find_arn(client, name: str) -> str | None:
    paginator = client.get_paginator("list_agent_runtimes")
    for page in paginator.paginate():
        for runtime in page.get("agentRuntimes", []):
            if runtime.get("agentRuntimeName") == name:
                return runtime.get("agentRuntimeArn")
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("action", choices=["find-arn", "create", "update", "get", "delete", "invoke"])
    p.add_argument("--region", required=True)
    p.add_argument("--profile", default=None)
    p.add_argument("--name", help="runtime name, for find-arn")
    p.add_argument("--agent-runtime-id", help="for update/get/delete")
    p.add_argument("--body-file", help="for create/update")
    p.add_argument("--agent-runtime-arn", help="for invoke")
    p.add_argument("--session-id", help="for invoke -- runtimeSessionId")
    p.add_argument("--qualifier", default="DEFAULT", help="for invoke -- endpoint name")
    p.add_argument("--payload-file", help="for invoke -- JSON request body")
    args = p.parse_args(argv)

    control_actions = {"find-arn", "create", "update", "get", "delete"}
    service = "bedrock-agentcore-control" if args.action in control_actions else "bedrock-agentcore"
    client = _client(service, args.region, args.profile)

    try:
        if args.action == "find-arn":
            if not args.name:
                p.error("find-arn requires --name")
            arn = find_arn(client, args.name)
            if arn:
                print(arn)
            return 0

        if args.action == "create":
            if not args.body_file:
                p.error("create requires --body-file")
            with open(args.body_file) as f:
                body = json.load(f)
            print(f"[agentcore_boto3] CreateAgentRuntime {body.get('agentRuntimeName')!r}", file=sys.stderr)
            resp = client.create_agent_runtime(**body)

        elif args.action == "update":
            if not (args.agent_runtime_id and args.body_file):
                p.error("update requires --agent-runtime-id and --body-file")
            with open(args.body_file) as f:
                body = json.load(f)
            print(f"[agentcore_boto3] UpdateAgentRuntime {args.agent_runtime_id}", file=sys.stderr)
            resp = client.update_agent_runtime(agentRuntimeId=args.agent_runtime_id, **body)

        elif args.action == "get":
            if not args.agent_runtime_id:
                p.error("get requires --agent-runtime-id")
            resp = client.get_agent_runtime(agentRuntimeId=args.agent_runtime_id)

        elif args.action == "delete":
            if not args.agent_runtime_id:
                p.error("delete requires --agent-runtime-id")
            resp = client.delete_agent_runtime(agentRuntimeId=args.agent_runtime_id)

        elif args.action == "invoke":
            if not (args.agent_runtime_arn and args.session_id):
                p.error("invoke requires --agent-runtime-arn and --session-id")
            payload = b"{}"
            if args.payload_file:
                with open(args.payload_file, "rb") as f:
                    payload = f.read()
            print(f"[agentcore_boto3] InvokeAgentRuntime session={args.session_id}", file=sys.stderr)
            resp = _invoke_retrying_conflicts(client, args, payload)
            body_stream = resp.pop("response", None)
            resp["body"] = json.loads(body_stream.read()) if body_stream else None

        else:  # pragma: no cover - argparse choices already restrict this
            p.error(f"unknown action {args.action!r}")
            return 2

        request_id = resp.pop("ResponseMetadata", {}).get("RequestId")
        if request_id:
            print(f"[agentcore_boto3] RequestId: {request_id}", file=sys.stderr)
        print(json.dumps(resp, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001 - surface any API/client error plainly
        print(f"[agentcore_boto3] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
