#!/usr/bin/env python3
"""agentcore_raw_request.py

Signed HTTP request against the Bedrock AgentCore control plane, for fields
the installed `aws` CLI / botocore do not yet know about — e.g. the preview
name for what later launched as `platformVersion` (V1 = Original Runtime,
V2 = New Runtime). `aws bedrock-agentcore-control create-agent-runtime
--cli-input-json` rejects that key locally with a ParamValidation error
before any request is sent, because the field is absent from the installed
service model; this script builds and signs the HTTP request directly
instead of going through the CLI's shape validation.

Generalizes an internal predecessor script (not part of this public sample):
any artifact (code or container), create or update, any extra top-level body
fields. Prod only — no stage switch.

Unused in this copy: agentcore_boto3.py now handles create/update via plain
boto3 instead (see its docstring for why), which picks up `platformVersion`
(the field this file's signed request existed for) directly, no signing
workaround needed. Kept only for reference.

Credentials come from the normal boto3 resolution chain (env vars, profile,
SSO, role) — whatever is already exported in the calling shell.

TODO: once `managedComputeConfiguration` lands in the installed botocore's
service model for bedrock-agentcore-control (official launch, not preview),
delete this file and call boto3's own `create_agent_runtime` /
`update_agent_runtime` directly instead. That also removes the need for
`resolve_endpoint`/`CONTROL_ENDPOINT_TEMPLATE` above: boto3 resolves the
regional control-plane endpoint on its own, same as every other AWS API call
in this project already does.

Usage:
    python3 agentcore_raw_request.py create --region us-west-2 \
        --body-file /tmp/body.json
    python3 agentcore_raw_request.py update --region us-west-2 \
        --agent-runtime-id ac_zip_x_lambda_bench_agent-abc123 \
        --body-file /tmp/body.json

Prints the response JSON to stdout on success; exits non-zero with the
error body on failure.
"""

import argparse
import json
import sys

import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

# SigV4 signingName for the AgentCore control plane is "bedrock-agentcore"
# (NOT "bedrock-agentcore-control", which is only the endpoint/service id).
CONTROL_SERVICE = "bedrock-agentcore"

CONTROL_ENDPOINT_TEMPLATE = "https://bedrock-agentcore-control.{region}.amazonaws.com"


def resolve_endpoint(region: str) -> str:
    return CONTROL_ENDPOINT_TEMPLATE.format(region=region)


def signed_request(method: str, url: str, region: str, body: bytes, profile: str | None):
    session = Session(profile=profile) if profile else Session()
    creds = session.get_credentials()
    if creds is None:
        raise SystemExit(
            "no AWS credentials found (checked env vars, profile, SSO, role)"
        )
    frozen = creds.get_frozen_credentials()
    aws_request = AWSRequest(
        method=method, url=url, data=body,
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(frozen, CONTROL_SERVICE, region).add_auth(aws_request)
    return requests.request(
        method=method, url=url, headers=dict(aws_request.headers),
        data=body, timeout=30.0,
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("action", choices=["create", "update"])
    p.add_argument("--region", required=True)
    p.add_argument("--agent-runtime-id", help="required for 'update'")
    p.add_argument("--body-file", required=True, help="JSON request body")
    p.add_argument("--profile", default=None)
    args = p.parse_args(argv)

    if args.action == "update" and not args.agent_runtime_id:
        p.error("update requires --agent-runtime-id")

    endpoint = resolve_endpoint(args.region)
    if args.action == "create":
        url = endpoint.rstrip("/") + "/runtimes/"
    else:
        url = endpoint.rstrip("/") + "/runtimes/" + args.agent_runtime_id + "/"

    with open(args.body_file, "rb") as f:
        body = f.read()

    print(f"[agentcore_raw_request] PUT {url}", file=sys.stderr)
    resp = signed_request("PUT", url, args.region, body, args.profile)
    if not resp.ok:
        print(
            f"[agentcore_raw_request] HTTP {resp.status_code}: {resp.text}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(resp.json()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
