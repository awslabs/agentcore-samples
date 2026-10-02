"""Create the MCP gateway, its ID-JAG interceptor, the policy engine and the target.

Four resources, all idempotent:

  1. The interceptor Lambda, holding the AI Agent's signing key (from Secrets
     Manager) and performing both ID-JAG legs.
  2. A policy engine, attached to the gateway in ENFORCE mode.
  3. The gateway: protocolType MCP, CUSTOM_JWT inbound against AS 1.
  4. An openApiSchema target for the todo API, with **no outbound credential
     provider** -- the interceptor supplies Authorization instead.

Several of these choices are forced by behaviour that is not obvious from the docs,
and was established against a live gateway:

  * `JWT_PASSTHROUGH` is rejected on every MCP-gateway target type, so the target
    carries no credentialProviderConfigurations at all. Omitting the field is exactly
    what lets the interceptor's header reach the upstream.
  * The gateway role needs GetPolicyEngine, AuthorizeAction AND
    PartiallyAuthorizeActions before CreateGateway will even succeed -- the create
    call probes the policy engine using this role. They are scoped to the policy-engine
    and gateway ARNs rather than "*"; on the very first run the gateway does not exist
    yet, so a gateway pattern scoped to this account and region is used, and a re-run
    tightens it to the exact ARN.
  * `passRequestHeaders: True` is required, or the interceptor never sees the
    headers it needs.
  * exceptionLevel DEBUG makes authorizer and policy denials state a reason.

Writes GATEWAY_ID, GATEWAY_URL, GATEWAY_MCP_URL, GATEWAY_SERVICE_ROLE_ARN,
POLICY_ENGINE_ID and INTERCEPTOR_LAMBDA_ARN to .env.

    python deploy/02_create_gateway.py
    python deploy/02_create_gateway.py --policy-mode LOG_ONLY
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    SAMPLE_ROOT,
    account_id,
    clients,
    discovery_url,
    ensure_lambda,
    ensure_role,
    env,
    gateway_name,
    gateway_role_name,
    interceptor_name,
    interceptor_role_name,
    load_env,
    must_env,
    policy_engine_name,
    region,
    save_env,
    set_log_retention,
    wait_status,
    zip_files,
)

TARGET_NAME = "todo"
AGENT_KEY_SM_ID = "agentcore/xaa-ai-agent-key"


def ensure_key_secret(aws) -> str:
    """Put the AI Agent's private key in Secrets Manager so only the interceptor reads it."""
    from botocore.exceptions import ClientError

    pem_path = SAMPLE_ROOT / "scripts" / "keys" / "okta_private_key.pem"
    if not pem_path.exists():
        print(f"ERROR: {pem_path} not found. Run: python scripts/gen_keypair.py", file=sys.stderr)
        sys.exit(1)
    # The PEM is read inline and never bound to a local that outlives the call, so the
    # key material has the shortest possible scope.
    try:
        arn = aws["sm"].create_secret(Name=AGENT_KEY_SM_ID, SecretString=pem_path.read_text())["ARN"]
        stored = "stored"
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceExistsException":
            raise
        aws["sm"].put_secret_value(SecretId=AGENT_KEY_SM_ID, SecretString=pem_path.read_text())
        arn = aws["sm"].describe_secret(SecretId=AGENT_KEY_SM_ID)["ARN"]
        stored = "refreshed"
    print(f"  ✓ {stored} the AI Agent key in {AGENT_KEY_SM_ID}")
    return arn


def build_interceptor_bundle() -> bytes:
    """Zip the interceptor WITH its dependencies.

    pyjwt and cryptography are not in the Lambda runtime, so shipping handler.py alone
    fails at import with "No module named 'jwt'" -- and because the gateway surfaces
    that as a 500 from the tool call, it reads like a gateway problem rather than a
    packaging one. cryptography has compiled extensions, hence the explicit
    manylinux platform.
    """
    import shutil
    import subprocess
    import tempfile
    import zipfile

    stage = Path(tempfile.mkdtemp(prefix="xaa-icept-"))
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--platform",
                "manylinux2014_x86_64",
                "--only-binary=:all:",
                "--python-version",
                "3.12",
                "--target",
                str(stage),
                "-r",
                str(SAMPLE_ROOT / "interceptors" / "requirements.txt"),
            ],
            check=True,
        )
        shutil.copy(SAMPLE_ROOT / "interceptors" / "request_interceptor.py", stage / "handler.py")
        out = Path(tempfile.mkdtemp(prefix="xaa-icept-zip-")) / "bundle.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(stage.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    z.write(path, path.relative_to(stage))
        data = out.read_bytes()
        print(f"  interceptor bundle: {len(data) / 1_000_000:.1f} MB")
        return data
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def ensure_interceptor(aws, secret_arn: str) -> str:
    role = ensure_role(
        aws["iam"],
        interceptor_role_name(),
        "lambda.amazonaws.com",
        {
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Action": "secretsmanager:GetSecretValue", "Resource": secret_arn}],
        },
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    code = build_interceptor_bundle()
    name = interceptor_name()
    arn = ensure_lambda(
        aws["lam"],
        name,
        code,
        role,
        "handler.handler",
        {
            "OKTA_ORG_URL": must_env("OKTA_ORG_URL"),
            "RESOURCE_AS_ISSUER": must_env("RESOURCE_AS_ISSUER"),
            "RESOURCE_SCOPE": env("RESOURCE_SCOPE", "todos.read"),
            "AI_AGENT_CLIENT_ID": must_env("AI_AGENT_CLIENT_ID"),
            "AI_AGENT_KEY_KID": must_env("AI_AGENT_KEY_KID"),
            "AI_AGENT_KEY_SECRET_ID": AGENT_KEY_SM_ID,
            "ID_TOKEN_HEADER": env("ID_TOKEN_HEADER", "X-Okta-Id-Token"),
            "XAA_LEG1_SUBJECT": env("XAA_LEG1_SUBJECT", "access_token"),
            "LOG_CLAIMS": "true",
        },
    )
    set_log_retention(aws["logs"], name)
    return arn


def ensure_policy_engine(aws) -> str:
    existing = next(
        (p for p in aws["acc"].list_policy_engines().get("policyEngines", []) if p["name"] == policy_engine_name()),
        None,
    )
    if existing:
        pe_id = existing["policyEngineId"]
        print(f"  • reusing policy engine {pe_id}")
    else:
        pe_id = aws["acc"].create_policy_engine(name=policy_engine_name(), description="XAA delegation chain sample")[
            "policyEngineId"
        ]
        print(f"  ✓ created policy engine {pe_id}")
    wait_status(lambda: aws["acc"].get_policy_engine(policyEngineId=pe_id))
    return pe_id


def ensure_gateway(aws, icept_arn: str, pe_id: str, mode: str, allow_user_scope: bool) -> tuple[str, str]:
    # CreateGateway probes the policy engine using this role, so all three
    # policy-engine actions must exist up front. Each missing one fails the create
    # with its own AccessDenied.
    #
    # Scoped, not "*". Per the AWS service reference, AuthorizeAction and
    # PartiallyAuthorizeActions accept BOTH gateway and policy-engine resources, and
    # GetPolicyEngine/GetGateway accept their own. The policy-engine ARN is known here
    # because [3/5] runs first. The gateway ARN is not -- CreateGateway needs this role,
    # so the gateway does not exist yet -- hence a gateway pattern narrowed to this
    # account and region on the first run. Re-running this script after the gateway
    # exists replaces the pattern with the exact ARN, which is why it is worth re-running
    # once at the end of a deploy.
    reg, acct = region(), account_id()
    pe_arn = f"arn:aws:bedrock-agentcore:{reg}:{acct}:policy-engine/{pe_id}"
    known_gw = env("GATEWAY_ID")
    gw_res = (
        f"arn:aws:bedrock-agentcore:{reg}:{acct}:gateway/{known_gw}"
        if known_gw
        else f"arn:aws:bedrock-agentcore:{reg}:{acct}:gateway/*"
    )
    role = ensure_role(
        aws["iam"],
        gateway_role_name(),
        "bedrock-agentcore.amazonaws.com",
        {
            "Version": "2012-10-17",
            "Statement": [
                {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": icept_arn},
                {
                    "Sid": "PolicyEngineEvaluation",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetPolicyEngine",
                        "bedrock-agentcore:AuthorizeAction",
                        "bedrock-agentcore:PartiallyAuthorizeActions",
                    ],
                    "Resource": [pe_arn, gw_res],
                },
                {
                    "Sid": "ReadOwnGateway",
                    "Effect": "Allow",
                    "Action": "bedrock-agentcore:GetGateway",
                    "Resource": gw_res,
                },
            ],
        },
        None,
    )
    print("  waiting 12s for the role statement to propagate")
    time.sleep(12)

    audience = env("AGENTCORE_AUDIENCE", "https://xaa-agentcore.example.com")
    tools_scope = env("SCOPE_TOOLS_ACCESS", "tools.access")
    allowed_scopes = [tools_scope]
    if allow_user_scope:
        # Debugging only: lets you drive the gateway with a
        # T_user straight from sign-in, before the Runtime and OBO hop exist.
        allowed_scopes.append(env("SCOPE_AGENT_ACCESS", "agent.access"))
    print(f"  allowedScopes: {allowed_scopes}")
    config = {
        "name": gateway_name(),
        "roleArn": role,
        "protocolType": "MCP",
        "authorizerType": "CUSTOM_JWT",
        "authorizerConfiguration": {
            "customJWTAuthorizer": {
                "discoveryUrl": discovery_url(must_env("AGENTCORE_AS_ISSUER")),
                "allowedAudience": [audience],
                # Pin on SCOPE, not client.
                #
                # `allowedClients` does NOT work with Okta: verified live, a token whose
                # `cid` is exactly the listed client is still refused, and the gateway
                # reports it as `insufficient_scope` -- which sends you hunting through
                # scopes. Okta puts the client in `cid`; the gateway evidently compares a
                # claim Okta does not emit in access tokens (`client_id`/`azp`).
                #
                # Scope pinning gives the same protection here: only the OBO exchange
                # mints `tools.access`, so a replayed `T_user` (which carries
                # `agent.access`) is refused at this hop.
                "allowedScopes": allowed_scopes,
            }
        },
        "interceptorConfigurations": [
            {
                "interceptor": {"lambda": {"arn": icept_arn}},
                "interceptionPoints": ["REQUEST"],
                # Without this the interceptor sees no headers at all -- including
                # Authorization, which is what it exchanges at leg 1.
                "inputConfiguration": {"passRequestHeaders": True},
            }
        ],
        "policyEngineConfiguration": {
            "arn": f"arn:aws:bedrock-agentcore:{region()}:{account_id()}:policy-engine/{pe_id}",
            "mode": mode,
        },
        "exceptionLevel": env("GATEWAY_EXCEPTION_LEVEL", "DEBUG"),
    }

    existing = next((g for g in aws["acc"].list_gateways()["items"] if g["name"] == gateway_name()), None)
    if existing:
        gw_id = existing["gatewayId"]
        aws["acc"].update_gateway(gatewayIdentifier=gw_id, **config)
        print(f"  • updated gateway {gw_id}")
    else:
        gw_id = aws["acc"].create_gateway(description="Okta XAA delegation chain", **config)["gatewayId"]
        print(f"  ✓ created gateway {gw_id}")
    detail = wait_status(lambda: aws["acc"].get_gateway(gatewayIdentifier=gw_id))
    if detail["status"] != "READY":
        sys.exit(f"  gateway not READY: {detail['status']} {detail.get('statusReasons')}")
    save_env(GATEWAY_SERVICE_ROLE_ARN=role)
    return gw_id, detail["gatewayUrl"]


def ensure_target(aws, gw_id: str) -> str:
    schema = (SAMPLE_ROOT / "gateway" / "todo-tools.json").read_text()
    api_url = must_env("RESOURCE_API_URL", "Run deploy/01_deploy_resource.py first.")
    # Point the OpenAPI servers[] at the deployed API so the gateway knows where to go.
    import json as _json

    doc = _json.loads(schema)
    doc["servers"] = [{"url": api_url.rstrip("/")}]
    config = {"mcp": {"openApiSchema": {"inlinePayload": _json.dumps(doc)}}}

    existing = next(
        (t for t in aws["acc"].list_gateway_targets(gatewayIdentifier=gw_id)["items"] if t["name"] == TARGET_NAME),
        None,
    )
    if existing:
        tid = existing["targetId"]
        # No credentialProviderConfigurations: JWT_PASSTHROUGH is rejected on MCP
        # targets, and omitting the field is what lets the interceptor's Authorization
        # header through to the API.
        aws["acc"].update_gateway_target(
            gatewayIdentifier=gw_id, targetId=tid, name=TARGET_NAME, targetConfiguration=config
        )
        print(f"  • updated target {tid}")
    else:
        tid = aws["acc"].create_gateway_target(gatewayIdentifier=gw_id, name=TARGET_NAME, targetConfiguration=config)[
            "targetId"
        ]
        print(f"  ✓ created target {tid} (no outbound credential provider)")
    detail = wait_status(lambda: aws["acc"].get_gateway_target(gatewayIdentifier=gw_id, targetId=tid))
    print(f"    status: {detail['status']} {detail.get('statusReasons') or ''}")
    return tid


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--policy-mode",
        choices=["ENFORCE", "LOG_ONLY"],
        default="ENFORCE",
        help="Cedar enforcement. LOG_ONLY evaluates and logs without blocking.",
    )
    ap.add_argument(
        "--allow-user-scope",
        action="store_true",
        help=(
            "Also accept agent.access at the gateway. Only for debugging a T_user "
            "directly against the gateway -- the agent always presents a tools.access "
            "token. Leave it off so a replayed T_user is refused."
        ),
    )
    args = ap.parse_args()
    load_env()
    aws = clients()
    print(f"account {account_id()} region {region()}\n")

    print("[1/5] AI Agent key in Secrets Manager")
    secret_arn = ensure_key_secret(aws)

    print("\n[2/5] ID-JAG interceptor Lambda")
    icept_arn = ensure_interceptor(aws, secret_arn)

    print("\n[3/5] Policy engine")
    pe_id = ensure_policy_engine(aws)

    print(f"\n[4/5] Gateway (policy mode {args.policy_mode})")
    gw_id, gw_url = ensure_gateway(aws, icept_arn, pe_id, args.policy_mode, args.allow_user_scope)

    print("\n[5/5] Todo target")
    ensure_target(aws, gw_id)

    save_env(
        GATEWAY_ID=gw_id,
        GATEWAY_URL=gw_url,
        GATEWAY_MCP_URL=gw_url if gw_url.endswith("/mcp") else f"{gw_url.rstrip('/')}/mcp",
        POLICY_ENGINE_ID=pe_id,
        INTERCEPTOR_LAMBDA_ARN=icept_arn,
    )
    print(f"\n  ✓ GATEWAY_MCP_URL={gw_url}")
    print("\n  Next: python deploy/03_create_policies.py")


if __name__ == "__main__":
    main()
