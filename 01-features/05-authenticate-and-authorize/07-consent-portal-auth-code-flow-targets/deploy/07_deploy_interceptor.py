"""OPTIONAL: wire a RESPONSE interceptor onto the gateway (gateway-wide JIT auth).

Without this, the consent substitution happens in the agent: `agent/agent.py`
detects the `-32042` elicitation and replies with the portal URL. That protects
this sample's agent and nothing else — any other MCP client on the same gateway
(the MCP Inspector, a coding agent, a colleague's script) still receives the raw
AgentCore Identity authorize URL, and following it bypasses the portal's session
binding.

This script moves the substitution into the gateway, so **every** client is
steered to the portal regardless of its code. A gateway supports at most one
RESPONSE interceptor.

Creates, idempotently:
  1. A Lambda execution role with CloudWatch Logs access.
  2. The interceptor Lambda from deploy/lambda/interceptor.py.
  3. A scoped `lambda:InvokeFunction` grant on the gateway's service role — a
     separate inline policy, so the gateway's own policy is untouched.
  4. The gateway's interceptorConfigurations, via UpdateGateway.

Modes:
    --mode rewrite    (default) inject PORTAL_URL on a -32042 elicitation.
    --mode log-only   pass through, but log the whole interceptor event. Use this
                      if the rewrite stops matching and you need to see the real
                      payload shape in CloudWatch.

Removal:
    --remove          detach the interceptor and delete the Lambda and its role.
                      Leaves the gateway, portal and targets alone.

Run from the sample root:
    python deploy/07_deploy_interceptor.py --entra
    python deploy/07_deploy_interceptor.py --entra --mode log-only
    python deploy/07_deploy_interceptor.py --entra --remove
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    DEPLOY_DIR,
    check_boto_version,
    control_client,
    gateway_name,
    gateway_role_name,
    interceptor_lambda_name,
    interceptor_role_name,
    load_env,
    must_env,
    save_env,
    select_idp_with_args,
)

HANDLER_PATH = DEPLOY_DIR / "lambda" / "interceptor.py"
HANDLER = "interceptor.lambda_handler"
INVOKE_POLICY_NAME = "InterceptorInvoke"
GATEWAY_TERMINAL = ("READY", "FAILED", "UPDATE_FAILED", "UPDATE_UNSUCCESSFUL")

# --- Adopter-facing configuration -------------------------------------------
# All optional, all read from .env (or the environment), all with defaults that
# work as-is. See README "Configuration you can change".
#
# INTERCEPTOR_TARGET_URL      where users are sent instead of the identity URL.
#                             Defaults to PORTAL_URL. Point it at your own
#                             landing page if you would rather explain what is
#                             about to happen before handing off to the portal —
#                             the portal still does the consent, this only
#                             changes the first hop the user sees.
# INTERCEPTOR_LAMBDA_NAME     override the derived function name
# INTERCEPTOR_ROLE_NAME       override the derived execution role name
# INTERCEPTOR_RUNTIME         Lambda runtime (default python3.12)
# INTERCEPTOR_TIMEOUT_SECONDS Lambda timeout (default 10). The gateway waits on
#                             this on every response, so keep it small.
# INTERCEPTOR_MEMORY_MB       Lambda memory (default 128; observed 37 MB used)
# INTERCEPTOR_LOG_RETENTION_DAYS  retention on the Lambda's log group. Unset
#                             means "never expire", which is a slow cost leak.
# INTERCEPTOR_IDENTITY_URL_HOST   host substring that marks a URL as rewritable
#                             (default "bedrock-agentcore"). The safety guard —
#                             widen it only if you know why.
# INTERCEPTOR_PASS_REQUEST_HEADERS  "true" to let the interceptor see request
#                             headers. Default false, which keeps the caller's
#                             JWT out of the event and out of CloudWatch.


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"ERROR: {name} must be an integer, got {raw!r}", file=sys.stderr)
        sys.exit(1)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


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


def ensure_lambda_role(iam, role_name: str) -> str:
    try:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(LAMBDA_TRUST),
            Description="Execution role for the AgentCore consent JIT interceptor",
        )
        print(f"  ✓ Created IAM role: {role_name}")
        print("  ⏳ Waiting 10s for IAM propagation…")
        time.sleep(10)
    except iam.exceptions.EntityAlreadyExistsException:
        print(f"  • IAM role already exists: {role_name}")
    iam.attach_role_policy(RoleName=role_name, PolicyArn=BASIC_EXECUTION)
    return iam.get_role(RoleName=role_name)["Role"]["Arn"]


def zip_handler() -> bytes:
    """Zip the single-file handler in memory, as interceptor.py at the root."""
    source = HANDLER_PATH.read_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("interceptor.py")
        info.external_attr = 0o644 << 16
        zf.writestr(info, source)
    return buffer.getvalue()


def _wait_lambda_updated(lambda_client, fn_name: str) -> None:
    """A second update is rejected while the first is still InProgress."""
    for _ in range(30):
        cfg = lambda_client.get_function(FunctionName=fn_name)["Configuration"]
        if cfg.get("LastUpdateStatus") != "InProgress":
            return
        time.sleep(2)


def deploy_lambda(lambda_client, fn_name: str, role_arn: str, env_vars: dict) -> str:
    code = zip_handler()
    environment = {"Variables": env_vars}
    runtime = os.environ.get("INTERCEPTOR_RUNTIME") or "python3.12"
    timeout = _env_int("INTERCEPTOR_TIMEOUT_SECONDS", 10)
    memory = _env_int("INTERCEPTOR_MEMORY_MB", 128)
    print(f"  runtime={runtime} timeout={timeout}s memory={memory}MB")
    try:
        resp = lambda_client.create_function(
            FunctionName=fn_name,
            Runtime=runtime,
            Role=role_arn,
            Handler=HANDLER,
            Code={"ZipFile": code},
            Timeout=timeout,
            MemorySize=memory,
            Environment=environment,
            Description="AgentCore Gateway RESPONSE interceptor — consent portal JIT auth",
        )
        print(f"  ✓ Created Lambda: {fn_name}")
        return resp["FunctionArn"]
    except lambda_client.exceptions.ResourceConflictException:
        lambda_client.update_function_code(FunctionName=fn_name, ZipFile=code)
        _wait_lambda_updated(lambda_client, fn_name)
        lambda_client.update_function_configuration(
            FunctionName=fn_name,
            Runtime=runtime,
            Role=role_arn,
            Handler=HANDLER,
            Timeout=timeout,
            MemorySize=memory,
            Environment=environment,
        )
        _wait_lambda_updated(lambda_client, fn_name)
        print(f"  ✓ Updated Lambda: {fn_name}")
        return lambda_client.get_function(FunctionName=fn_name)["Configuration"]["FunctionArn"]


def set_log_retention(logs_client, fn_name: str) -> None:
    """Apply INTERCEPTOR_LOG_RETENTION_DAYS, if set.

    Lambda creates the log group on first invocation with no expiry, so without
    this the interceptor's logs accumulate forever.
    """
    days = os.environ.get("INTERCEPTOR_LOG_RETENTION_DAYS", "").strip()
    if not days:
        return
    group = f"/aws/lambda/{fn_name}"
    try:
        logs_client.create_log_group(logGroupName=group)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            print(f"  ⚠ could not create {group}: {e.response['Error']['Code']}", file=sys.stderr)
            return
    try:
        logs_client.put_retention_policy(
            logGroupName=group, retentionInDays=_env_int("INTERCEPTOR_LOG_RETENTION_DAYS", 14)
        )
        print(f"  ✓ Log retention set to {days} day(s) on {group}")
    except ClientError as e:
        print(f"  ⚠ could not set retention: {e.response['Error']['Code']}", file=sys.stderr)


def grant_gateway_invoke(iam, role_name: str, lambda_arn: str) -> None:
    """Scoped invoke grant, as its own inline policy.

    Scoped to this one function ARN rather than a wildcard, and kept separate
    from the gateway's own policy so neither overwrites the other.
    """
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=INVOKE_POLICY_NAME,
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
    print(f"  ✓ Granted lambda:InvokeFunction on {role_name} (scoped to this function)")


def update_gateway_interceptors(control, gateway_id: str, configs: list) -> None:
    """UpdateGateway preserving everything else about the gateway.

    UpdateGateway is a full replace, so every field we care about has to be read
    back and passed in — dropping protocolConfiguration here would silently
    reset supportedVersions and break URL-mode elicitation altogether.
    """
    gw = control.get_gateway(gatewayIdentifier=gateway_id)
    kwargs = {
        "gatewayIdentifier": gateway_id,
        "name": gw["name"],
        "roleArn": gw["roleArn"],
        "protocolType": gw.get("protocolType", "MCP"),
        "authorizerType": gw["authorizerType"],
        "authorizerConfiguration": gw["authorizerConfiguration"],
        "interceptorConfigurations": configs,
    }
    if gw.get("protocolConfiguration"):
        kwargs["protocolConfiguration"] = gw["protocolConfiguration"]
    if gw.get("exceptionLevel"):
        kwargs["exceptionLevel"] = gw["exceptionLevel"]
    if gw.get("description"):
        kwargs["description"] = gw["description"]
    control.update_gateway(**kwargs)

    print("  ⏳ Waiting for the gateway to settle…")
    last = None
    for _ in range(30):
        time.sleep(5)
        status = control.get_gateway(gatewayIdentifier=gateway_id)["status"]
        if status != last:
            print(f"    status: {status}")
            last = status
        if status in GATEWAY_TERMINAL:
            return
    print("  ⚠ Gateway did not settle within 150s; check its status.", file=sys.stderr)


def remove(control, iam, lambda_client, idp: dict, gateway_id: str) -> None:
    print("--- Detaching the interceptor from the gateway ---")
    try:
        update_gateway_interceptors(control, gateway_id, [])
        print("  ✓ interceptorConfigurations cleared")
    except control.exceptions.ResourceNotFoundException:
        # Running this after teardown.py is a normal order of events. With the
        # gateway already gone there is nothing to detach from, and stopping here
        # would orphan the Lambda and its role — so carry on and delete them.
        print("  • Gateway already gone; nothing to detach")

    fn_name = interceptor_lambda_name(idp)
    role_name = interceptor_role_name(idp)

    print("\n--- Removing the invoke grant from the gateway role ---")
    try:
        iam.delete_role_policy(RoleName=gateway_role_name(idp), PolicyName=INVOKE_POLICY_NAME)
        print(f"  ✓ Deleted inline policy {INVOKE_POLICY_NAME}")
    except iam.exceptions.NoSuchEntityException:
        print(f"  • {INVOKE_POLICY_NAME} not present, skipping")

    print("\n--- Deleting the Lambda and its role ---")
    try:
        lambda_client.delete_function(FunctionName=fn_name)
        print(f"  ✓ Deleted Lambda: {fn_name}")
    except lambda_client.exceptions.ResourceNotFoundException:
        print(f"  • Lambda already gone: {fn_name}")
    try:
        iam.detach_role_policy(RoleName=role_name, PolicyArn=BASIC_EXECUTION)
    except ClientError:
        pass
    try:
        iam.delete_role(RoleName=role_name)
        print(f"  ✓ Deleted IAM role: {role_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"  • IAM role already gone: {role_name}")

    save_env(INTERCEPTOR_LAMBDA_ARN="", INTERCEPTOR_ROLE_ARN="", INTERCEPTOR_MODE="")
    print("\n✓ Interceptor removed. The agent's own -32042 handling still applies.")


def main() -> None:
    idp, args = select_idp_with_args(
        __doc__,
        [
            (
                ["--mode"],
                {
                    "choices": ["rewrite", "log-only"],
                    "default": "rewrite",
                    "help": "rewrite (default) injects the portal URL; log-only just logs.",
                },
            ),
            (["--remove"], {"action": "store_true", "help": "Detach and delete the interceptor."}),
        ],
    )
    check_boto_version()
    load_env()

    gateway_id = must_env("GATEWAY_ID")
    control, region = control_client()
    iam = boto3.client("iam", region_name=region)
    lambda_client = boto3.client("lambda", region_name=region)

    if args.remove:
        remove(control, iam, lambda_client, idp, gateway_id)
        return

    mode = "REWRITE" if args.mode == "rewrite" else "LOG_ONLY"
    # Where the user is sent. Defaults to the portal; override to interpose your
    # own landing page (the portal still gathers the consent).
    target_url = os.environ.get("INTERCEPTOR_TARGET_URL", "").strip() or os.environ.get("PORTAL_URL", "").strip()
    if mode == "REWRITE" and not target_url:
        print("ERROR: --mode rewrite needs PORTAL_URL in .env.", file=sys.stderr)
        print("  Run deploy/02_create_portal.py first, or set INTERCEPTOR_TARGET_URL.", file=sys.stderr)
        sys.exit(1)
    # PORTAL_URL is stored as a bare host. The interceptor injects this value
    # straight into the elicitation, and an MCP client resolves a scheme-less
    # string against its own origin — the Inspector would turn it into
    # http://localhost:6274/<host>. Force an absolute URL.
    if target_url and not target_url.startswith(("http://", "https://")):
        target_url = f"https://{target_url}"
    if os.environ.get("INTERCEPTOR_TARGET_URL", "").strip():
        print(f"  (using INTERCEPTOR_TARGET_URL override: {target_url})")

    fn_name = interceptor_lambda_name(idp)
    print(f"--- Interceptor deploy (mode: {mode}) ---")
    print(f"  gateway: {gateway_name(idp)} ({gateway_id})")
    print(f"  lambda:  {fn_name}")
    if mode == "REWRITE":
        print(f"  target:  {target_url}")

    print("\n--- Step 1: Lambda execution role ---")
    lambda_role_arn = ensure_lambda_role(iam, interceptor_role_name(idp))
    print(f"  arn: {lambda_role_arn}")

    print("\n--- Step 2: package and deploy the Lambda ---")
    lambda_arn = deploy_lambda(
        lambda_client,
        fn_name,
        lambda_role_arn,
        {
            "INTERCEPTOR_MODE": mode,
            "PORTAL_URL": target_url,
            "IDENTITY_URL_HOST": os.environ.get("INTERCEPTOR_IDENTITY_URL_HOST") or "bedrock-agentcore",
        },
    )
    print(f"  arn: {lambda_arn}")
    set_log_retention(boto3.client("logs", region_name=region), fn_name)

    print("\n--- Step 3: grant the gateway role invoke access ---")
    grant_gateway_invoke(iam, gateway_role_name(idp), lambda_arn)

    print("\n--- Step 4: attach it as the RESPONSE interceptor ---")
    update_gateway_interceptors(
        control,
        gateway_id,
        [
            {
                "interceptor": {"lambda": {"arn": lambda_arn}},
                "interceptionPoints": ["RESPONSE"],
                # The rewrite needs only the response body. Turning headers off
                # keeps the caller's JWT out of the interceptor event, and out of
                # CloudWatch in log-only mode.
                "inputConfiguration": {"passRequestHeaders": _env_bool("INTERCEPTOR_PASS_REQUEST_HEADERS", False)},
            }
        ],
    )

    save_env(
        INTERCEPTOR_LAMBDA_ARN=lambda_arn,
        INTERCEPTOR_ROLE_ARN=lambda_role_arn,
        INTERCEPTOR_MODE=mode,
    )
    print("  Saved to .env: INTERCEPTOR_LAMBDA_ARN, INTERCEPTOR_ROLE_ARN, INTERCEPTOR_MODE")

    log_group = f"/aws/lambda/{fn_name}"
    print()
    print("=" * 68)
    if mode == "LOG_ONLY":
        print("  LOG_ONLY is live — nothing is being rewritten.")
        print("  1. Invoke a tool as a user who has NOT consented.")
        print(f"  2. Read the INTERCEPTOR_EVENT line in {log_group}")
        print(f"  3. Flip to rewriting: python deploy/07_deploy_interceptor.py --{idp['name']}")
    else:
        print("  REWRITE is live. Any MCP client on this gateway now receives the")
        print("  portal URL instead of the raw identity URL.")
        print()
        print("  Try it: sign in as an IdP user who has NOT consented yet (the")
        print("  portal has no Disconnect action, and consent is per user), then")
        print("  invoke a tool from a plain MCP client — the Inspector, or Claude")
        print("  Code. The elicitation should carry the portal URL, not the raw")
        print("  identity URL.")
        print(f"  Logs: {log_group}  (look for CONSENT_ELICITATION)")
    print()
    print(f"  Remove: python deploy/07_deploy_interceptor.py --{idp['name']} --remove")
    print("=" * 68)


if __name__ == "__main__":
    main()
