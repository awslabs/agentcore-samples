"""Stand up the spike harness and answer spikes 1 and 3 with a live call.

    python scripts/spikes/run_spike.py            # create + test + report
    python scripts/spikes/run_spike.py --cleanup   # delete everything it made

What it builds (all named `xaa-spike-*` so cleanup is unambiguous):

  echo Lambda + HTTP API   a target that reports the Authorization header it saw
  interceptor Lambda       injects a marker Authorization header, dumps its event
  gateway (authorizerType NONE, protocolType MCP)
    + openApiSchema target -> the HTTP API, credentialProviderType JWT_PASSTHROUGH
    + REQUEST interceptor

Then it calls tools/call through the gateway and reads the echo's answer.

  marker seen  -> the TRANSFORMED Authorization header is forwarded. The egress
                  design works: the interceptor can mint T_tool and inject it.
  original/absent -> JWT_PASSTHROUGH forwards the untransformed request; the
                  interceptor cannot supply the API credential this way.

The gateway uses authorizerType NONE deliberately: with no inbound Authorization
at all, any Authorization the echo target reports can only have come from the
interceptor. That isolates the mechanism from token validation.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "us-west-2"
PREFIX = "xaa-spike"
ECHO_FN = f"{PREFIX}-echo"
INTERCEPTOR_FN = f"{PREFIX}-interceptor"
ROLE = f"{PREFIX}-lambda-role"
GW_ROLE = f"{PREFIX}-gateway-role"
API_NAME = f"{PREFIX}-echo-api"
GW_NAME = f"{PREFIX}-gw"
MARKER = "SPIKE-INJECTED-TOKEN"
# The spike gateway is created without protocolConfiguration, so it advertises only
# this version. The sample proper may raise it; nothing here depends on the version.
MCP_VERSION = "2025-03-26"
HERE = Path(__file__).resolve().parent

iam = boto3.client("iam", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)
apigw = boto3.client("apigatewayv2", region_name=REGION)
acc = boto3.client("bedrock-agentcore-control", region_name=REGION)
sts = boto3.client("sts", region_name=REGION)
ACCOUNT = sts.get_caller_identity()["Account"]


def zip_one(path: Path, arcname: str = "handler.py") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo(arcname)
        info.external_attr = 0o644 << 16
        z.writestr(info, path.read_text())
    return buf.getvalue()


def ensure_lambda_role() -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        arn = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        iam.attach_role_policy(
            RoleName=ROLE,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        print(f"  created role {ROLE}; waiting 12s for propagation")
        time.sleep(12)
        return arn
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        return iam.get_role(RoleName=ROLE)["Role"]["Arn"]


def ensure_gateway_role() -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}},
            }
        ],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{INTERCEPTOR_FN}",
            }
        ],
    }
    try:
        arn = iam.create_role(RoleName=GW_ROLE, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        iam.put_role_policy(RoleName=GW_ROLE, PolicyName="invoke-interceptor", PolicyDocument=json.dumps(policy))
        print(f"  created role {GW_ROLE}; waiting 12s for propagation")
        time.sleep(12)
        return arn
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        iam.put_role_policy(RoleName=GW_ROLE, PolicyName="invoke-interceptor", PolicyDocument=json.dumps(policy))
        return iam.get_role(RoleName=GW_ROLE)["Role"]["Arn"]


def ensure_fn(name: str, src: Path, role_arn: str, env: dict | None = None) -> str:
    code = zip_one(src)
    try:
        arn = lam.create_function(
            FunctionName=name,
            Runtime="python3.12",
            Role=role_arn,
            Handler="handler.handler",
            Code={"ZipFile": code},
            Timeout=20,
            MemorySize=256,
            Environment={"Variables": env or {}},
        )["FunctionArn"]
        print(f"  created lambda {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceConflictException":
            raise
        lam.update_function_code(FunctionName=name, ZipFile=code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=name)
        if env:
            lam.update_function_configuration(FunctionName=name, Environment={"Variables": env})
            lam.get_waiter("function_updated_v2").wait(FunctionName=name)
        arn = lam.get_function(FunctionName=name)["Configuration"]["FunctionArn"]
        print(f"  updated lambda {name}")
    lam.get_waiter("function_active_v2").wait(FunctionName=name)
    return arn


def ensure_http_api(echo_arn: str) -> str:
    for a in apigw.get_apis()["Items"]:
        if a["Name"] == API_NAME:
            return a["ApiEndpoint"]
    api = apigw.create_api(Name=API_NAME, ProtocolType="HTTP", Target=echo_arn, RouteKey="ANY /{proxy+}")
    try:
        lam.add_permission(
            FunctionName=echo_arn,
            StatementId="apigw-invoke",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{REGION}:{ACCOUNT}:{api['ApiId']}/*",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceConflictException":
            raise
    print(f"  created HTTP API {API_NAME}")
    return api["ApiEndpoint"]


def openapi(endpoint: str) -> str:
    return json.dumps(
        {
            "openapi": "3.0.0",
            "info": {"title": "spike echo", "version": "1.0.0"},
            "servers": [{"url": endpoint}],
            "paths": {
                "/echo": {
                    "get": {
                        "operationId": "echo_headers",
                        "summary": "Return the headers this API received",
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {"application/json": {"schema": {"type": "object"}}},
                            }
                        },
                    }
                }
            },
        }
    )


def ensure_gateway(gw_role: str, interceptor_arn: str, endpoint: str) -> tuple[str, str]:
    gw = next((g for g in acc.list_gateways()["items"] if g["name"] == GW_NAME), None)
    cfg = {
        "interceptor": {"lambda": {"arn": interceptor_arn}},
        "interceptionPoints": ["REQUEST"],
        "inputConfiguration": {"passRequestHeaders": True},
    }
    if gw is None:
        r = acc.create_gateway(
            name=GW_NAME,
            roleArn=gw_role,
            protocolType="MCP",
            authorizerType="NONE",
            interceptorConfigurations=[cfg],
            exceptionLevel="DEBUG",
            description="XAA spike: does JWT_PASSTHROUGH forward a transformed Authorization header?",
        )
        gw_id = r["gatewayId"]
        print(f"  created gateway {gw_id}")
    else:
        gw_id = gw["gatewayId"]
        acc.update_gateway(
            gatewayIdentifier=gw_id,
            name=GW_NAME,
            roleArn=gw_role,
            protocolType="MCP",
            authorizerType="NONE",
            interceptorConfigurations=[cfg],
            exceptionLevel="DEBUG",
        )
        print(f"  reusing gateway {gw_id}")

    for _ in range(40):
        d = acc.get_gateway(gatewayIdentifier=gw_id)
        if d["status"] in ("READY", "FAILED"):
            break
        time.sleep(5)
    if d["status"] != "READY":
        sys.exit(f"gateway not READY: {d['status']} {d.get('statusReasons')}")

    tname = "echo"
    tgt = next(
        (t for t in acc.list_gateway_targets(gatewayIdentifier=gw_id)["items"] if t["name"] == tname),
        None,
    )
    target_cfg = {"mcp": {"openApiSchema": {"inlinePayload": openapi(endpoint)}}}
    # No credentialProviderConfigurations at all.
    #
    # Probed live: on an MCP-protocol gateway NO target type accepts
    # JWT_PASSTHROUGH -- openApiSchema and mcpServer both reject it explicitly, and
    # http.passthrough is rejected because http.* targets require a gateway with no
    # protocol type. So the question becomes whether the interceptor's transformed
    # Authorization header is forwarded to the upstream anyway, with the target
    # carrying no outbound credential of its own. Omitting the field is accepted.
    if tgt is None:
        t = acc.create_gateway_target(
            gatewayIdentifier=gw_id,
            name=tname,
            targetConfiguration=target_cfg,
        )
        tid = t["targetId"]
        print(f"  created target {tid} (no outbound credential provider)")
    else:
        tid = tgt["targetId"]
        print(f"  reusing target {tid}")
        acc.update_gateway_target(gatewayIdentifier=gw_id, targetId=tid, name=tname, targetConfiguration=target_cfg)
    for _ in range(40):
        d2 = acc.get_gateway_target(gatewayIdentifier=gw_id, targetId=tid)
        if d2["status"] in ("READY", "FAILED", "UPDATE_UNSUCCESSFUL"):
            break
        time.sleep(5)
    print(f"  target status: {d2['status']} {d2.get('statusReasons') or ''}")
    return gw_id, d["gatewayUrl"]


def mcp(url: str, payload: dict, sid: str | None = None) -> tuple[dict, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_VERSION,
    }
    if sid:
        headers["Mcp-Session-Id"] = sid
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.headers, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.headers, f"HTTP {e.code}: {e.read().decode()[:600]}"


def run_test(gw_url: str) -> None:
    print("\n--- calling the gateway ---")
    h, _ = mcp(
        gw_url,
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "spike", "version": "1"},
            },
        },
    )
    sid = h.get("Mcp-Session-Id") or h.get("mcp-session-id")
    mcp(gw_url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    _, tl = mcp(gw_url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, sid)
    try:
        tools = [t["name"] for t in json.loads(tl)["result"]["tools"]]
    except (ValueError, KeyError, TypeError):
        sys.exit(f"tools/list failed: {tl[:400]}")
    print(f"  tools: {tools}")
    tool = next((t for t in tools if t.endswith("echo_headers")), None)
    if not tool:
        sys.exit("echo_headers not advertised")

    _, res = mcp(
        gw_url,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": {}}},
        sid,
    )
    print(f"  raw tools/call response:\n    {res[:900]}")

    verdict = "INCONCLUSIVE"
    if MARKER in res:
        verdict = "TRANSFORMED header IS forwarded -- egress design works"
    elif "authorization_seen" in res and "<ABSENT>" in res:
        verdict = "NO Authorization reached the target -- transformed header dropped"
    elif "authorization_seen" in res:
        verdict = "an Authorization reached the target, but NOT our marker"
    print(f"\n==> SPIKE 1 VERDICT: {verdict}")
    print("==> SPIKE 3: read the interceptor's INTERCEPTOR_EVENT log line:")
    print(f"    aws logs tail /aws/lambda/{INTERCEPTOR_FN} --region {REGION} --since 10m")


def cleanup() -> None:
    print("--- cleanup ---")
    for g in acc.list_gateways()["items"]:
        if g["name"] != GW_NAME:
            continue
        gid = g["gatewayId"]
        for t in acc.list_gateway_targets(gatewayIdentifier=gid)["items"]:
            acc.delete_gateway_target(gatewayIdentifier=gid, targetId=t["targetId"])
            print(f"  deleted target {t['targetId']}")
        time.sleep(5)
        acc.delete_gateway(gatewayIdentifier=gid)
        print(f"  deleted gateway {gid}")
    for a in apigw.get_apis()["Items"]:
        if a["Name"] == API_NAME:
            apigw.delete_api(ApiId=a["ApiId"])
            print(f"  deleted api {a['ApiId']}")
    for fn in (ECHO_FN, INTERCEPTOR_FN):
        try:
            lam.delete_function(FunctionName=fn)
            print(f"  deleted lambda {fn}")
        except ClientError:
            pass
    for role, inline in ((ROLE, []), (GW_ROLE, ["invoke-interceptor"])):
        try:
            for p in inline:
                iam.delete_role_policy(RoleName=role, PolicyName=p)
            for p in iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]:
                iam.detach_role_policy(RoleName=role, PolicyArn=p["PolicyArn"])
            iam.delete_role(RoleName=role)
            print(f"  deleted role {role}")
        except ClientError:
            pass
    print("  done")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()
    if args.cleanup:
        cleanup()
        return

    print(f"account {ACCOUNT} region {REGION}")
    lrole = ensure_lambda_role()
    echo_arn = ensure_fn(ECHO_FN, HERE / "echo_lambda.py", lrole)
    icept_arn = ensure_fn(INTERCEPTOR_FN, HERE / "interceptor_spike.py", lrole, {"SPIKE_MARKER": MARKER})
    endpoint = ensure_http_api(echo_arn)
    print(f"  echo endpoint: {endpoint}")
    grole = ensure_gateway_role()
    _, gw_url = ensure_gateway(grole, icept_arn, endpoint)
    print(f"  gateway url: {gw_url}")
    run_test(gw_url)


if __name__ == "__main__":
    main()
