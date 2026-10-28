"""agentcore_boto3.py -- boto3 wrapper for AgentCore control+data plane
calls. --body-file for create/update is the raw create/update kwargs as
JSON, passed through as **kwargs.
"""

import argparse
import json
import sys

import boto3


def _client(service: str, region: str, profile: str | None):
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    return session.client(service, region_name=region)


def find_arn(client, name: str) -> str | None:
    paginator = client.get_paginator("list_agent_runtimes")
    for page in paginator.paginate():
        for runtime in page.get("agentRuntimes", []):
            if runtime.get("agentRuntimeName") == name:
                return runtime.get("agentRuntimeArn")
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "action",
        choices=[
            "find-arn", "create", "update", "get", "delete",
            "create-endpoint", "get-endpoint", "delete-endpoint",
            "invoke", "exec",
        ],
    )
    p.add_argument("--region", required=True)
    p.add_argument("--profile", default=None)
    p.add_argument("--name", help="runtime name, for find-arn")
    p.add_argument("--agent-runtime-id", help="for update/get/delete/create-endpoint/get-endpoint/delete-endpoint")
    p.add_argument("--endpoint-name", default="DEFAULT", help="for create-endpoint/get-endpoint/delete-endpoint")
    p.add_argument("--agent-runtime-version", help="for create-endpoint")
    p.add_argument("--body-file", help="for create/update/create-endpoint")
    p.add_argument("--agent-runtime-arn", help="for invoke/exec")
    p.add_argument("--session-id", help="for invoke/exec -- runtimeSessionId")
    p.add_argument("--qualifier", default="DEFAULT", help="for invoke -- endpoint name")
    p.add_argument("--payload-file", help="for invoke -- JSON request body")
    p.add_argument("--command", help="for exec -- shell command to run inside the running session")
    args = p.parse_args(argv)

    control_actions = {
        "find-arn", "create", "update", "get", "delete",
        "create-endpoint", "get-endpoint", "delete-endpoint",
    }
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

        elif args.action == "create-endpoint":
            if not args.agent_runtime_id:
                p.error("create-endpoint requires --agent-runtime-id")
            kwargs = {"agentRuntimeId": args.agent_runtime_id, "name": args.endpoint_name}
            if args.agent_runtime_version:
                kwargs["agentRuntimeVersion"] = args.agent_runtime_version
            print(f"[agentcore_boto3] CreateAgentRuntimeEndpoint {args.endpoint_name!r} on {args.agent_runtime_id}", file=sys.stderr)
            resp = client.create_agent_runtime_endpoint(**kwargs)

        elif args.action == "get-endpoint":
            if not args.agent_runtime_id:
                p.error("get-endpoint requires --agent-runtime-id")
            resp = client.get_agent_runtime_endpoint(
                agentRuntimeId=args.agent_runtime_id, endpointName=args.endpoint_name
            )

        elif args.action == "delete-endpoint":
            if not args.agent_runtime_id:
                p.error("delete-endpoint requires --agent-runtime-id")
            resp = client.delete_agent_runtime_endpoint(
                agentRuntimeId=args.agent_runtime_id, endpointName=args.endpoint_name
            )

        elif args.action == "invoke":
            if not (args.agent_runtime_arn and args.session_id):
                p.error("invoke requires --agent-runtime-arn and --session-id")
            payload = b"{}"
            if args.payload_file:
                with open(args.payload_file, "rb") as f:
                    payload = f.read()
            print(f"[agentcore_boto3] InvokeAgentRuntime session={args.session_id}", file=sys.stderr)
            resp = client.invoke_agent_runtime(
                agentRuntimeArn=args.agent_runtime_arn,
                runtimeSessionId=args.session_id,
                qualifier=args.qualifier,
                payload=payload,
                contentType="application/json",
            )
            body_stream = resp.pop("response", None)
            resp["body"] = json.loads(body_stream.read()) if body_stream else None

        elif args.action == "exec":
            if not (args.agent_runtime_arn and args.session_id and args.command):
                p.error("exec requires --agent-runtime-arn, --session-id, and --command")
            print(f"[agentcore_boto3] InvokeAgentRuntimeCommand session={args.session_id} command={args.command!r}", file=sys.stderr)
            resp = client.invoke_agent_runtime_command(
                agentRuntimeArn=args.agent_runtime_arn,
                runtimeSessionId=args.session_id,
                body={"command": args.command},
            )
            stream = resp.pop("stream", None)
            stdout_parts, stderr_parts = [], []
            if stream:
                for event in stream:
                    delta = event.get("chunk", {}).get("contentDelta", {})
                    if delta.get("stdout"):
                        stdout_parts.append(delta["stdout"])
                    if delta.get("stderr"):
                        stderr_parts.append(delta["stderr"])
            resp["stdout"] = "".join(stdout_parts)
            resp["stderr"] = "".join(stderr_parts)

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
