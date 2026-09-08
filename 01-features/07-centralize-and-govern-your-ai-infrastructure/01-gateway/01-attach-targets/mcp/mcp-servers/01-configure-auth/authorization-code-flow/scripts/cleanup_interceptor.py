"""Detach and delete the Custom JIT Auth interceptor from the Entra gateway.

Reverses deploy_interceptor.py, in the reverse order it created things, and every
step is skipped-not-fatal so a partial deploy still cleans up:

    1. update_gateway with interceptorConfigurations=[]  (detach first, so the
       gateway stops invoking a Lambda we are about to delete)
    2. delete the interceptor Lambda
    3. delete the Lambda execution role (detach its managed policy first)
    4. delete the "InterceptorInvoke" inline policy from the gateway service role

Does NOT touch the gateway itself, the consent portal, any target, or the Entra
resource app -- only what the interceptor deploy added.

Usage:
    uv run python scripts/cleanup_interceptor.py --entra
"""

import os

import boto3
from gateway_admin import GatewayBoto3Client
from idp_config import (
    gateway_name,
    interceptor_lambda_name,
    interceptor_role_name,
    select_idp,
)
from mcp_config import load_env

BASIC_EXECUTION = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"


def detach_interceptor(control, gateway_id):
    """Re-put the gateway with an empty interceptor list, re-supplying required fields."""
    gw = control.get_gateway(gatewayIdentifier=gateway_id)
    if not gw.get("interceptorConfigurations"):
        print("  No interceptor attached; nothing to detach.")
        return
    kwargs = {
        "gatewayIdentifier": gateway_id,
        "name": gw["name"],
        "roleArn": gw["roleArn"],
        "protocolType": gw.get("protocolType", "MCP"),
        "authorizerType": gw["authorizerType"],
        "authorizerConfiguration": gw["authorizerConfiguration"],
        "interceptorConfigurations": [],
        "exceptionLevel": "DEBUG",
    }
    if gw.get("protocolConfiguration"):
        kwargs["protocolConfiguration"] = gw["protocolConfiguration"]
    control.update_gateway(**kwargs)
    print("  Detached the RESPONSE interceptor from the gateway.")


def main():
    idp = select_idp(__doc__)
    load_env()

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client
    lambda_client = boto3.client("lambda", region_name=region)

    fn_name = interceptor_lambda_name(idp)
    role_name = interceptor_role_name(idp)
    gw_name = gateway_name(idp)
    gateway_role = f"agentcore-{gw_name}-role"

    print("--- Step 1: detach the interceptor from the gateway ---")
    gateway_id = os.environ.get("GATEWAY_ID", "")
    if not gateway_id:
        print("  GATEWAY_ID not set; skipping detach (gateway may already be gone).")
    else:
        try:
            detach_interceptor(control, gateway_id)
        except Exception as e:
            print(f"  Skipped: {e}")

    print("\n--- Step 2: delete the interceptor Lambda ---")
    try:
        lambda_client.delete_function(FunctionName=fn_name)
        print(f"  Deleted Lambda: {fn_name}")
    except lambda_client.exceptions.ResourceNotFoundException:
        print(f"  Already gone: {fn_name}")
    except Exception as e:
        print(f"  Skipped: {e}")

    print("\n--- Step 3: delete the Lambda execution role ---")
    try:
        admin.iam.detach_role_policy(RoleName=role_name, PolicyArn=BASIC_EXECUTION)
    except Exception:
        pass
    try:
        admin.iam.delete_role(RoleName=role_name)
        print(f"  Deleted role: {role_name}")
    except admin.iam.exceptions.NoSuchEntityException:
        print(f"  Already gone: {role_name}")
    except Exception as e:
        print(f"  Skipped: {e}")

    print("\n--- Step 4: remove the invoke grant from the gateway role ---")
    try:
        admin.iam.delete_role_policy(
            RoleName=gateway_role, PolicyName="InterceptorInvoke"
        )
        print(f"  Removed InterceptorInvoke from: {gateway_role}")
    except admin.iam.exceptions.NoSuchEntityException:
        print("  Already gone (role or policy).")
    except Exception as e:
        print(f"  Skipped: {e}")

    print("\n--- Left in place (on purpose) ---")
    print("  the gateway, the consent portal, all targets, and the Entra resource app")


if __name__ == "__main__":
    main()
