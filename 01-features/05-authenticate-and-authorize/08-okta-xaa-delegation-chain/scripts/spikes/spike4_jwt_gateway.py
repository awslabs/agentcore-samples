"""Spike 4: does the interceptor rewrite still win on a CUSTOM_JWT gateway with Cedar?

    .venv/bin/python scripts/spikes/spike4_jwt_gateway.py
    .venv/bin/python scripts/spikes/spike4_jwt_gateway.py --cleanup

Spike 1 proved the rewrite works, but on a gateway with `authorizerType: NONE`, no
policy engine, and a client that sent no custom header. This closes the three
questions that remain before the real gateway can be built:

  Q1  Does a CUSTOM inbound header (X-Okta-Id-Token) reach the interceptor?
      The ID token has to arrive this way -- the tool body would put it in the
      model's context. If it does NOT arrive, the architecture changes.

  Q2  Does the interceptor's rewritten Authorization still reach the upstream when
      the gateway itself validated a real JWT? It may re-assert the validated token.

  Q3  Does Cedar in ENFORCE mode see AgentCore::OAuthUser with the token's claims
      as principal tags, alongside a REQUEST interceptor?

Builds: echo Lambda + HTTP API, an interceptor, a policy engine with one permit
policy, and a gateway with CUSTOM_JWT against AS 1. Signs you in interactively to
get a real T_user, then calls tools/call and reports a verdict per question.

Everything is named `xaa-spike4-*`. Nothing touches .env.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "deploy"))
from _common import env, load_env, must_env, okta_org_url

HERE = Path(__file__).resolve().parent
REGION = env("AWS_REGION", "us-west-2")
PREFIX = "xaa-spike4"
ECHO_FN = f"{PREFIX}-echo"
ICEPT_FN = f"{PREFIX}-interceptor"
LAMBDA_ROLE = f"{PREFIX}-lambda-role"
GW_ROLE = f"{PREFIX}-gateway-role"
API_NAME = f"{PREFIX}-echo-api"
GW_NAME = f"{PREFIX}-gw"
# Policy engine and policy names allow NO hyphens: the API enforces
# [A-Za-z][A-Za-z0-9_]* (max 48), unlike gateway and target names which do.
PE_NAME = "xaaSpike4Policies"
POLICY_NAME = "xaaSpike4PermitOAuthUser"
MARKER = "SPIKE4-REWRITTEN-BY-INTERCEPTOR"
ID_TOKEN_HEADER = "X-Okta-Id-Token"
# <target name>___<openapi operationId>. Named directly so the spike never calls
# tools/list, which the gateway denies under any Cedar policy.
TOOL_NAME = "echo___echo_headers"
PRINCIPAL_AGNOSTIC = os.environ.get("PRINCIPAL_AGNOSTIC", "").lower() == "true"
MCP_VERSION = "2025-03-26"

iam = boto3.client("iam", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)
apigw = boto3.client("apigatewayv2", region_name=REGION)
acc = boto3.client("bedrock-agentcore-control", region_name=REGION)
ACCOUNT = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]


def zip_src(path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo("handler.py")
        info.external_attr = 0o644 << 16
        z.writestr(info, path.read_text())
    return buf.getvalue()


def ensure_role(name: str, service: str, policy: dict | None, managed: str | None) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole"}],
    }
    if service == "bedrock-agentcore.amazonaws.com":
        trust["Statement"][0]["Condition"] = {"StringEquals": {"aws:SourceAccount": ACCOUNT}}
    try:
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        if managed:
            iam.attach_role_policy(RoleName=name, PolicyArn=managed)
        if policy:
            iam.put_role_policy(RoleName=name, PolicyName="inline", PolicyDocument=json.dumps(policy))
        print(f"  created role {name}; waiting 12s for propagation")
        time.sleep(12)
        return arn
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        if policy:
            iam.put_role_policy(RoleName=name, PolicyName="inline", PolicyDocument=json.dumps(policy))
        return iam.get_role(RoleName=name)["Role"]["Arn"]


def ensure_fn(name: str, src: Path, role: str, envvars: dict) -> str:
    code = zip_src(src)
    try:
        arn = lam.create_function(
            FunctionName=name,
            Runtime="python3.12",
            Role=role,
            Handler="handler.handler",
            Code={"ZipFile": code},
            Timeout=25,
            MemorySize=256,
            Environment={"Variables": envvars},
        )["FunctionArn"]
        print(f"  created lambda {name}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
        lam.update_function_code(FunctionName=name, ZipFile=code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=name)
        lam.update_function_configuration(FunctionName=name, Environment={"Variables": envvars})
        lam.get_waiter("function_updated_v2").wait(FunctionName=name)
        arn = lam.get_function(FunctionName=name)["Configuration"]["FunctionArn"]
        print(f"  updated lambda {name}")
    lam.get_waiter("function_active_v2").wait(FunctionName=name)
    return arn


def ensure_api(echo_arn: str) -> str:
    for api in apigw.get_apis()["Items"]:
        if api["Name"] == API_NAME:
            return api["ApiEndpoint"]
    api = apigw.create_api(Name=API_NAME, ProtocolType="HTTP", Target=echo_arn, RouteKey="ANY /{proxy+}")
    try:
        lam.add_permission(
            FunctionName=echo_arn,
            StatementId="apigw",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{REGION}:{ACCOUNT}:{api['ApiId']}/*",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
    print(f"  created HTTP API {API_NAME}")
    return api["ApiEndpoint"]


def openapi(endpoint: str) -> str:
    return json.dumps(
        {
            "openapi": "3.0.0",
            "info": {"title": "spike4 echo", "version": "1.0.0"},
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


def ensure_policy_engine(gateway_arn: str | None) -> str:
    existing = next((p for p in acc.list_policy_engines().get("policyEngines", []) if p["name"] == PE_NAME), None)
    if existing:
        pe_id = existing["policyEngineId"]
        print(f"  reusing policy engine {pe_id}")
    else:
        pe_id = acc.create_policy_engine(name=PE_NAME, description="XAA spike 4")["policyEngineId"]
        print(f"  created policy engine {pe_id}")
    for _ in range(30):
        if acc.get_policy_engine(policyEngineId=pe_id)["status"] in ("ACTIVE", "CREATE_FAILED"):
            break
        time.sleep(5)

    if gateway_arn:
        # Allowlist ONE tool action -- the shape a real deployment uses.
        #
        # Note that tools/LIST stays denied under a policy like this, and an
        # unconstrained `action` does not fix it either. That is a known gateway
        # limitation, not a Cedar authoring mistake. It does not block the flow: MCP
        # permits tools/call without a prior tools/list, and an agent given the tool
        # name can call it directly. The spike therefore skips discovery.
        #
        # The condition is the Q3 test: if Cedar cannot see the inbound JWT's claims
        # as principal tags, hasTag("sub") is false, the permit does not match, and
        # default-deny rejects the call.
        if PRINCIPAL_AGNOSTIC:
            # Never mentions the principal. If evaluation STILL fails internally once
            # the interceptor has clobbered Authorization, the failure is in building
            # the principal entity itself, not in the policy text -- which would rule
            # out keeping any Cedar policy on a credential-injecting gateway.
            statement = (
                "permit(\n"
                "  principal,\n"
                f'  action == AgentCore::Action::"{TOOL_NAME}",\n'
                f'  resource == AgentCore::Gateway::"{gateway_arn}"\n'
                ");\n"
            )
        else:
            statement = (
                "permit(\n"
                "  principal is AgentCore::OAuthUser,\n"
                f'  action == AgentCore::Action::"{TOOL_NAME}",\n'
                f'  resource == AgentCore::Gateway::"{gateway_arn}"\n'
                ") when {\n"
                '  principal.hasTag("sub")\n'
                "};\n"
            )
        # Replace rather than skip, so editing the statement takes effect on a re-run
        # instead of silently keeping the old policy.
        for existing in acc.list_policies(policyEngineId=pe_id).get("policies", []):
            if existing["name"] == POLICY_NAME:
                acc.delete_policy(policyEngineId=pe_id, policyId=existing["policyId"])
                print(f"  deleting previous policy {existing['policyId']}")
        # DeletePolicy is asynchronous: creating the same name immediately fails with
        # ConflictException "Policy with the same name already exists". Wait it out.
        for _ in range(24):
            names = [p["name"] for p in acc.list_policies(policyEngineId=pe_id).get("policies", [])]
            if POLICY_NAME not in names:
                break
            time.sleep(5)
        else:
            print(f"  ⚠ {POLICY_NAME} still present after 120s; create will likely conflict")
        acc.create_policy(
            policyEngineId=pe_id,
            name=POLICY_NAME,
            # PolicyDefinition members are cedar | policy | policyGeneration.
            definition={"cedar": {"statement": statement}},
            enforcementMode="ACTIVE",
            validationMode="IGNORE_ALL_FINDINGS",
        )
        print(f'  created Cedar policy: permit OAuthUser on "{TOOL_NAME}" when hasTag("sub")')
        # A policy in CREATING is not yet enforced; testing against it races.
        for _ in range(24):
            pols = {p["name"]: p for p in acc.list_policies(policyEngineId=pe_id).get("policies", [])}
            status = (pols.get(POLICY_NAME) or {}).get("status")
            if status not in ("CREATING", None):
                print(f"  policy status: {status}")
                break
            time.sleep(5)
    return pe_id


def ensure_gateway(gw_role: str, icept_arn: str, endpoint: str, pe_id: str, mode: str) -> tuple[str, str]:
    disco = f"{must_env('AGENTCORE_AS_ISSUER')}/.well-known/openid-configuration"
    audience = env("AGENTCORE_AUDIENCE", "api://agentcore")
    authorizer = {
        "customJWTAuthorizer": {
            "discoveryUrl": disco,
            "allowedAudience": [audience],
        }
    }
    icept = [
        {
            "interceptor": {"lambda": {"arn": icept_arn}},
            "interceptionPoints": ["REQUEST"],
            # Q1 depends on this: the interceptor must be shown inbound headers.
            "inputConfiguration": {"passRequestHeaders": True},
        }
    ]
    pe_cfg = {"arn": f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:policy-engine/{pe_id}", "mode": mode}

    gw = next((g for g in acc.list_gateways()["items"] if g["name"] == GW_NAME), None)
    common = {
        "name": GW_NAME,
        "roleArn": gw_role,
        "protocolType": "MCP",
        "authorizerType": "CUSTOM_JWT",
        "authorizerConfiguration": authorizer,
        "interceptorConfigurations": icept,
        "policyEngineConfiguration": pe_cfg,
        "exceptionLevel": "DEBUG",
    }
    if gw is None:
        gw_id = acc.create_gateway(description="XAA spike 4", **common)["gatewayId"]
        print(f"  created gateway {gw_id}")
    else:
        gw_id = gw["gatewayId"]
        acc.update_gateway(gatewayIdentifier=gw_id, **common)
        print(f"  reusing gateway {gw_id}")
    for _ in range(40):
        detail = acc.get_gateway(gatewayIdentifier=gw_id)
        if detail["status"] in ("READY", "FAILED"):
            break
        time.sleep(5)
    if detail["status"] != "READY":
        sys.exit(f"  gateway not READY: {detail['status']} {detail.get('statusReasons')}")

    target = next(
        (t for t in acc.list_gateway_targets(gatewayIdentifier=gw_id)["items"] if t["name"] == "echo"),
        None,
    )
    cfg = {"mcp": {"openApiSchema": {"inlinePayload": openapi(endpoint)}}}
    if target is None:
        tid = acc.create_gateway_target(gatewayIdentifier=gw_id, name="echo", targetConfiguration=cfg)["targetId"]
        print(f"  created target {tid} (no outbound credential provider)")
    else:
        tid = target["targetId"]
        acc.update_gateway_target(gatewayIdentifier=gw_id, targetId=tid, name="echo", targetConfiguration=cfg)
        print(f"  reusing target {tid}")
    for _ in range(40):
        td = acc.get_gateway_target(gatewayIdentifier=gw_id, targetId=tid)
        if td["status"] in ("READY", "FAILED", "UPDATE_UNSUCCESSFUL"):
            break
        time.sleep(5)
    print(f"  target status: {td['status']} {td.get('statusReasons') or ''}")
    return gw_id, detail["gatewayUrl"]


def mcp_call(url: str, payload: dict, token: str, id_token: str, sid: str | None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_VERSION,
        "Authorization": f"Bearer {token}",
        ID_TOKEN_HEADER: id_token,
    }
    if sid:
        headers["Mcp-Session-Id"] = sid
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        return resp.headers, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.headers, f"HTTP {exc.code}: {exc.read().decode()[:700]}"


def run_test(gw_url: str, token: str, id_token: str, args) -> None:
    print("\n--- calling the gateway with Authorization + " + ID_TOKEN_HEADER + " ---")
    init = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {"protocolVersion": MCP_VERSION, "capabilities": {}, "clientInfo": {"name": "s4", "version": "1"}},
    }
    hdrs, raw = mcp_call(gw_url, init, token, id_token, None)
    if raw.startswith("HTTP "):
        sys.exit(f"  ✗ initialize failed: {raw[:400]}")
    sid = hdrs.get("Mcp-Session-Id") or hdrs.get("mcp-session-id")
    mcp_call(gw_url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, token, id_token, sid)

    # Deliberately NO tools/list. The gateway denies listing under any Cedar policy
    # (a known limitation), and MCP does not require discovery before a call -- the
    # tool name is enough. Reporting it for the record, then moving on.
    _, listing = mcp_call(
        gw_url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, token, id_token, sid
    )
    listed_ok = '"tools"' in listing
    print(f"  tools/list (informational): {'succeeded' if listed_ok else 'denied by policy, as expected'}")

    print(f"  calling {TOOL_NAME} directly")
    _, result = mcp_call(
        gw_url,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": TOOL_NAME, "arguments": {}}},
        token,
        id_token,
        sid,
    )
    print(f"\n  tools/call response:\n    {result[:800]}")

    print("\n================ SPIKE 4 VERDICT ================")
    print(f"  injected: {args.rewrite_with} | policy engine: {args.policy_mode}")
    internal = "Internal Failure" in result
    ok = '"isError":false' in result
    if internal:
        print("  RESULT: policy evaluation ERRORED (not a deny)")
    elif ok:
        print("  RESULT: call SUCCEEDED end to end")
    else:
        print("  RESULT: call failed -- see the response above")
    # Q2 asks whether the upstream saw what the interceptor put there. In marker mode
    # that is the marker; in t_tool/same mode it is a JWT, so look for one instead of
    # the marker -- checking for MARKER alone reported a false FAIL on a passing run.
    if args.rewrite_with == "marker":
        q2 = MARKER in result
    elif args.rewrite_with == "none":
        q2 = None
    else:
        q2 = "Bearer eyJ" in result
    label = "n/a (nothing injected)" if q2 is None else ("PASS" if q2 else "FAIL")
    print(f"  Q2 injected credential reaches upstream : {label}")
    if q2 is False:
        print("     the upstream did not see the interceptor's Authorization")
    denied = "denied" in result.lower() or "not authorized" in result.lower()
    print(f"  Q3 Cedar ENFORCE + interceptor    : {'FAIL (denied)' if denied else 'PASS (permitted)'}")
    if denied:
        print("     the OAuthUser policy did not match -- Cedar may not see JWT claims")
    print("\n  Q1 custom header reached the interceptor: read the log line below")
    print(f"    aws logs tail /aws/lambda/{ICEPT_FN} --region {REGION} --since 10m \\")
    print("      | grep -E 'ID_TOKEN_HEADER_SEEN|CONTEXT_PRESENT|INBOUND_HEADER_NAMES'")


def cleanup() -> None:
    print("--- cleanup ---")
    for gw in acc.list_gateways()["items"]:
        if gw["name"] != GW_NAME:
            continue
        gid = gw["gatewayId"]
        for t in acc.list_gateway_targets(gatewayIdentifier=gid)["items"]:
            acc.delete_gateway_target(gatewayIdentifier=gid, targetId=t["targetId"])
            print(f"  deleted target {t['targetId']}")
        time.sleep(5)
        acc.delete_gateway(gatewayIdentifier=gid)
        print(f"  deleted gateway {gid}")
    for pe in acc.list_policy_engines().get("policyEngines", []):
        if pe["name"] != PE_NAME:
            continue
        pe_id = pe["policyEngineId"]
        for pol in acc.list_policies(policyEngineId=pe_id).get("policies", []):
            acc.delete_policy(policyEngineId=pe_id, policyId=pol["policyId"])
            print(f"  deleting policy {pol['policyId']}")
        # DeletePolicy is asynchronous, and DeletePolicyEngine refuses while any
        # policy remains ("still contains 1 policy and cannot be deleted").
        for _ in range(24):
            if not acc.list_policies(policyEngineId=pe_id).get("policies", []):
                break
            time.sleep(5)
        acc.delete_policy_engine(policyEngineId=pe_id)
        print(f"  deleted policy engine {pe['policyEngineId']}")
    for api in apigw.get_apis()["Items"]:
        if api["Name"] == API_NAME:
            apigw.delete_api(ApiId=api["ApiId"])
            print(f"  deleted api {api['ApiId']}")
    for fn in (ECHO_FN, ICEPT_FN):
        try:
            lam.delete_function(FunctionName=fn)
            print(f"  deleted lambda {fn}")
        except ClientError:
            pass
    for role in (LAMBDA_ROLE, GW_ROLE):
        try:
            for p in iam.list_role_policies(RoleName=role)["PolicyNames"]:
                iam.delete_role_policy(RoleName=role, PolicyName=p)
            for p in iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]:
                iam.detach_role_policy(RoleName=role, PolicyArn=p["PolicyArn"])
            iam.delete_role(RoleName=role)
            print(f"  deleted role {role}")
        except ClientError:
            pass
    print("  done")


TOKEN_CACHE = Path("/tmp/xaa-spike4-tokens.json")


def cached_tokens() -> dict | None:
    """Reuse a recent sign-in so iterating does not need repeated MFA.

    Deliberately in /tmp, never in the repo: these are real tokens.
    """
    if not TOKEN_CACHE.exists():
        return None
    try:
        blob = json.loads(TOKEN_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if time.time() - blob.get("_at", 0) > 45 * 60:
        print("  cached tokens are older than 45 minutes -- signing in again")
        return None
    print(f"  reusing tokens cached {int(time.time() - blob['_at'])}s ago ({TOKEN_CACHE})")
    return blob


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument(
        "--policy-mode",
        choices=["ENFORCE", "LOG_ONLY"],
        default="ENFORCE",
        help="Policy engine mode. LOG_ONLY isolates Q2 from Cedar entirely.",
    )
    ap.add_argument(
        "--rewrite-with",
        choices=["none", "marker", "same", "t_tool"],
        default="t_tool",
        help=(
            "What the interceptor puts in Authorization. t_tool (default) mints a real "
            "resource token via the ID-JAG legs -- the actual design. marker uses a "
            "non-JWT string, which is NOT representative."
        ),
    )
    args = ap.parse_args()
    load_env()
    if args.cleanup:
        cleanup()
        return

    print(f"account {ACCOUNT} region {REGION}\n")
    lrole = ensure_role(
        LAMBDA_ROLE,
        "lambda.amazonaws.com",
        None,
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    echo_arn = ensure_fn(ECHO_FN, HERE / "echo_lambda.py", lrole, {})
    icept_arn = ensure_fn(
        ICEPT_FN,
        HERE / "interceptor_spike4.py",
        lrole,
        {
            "MARKER": MARKER,
            "ID_TOKEN_HEADER": ID_TOKEN_HEADER,
            "REWRITE_MODE": args.rewrite_with,
        },
    )
    endpoint = ensure_api(echo_arn)
    print(f"  echo endpoint: {endpoint}")
    grole = ensure_role(
        GW_ROLE,
        "bedrock-agentcore.amazonaws.com",
        {
            "Version": "2012-10-17",
            "Statement": [
                {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": icept_arn},
                {
                    # CreateGateway resolves AND probes the attached policy engine
                    # using THIS role, from a GenesisPolicyEngineCheck session. All
                    # four are required before the gateway will create; each missing
                    # one fails with its own AccessDenied naming the action:
                    #   GetPolicyEngine            resolve the engine
                    #   AuthorizeAction            evaluate a single action
                    #   PartiallyAuthorizeActions  evaluate a batch
                    #   GetGateway                 same resolution path
                    # Per the AWS service reference those two Authorize* actions are
                    # the only authorization verbs this service defines.
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetPolicyEngine",
                        "bedrock-agentcore:AuthorizeAction",
                        "bedrock-agentcore:PartiallyAuthorizeActions",
                        "bedrock-agentcore:GetGateway",
                    ],
                    "Resource": "*",
                },
            ],
        },
        None,
    )
    # IAM is eventually consistent and CreateGateway probes the role immediately.
    print("  waiting 12s for the role statement to propagate")
    time.sleep(12)
    pe_id = ensure_policy_engine(None)
    gw_id, gw_url = ensure_gateway(grole, icept_arn, endpoint, pe_id, args.policy_mode)
    print(f"  policy engine mode: {args.policy_mode}; interceptor injects: {args.rewrite_with}")
    gw_arn = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:gateway/{gw_id}"
    ensure_policy_engine(gw_arn)
    print(f"  gateway url: {gw_url}")

    print("\n--- need a real T_user: signing in ---")
    from spike2_idjag_subject import sign_in  # reuse the interactive flow

    tokens = cached_tokens()
    if tokens is None:
        tokens = sign_in(
            okta_org_url(),
            must_env("LOGIN_CLIENT_ID"),
            env("FRONTEND_REDIRECT_URI", "http://localhost:8000/auth/callback"),
            f"openid profile email {env('SCOPE_AGENT_ACCESS', 'agent.access')}",
            must_env("AGENTCORE_AS_ISSUER"),
        )
        tokens["_at"] = time.time()
        TOKEN_CACHE.write_text(json.dumps(tokens))
        TOKEN_CACHE.chmod(0o600)
    if args.rewrite_with == "t_tool":
        # Mint a REAL resource token the same way the production interceptor will:
        # ID-JAG leg 1 at the org server, then leg 2 at the resource AS. Injecting
        # this -- rather than a placeholder -- is what makes the Cedar result
        # meaningful, because it is a valid JWT from a DIFFERENT issuer than the one
        # the gateway's authorizer trusts.
        from spike2_idjag_subject import leg1, leg2

        print("\n--- minting a real T_tool for the interceptor to inject ---")
        agent_client = must_env("AI_AGENT_CLIENT_ID")
        as2 = must_env("RESOURCE_AS_ISSUER")
        status, body = leg1(
            okta_org_url(),
            agent_client,
            tokens["id_token"],
            "urn:ietf:params:oauth:token-type:id_token",
            as2,
            env("RESOURCE_SCOPE", "todos.read"),
        )
        if status >= 400 or "access_token" not in body:
            sys.exit(f"  ✗ leg 1 failed: {json.dumps(body)[:300]}")
        status, body = leg2(as2, agent_client, body["access_token"])
        if status >= 400 or "access_token" not in body:
            sys.exit(f"  ✗ leg 2 failed: {json.dumps(body)[:300]}")
        t_tool = body["access_token"]
        print(f"  ✓ T_tool minted (len {len(t_tool)})")
        lam.update_function_configuration(
            FunctionName=ICEPT_FN,
            Environment={
                "Variables": {
                    "MARKER": MARKER,
                    "ID_TOKEN_HEADER": ID_TOKEN_HEADER,
                    "REWRITE_MODE": "t_tool",
                    "T_TOOL": t_tool,
                }
            },
        )
        lam.get_waiter("function_updated_v2").wait(FunctionName=ICEPT_FN)
        print("  ✓ interceptor updated with the real token")

    run_test(gw_url, tokens["access_token"], tokens["id_token"], args)


if __name__ == "__main__":
    main()
