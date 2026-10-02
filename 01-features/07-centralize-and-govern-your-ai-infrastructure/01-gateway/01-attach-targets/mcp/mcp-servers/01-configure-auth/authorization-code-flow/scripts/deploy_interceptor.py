"""Wire a RESPONSE interceptor Lambda onto the Entra consent-portal gateway.

This implements "Custom Just-in-Time Auth" (consent-portal/README.md): a Lambda
RESPONSE interceptor that, in REWRITE mode, replaces the AgentCore-vended identity
URL in a URL-mode elicitation with the consent portal URL. Deploy it in two
phases with --mode:

    --mode log-only  (default) pass-through + full-body logging. Run a tool call
                     as an unconsented user, then read the INTERCEPTOR_EVENT line
                     in the Lambda's CloudWatch logs to see the real elicitation
                     body before rewriting anything.
    --mode rewrite   inject PORTAL_URL when an elicitation is detected.

Re-runnable: the Lambda, its role, the gateway-role invoke grant, and the gateway
interceptor wiring are all idempotent, so flipping --mode is just another run.

Prerequisites (from .env, written by the earlier steps):
    GATEWAY_ID    the READY Entra gateway (deploy_gateway_entra.py --entra)
    PORTAL_URL    the consent portal URL (deploy_portal.py --entra) -- required
                  for --mode rewrite; ignored (may be blank) for --mode log-only

Usage:
    uv run python scripts/deploy_interceptor.py --entra
    uv run python scripts/deploy_interceptor.py --entra --mode rewrite
"""

import io
import os
import sys
import time
import zipfile

import boto3
from gateway_admin import GatewayBoto3Client
from idp_config import (
    gateway_name,
    interceptor_lambda_name,
    interceptor_role_name,
    select_idp,
)
from mcp_config import get_required_env, load_env, save_env

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
HANDLER_PATH = os.path.join(SCRIPTS_DIR, "lambda", "interceptor.py")
HANDLER = "interceptor.lambda_handler"
RUNTIME = "python3.12"
TIMEOUT_SECONDS = 10
IDENTITY_URL_HOST = "bedrock-agentcore"
GATEWAY_TERMINAL = ("READY", "FAILED", "UPDATE_FAILED", "UPDATE_UNSUCCESSFUL")

LAMBDA_TRUST = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}
BASIC_EXECUTION = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"


def parse_mode():
    """Pop --mode out of argv before select_idp parses the required --<idp> flag."""
    mode = "log-only"
    argv = sys.argv[1:]
    kept = []
    i = 0
    while i < len(argv):
        if argv[i] == "--mode":
            if i + 1 >= len(argv):
                print("ERROR: --mode needs a value: log-only | rewrite")
                sys.exit(1)
            mode = argv[i + 1]
            i += 2
            continue
        if argv[i].startswith("--mode="):
            mode = argv[i].split("=", 1)[1]
            i += 1
            continue
        kept.append(argv[i])
        i += 1
    if mode not in ("log-only", "rewrite"):
        print(f"ERROR: unknown --mode {mode!r}; expected log-only | rewrite")
        sys.exit(1)
    sys.argv = [sys.argv[0], *kept]
    return "REWRITE" if mode == "rewrite" else "LOG_ONLY"


def ensure_lambda_role(admin, role_name):
    """Create (or reuse) the Lambda execution role with CloudWatch Logs access."""
    import json

    try:
        admin.iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(LAMBDA_TRUST),
            Description="Execution role for the AgentCore JIT-auth interceptor Lambda",
        )
        print(f"  Created IAM role: {role_name}")
        time.sleep(10)
    except admin.iam.exceptions.EntityAlreadyExistsException:
        print(f"  IAM role already exists: {role_name}")
    admin.iam.attach_role_policy(RoleName=role_name, PolicyArn=BASIC_EXECUTION)
    return admin.iam.get_role(RoleName=role_name)["Role"]["Arn"]


def zip_handler():
    """Zip the single-file handler in memory as interceptor.py at the root."""
    with open(HANDLER_PATH, "rb") as f:
        source = f.read()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("interceptor.py")
        info.external_attr = 0o644 << 16
        zf.writestr(info, source)
    return buffer.getvalue()


def deploy_lambda(lambda_client, fn_name, role_arn, env_vars):
    """Create or update the interceptor Lambda; return its ARN."""
    code = zip_handler()
    environment = {"Variables": env_vars}
    try:
        resp = lambda_client.create_function(
            FunctionName=fn_name,
            Runtime=RUNTIME,
            Role=role_arn,
            Handler=HANDLER,
            Code={"ZipFile": code},
            Timeout=TIMEOUT_SECONDS,
            Environment=environment,
            Description="AgentCore Gateway RESPONSE interceptor (Custom JIT Auth)",
        )
        print(f"  Created Lambda: {fn_name}")
        return resp["FunctionArn"]
    except lambda_client.exceptions.ResourceConflictException:
        lambda_client.update_function_code(FunctionName=fn_name, ZipFile=code)
        _wait_lambda_updated(lambda_client, fn_name)
        lambda_client.update_function_configuration(
            FunctionName=fn_name,
            Runtime=RUNTIME,
            Role=role_arn,
            Handler=HANDLER,
            Timeout=TIMEOUT_SECONDS,
            Environment=environment,
        )
        _wait_lambda_updated(lambda_client, fn_name)
        print(f"  Updated Lambda: {fn_name}")
        return lambda_client.get_function(FunctionName=fn_name)["Configuration"][
            "FunctionArn"
        ]


def _wait_lambda_updated(lambda_client, fn_name):
    """Wait until a code/config update has finished before the next update."""
    for _ in range(30):
        cfg = lambda_client.get_function(FunctionName=fn_name)["Configuration"]
        if cfg.get("LastUpdateStatus") != "InProgress":
            return
        time.sleep(2)


def grant_gateway_invoke(admin, gw_name, lambda_arn):
    """Add a scoped lambda:InvokeFunction grant to the gateway's service role.

    Scoped to the specific interceptor ARN, per the interceptor security best
    practice (no wildcard Lambda permission on the gateway role). Additive: a
    distinct inline policy that leaves the existing AgentCorePolicy untouched.
    """
    import json

    role_name = f"agentcore-{gw_name}-role"
    admin.iam.put_role_policy(
        RoleName=role_name,
        PolicyName="InterceptorInvoke",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "lambda:InvokeFunction",
                        "Resource": lambda_arn,
                    }
                ],
            }
        ),
    )
    print(f"  Granted lambda:InvokeFunction on the gateway role: {role_name}")


def main():
    mode = parse_mode()
    idp = select_idp(__doc__)
    load_env()

    gateway_id = get_required_env("GATEWAY_ID")
    gw_name = gateway_name(idp)
    fn_name = interceptor_lambda_name(idp)
    role_name = interceptor_role_name(idp)

    portal_url = os.environ.get("PORTAL_URL", "")
    if mode == "REWRITE" and not portal_url:
        print("ERROR: --mode rewrite needs PORTAL_URL in .env")
        print("  Run deploy_portal.py --entra first, or export PORTAL_URL.")
        sys.exit(1)
    # deploy_portal.py stores PORTAL_URL scheme-less (it builds the callback URLs
    # from the bare host). The interceptor injects this value straight into the
    # elicitation, and an MCP client resolves a scheme-less string relative to its
    # own origin -- e.g. the Inspector turns it into http://localhost:6274/<host>.
    # Force an absolute https URL so the client navigates to the portal itself.
    if portal_url and not portal_url.startswith(("http://", "https://")):
        portal_url = f"https://{portal_url}"

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client
    lambda_client = boto3.client("lambda", region_name=region)

    print(f"--- Interceptor deploy (mode: {mode}) ---")
    print(f"  gateway:  {gw_name} ({gateway_id})")
    print(f"  lambda:   {fn_name}")

    print("\n--- Step 1: Lambda execution role ---")
    lambda_role_arn = ensure_lambda_role(admin, role_name)
    print(f"  arn: {lambda_role_arn}")

    print("\n--- Step 2: package + deploy the interceptor Lambda ---")
    env_vars = {
        "INTERCEPTOR_MODE": mode,
        "PORTAL_URL": portal_url,
        "IDENTITY_URL_HOST": IDENTITY_URL_HOST,
    }
    lambda_arn = deploy_lambda(lambda_client, fn_name, lambda_role_arn, env_vars)
    print(f"  arn: {lambda_arn}")

    print("\n--- Step 3: grant the gateway role invoke access ---")
    grant_gateway_invoke(admin, gw_name, lambda_arn)

    print("\n--- Step 4: attach the RESPONSE interceptor to the gateway ---")
    gw = control.get_gateway(gatewayIdentifier=gateway_id)
    update_kwargs = {
        "gatewayIdentifier": gateway_id,
        "name": gw["name"],
        "roleArn": gw["roleArn"],
        "protocolType": gw.get("protocolType", "MCP"),
        "authorizerType": gw["authorizerType"],
        "authorizerConfiguration": gw["authorizerConfiguration"],
        "interceptorConfigurations": [
            {
                "interceptor": {"lambda": {"arn": lambda_arn}},
                "interceptionPoints": ["RESPONSE"],
                # The rewrite reads only the response body; keeping headers out
                # avoids logging inbound auth tokens.
                "inputConfiguration": {"passRequestHeaders": False},
            }
        ],
        "exceptionLevel": "DEBUG",
    }
    if gw.get("protocolConfiguration"):
        update_kwargs["protocolConfiguration"] = gw["protocolConfiguration"]
    control.update_gateway(**update_kwargs)

    print("  Waiting for the gateway to settle...")
    last_status = None
    while True:
        time.sleep(10)
        status = control.get_gateway(gatewayIdentifier=gateway_id)["status"]
        if status != last_status:
            print(f"    Status: {status}")
            last_status = status
        if status in GATEWAY_TERMINAL:
            break

    save_env(
        INTERCEPTOR_LAMBDA_ARN=lambda_arn,
        INTERCEPTOR_ROLE_ARN=lambda_role_arn,
        INTERCEPTOR_MODE=mode,
    )
    print(
        "\n  Saved to .env: INTERCEPTOR_LAMBDA_ARN, INTERCEPTOR_ROLE_ARN, INTERCEPTOR_MODE"
    )

    log_group = f"/aws/lambda/{fn_name}"
    print()
    print("=" * 62)
    if mode == "LOG_ONLY":
        print("  Phase 1 (LOG_ONLY) is live. Now:")
        print("   1. Invoke a tool as a user who has NOT consented in the portal")
        print("      (use the AgentCore gateway MCP Inspector).")
        print(f"   2. Read the elicitation body in CloudWatch: {log_group}")
        print("      Look for the 'INTERCEPTOR_EVENT' line.")
        print("   3. Flip to rewrite:")
        print(
            f"      uv run python scripts/deploy_interceptor.py --{idp['name']} --mode rewrite"
        )
    else:
        print("  Phase 2 (REWRITE) is live.")
        print(f"  Injecting portal URL: {portal_url}")
        print("  Invoke a tool as an unconsented user; the client should now be")
        print("  handed the portal URL instead of the raw identity URL.")
        print(f"  Logs: {log_group}")
    print("=" * 62)


if __name__ == "__main__":
    main()
