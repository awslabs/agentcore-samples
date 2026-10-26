#!/usr/bin/env python3
"""agentcore_boto3.py

Thin boto3 wrapper for the AgentCore control-plane calls the deploy scripts
need (find an existing runtime by name, create, update). Replaces two things
that used to be needed here:

1. The `aws` CLI (aws-cli ships its own bundled botocore, frozen at release
   time — it can lag behind whatever you `pip install` locally, so a
   just-released field can be usable from Python well before the CLI knows
   about it).
2. agentcore_raw_request.py's raw signed HTTP request (that existed only
   because `managedComputeConfiguration` was a preview-only field absent
   from any released botocore's service model). The service has since
   renamed/relaunched that as a plain top-level `platformVersion` string on
   CreateAgentRuntime/UpdateAgentRuntime — a normal, already-modeled field
   in any reasonably-current botocore, so a plain boto3 call handles it with
   no signing workaround needed.

The one gotcha this script exists to work around: botocore unconditionally
checks ~/.aws/models BEFORE its own bundled data (see
botocore.loaders.Loader.CUSTOMER_DATA_PATH) — a stale custom service model
dropped there for an earlier preview silently shadows newly-released fields
in a freshly pip-installed botocore, with no error, just a plain
"Unknown parameter" ParamValidationError that looks like the field doesn't
exist at all. _clean_session() below builds a session whose loader skips
that path entirely, so this script always sees the model that actually
ships with the installed botocore/boto3, regardless of what any other tool
has left in ~/.aws/models.

Usage:
    python3 agentcore_boto3.py find-arn --region us-west-2 --name ac_ctn_x_lambda_agent_200mb_V2
        # prints the ARN on stdout if a runtime with that name exists,
        # prints nothing (exit 0) if not — mirrors the old
        # `list-agent-runtimes --query ... || echo None` shape closely enough
        # that callers can keep testing for empty output.
    python3 agentcore_boto3.py create --region us-west-2 --body-file /tmp/body.json
    python3 agentcore_boto3.py update --region us-west-2 \
        --agent-runtime-id ac_ctn_x_lambda_agent_200mb_V2-abc123 --body-file /tmp/body.json

The body file is the exact create_agent_runtime/update_agent_runtime kwargs
as JSON (camelCase keys matching the API shape, e.g. agentRuntimeName,
agentRuntimeArtifact, roleArn, networkConfiguration, lifecycleConfiguration,
platformVersion) — passed straight through as **kwargs, so any field the
installed botocore's model knows about just works with no special-casing
here.

Credentials come from the normal boto3 resolution chain (env vars, profile,
SSO, role) — whatever is already exported in the calling shell, or
--profile.
"""

import argparse
import json
import sys

import boto3
import botocore.loaders
import botocore.session


def _clean_session(profile: str | None) -> boto3.Session:
    loader = botocore.loaders.Loader(
        extra_search_paths=[botocore.loaders.Loader.BUILTIN_DATA_PATH],
        include_default_search_paths=False,
    )
    session = botocore.session.Session(profile=profile)
    session.register_component("data_loader", loader)
    return boto3.Session(botocore_session=session)


def find_arn(client, name: str) -> str | None:
    paginator = client.get_paginator("list_agent_runtimes")
    for page in paginator.paginate():
        for runtime in page.get("agentRuntimes", []):
            if runtime.get("agentRuntimeName") == name:
                return runtime.get("agentRuntimeArn")
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("action", choices=["find-arn", "create", "update"])
    p.add_argument("--region", required=True)
    p.add_argument("--name", help="required for find-arn")
    p.add_argument("--agent-runtime-id", help="required for update")
    p.add_argument("--body-file", help="required for create/update")
    p.add_argument("--profile", default=None)
    args = p.parse_args(argv)

    if args.action == "find-arn" and not args.name:
        p.error("find-arn requires --name")
    if args.action in ("create", "update") and not args.body_file:
        p.error(f"{args.action} requires --body-file")
    if args.action == "update" and not args.agent_runtime_id:
        p.error("update requires --agent-runtime-id")

    session = _clean_session(args.profile)
    client = session.client("bedrock-agentcore-control", region_name=args.region)

    try:
        if args.action == "find-arn":
            arn = find_arn(client, args.name)
            if arn:
                print(arn)
            return 0

        with open(args.body_file) as f:
            body = json.load(f)

        if args.action == "create":
            print(f"[agentcore_boto3] CreateAgentRuntime {body.get('agentRuntimeName')!r}"
                  f" (region={args.region})", file=sys.stderr)
            resp = client.create_agent_runtime(**body)
        else:
            print(f"[agentcore_boto3] UpdateAgentRuntime {args.agent_runtime_id}"
                  f" (region={args.region})", file=sys.stderr)
            resp = client.update_agent_runtime(
                agentRuntimeId=args.agent_runtime_id, **body
            )
        resp.pop("ResponseMetadata", None)
        print(json.dumps(resp, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001 - surface any API/client error plainly
        print(f"[agentcore_boto3] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
