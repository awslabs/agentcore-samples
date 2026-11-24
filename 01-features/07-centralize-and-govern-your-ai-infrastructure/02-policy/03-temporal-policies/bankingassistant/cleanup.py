# /// script
# requires-python = ">=3.10"
# dependencies = ["boto3==1.43.36", "botocore==1.43.36", "jmespath==1.0.1", "s3transfer==0.19.0", "python-dateutil==2.9.0.post0", "six==1.17.0", "urllib3==2.5.0"]
# ///
"""Delete only the dedicated resources recorded by setup.py.

Keep setup_config.json until every deletion is verified. Access errors and
poll timeouts are failures, not evidence of deletion. This does not delete the
CLI-managed Runtime or OAuth provider (see README Cleanup).
"""

import argparse
import json
import os
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from policy_contract import API_VERSION, verified_policy_session
from resource_state import OWNER_TAG, absent, require_equal, verify_resources

CONFIG_FILE = Path(__file__).parent / "setup_config.json"


def delete_if_present(delete, **params) -> None:
    try:
        delete(**params)
    except ClientError as error:
        if not absent(error):
            raise


def wait_deleted(get_resource, label: str, attempts: int = 60) -> None:
    for _ in range(attempts):
        try:
            get_resource()
        except ClientError as error:
            if absent(error):
                return
            raise
        time.sleep(5)
    raise TimeoutError(f"{label} deletion not verified; keep setup_config.json and retry")


def list_all(list_resources, key: str, **params) -> list:
    items = []
    while True:
        try:
            result = list_resources(**params)
        except ClientError as error:
            if absent(error):
                return items
            raise
        items.extend(result.get(key, []))
        token = result.get("nextToken")
        if not token:
            return items
        params["nextToken"] = token


def cleanup(ctrl, iam, cfg: dict) -> None:
    verify_resources(ctrl, iam, cfg)
    engine_id = cfg.get("engine_id")
    gateway_id = cfg.get("gateway_id")
    # Inventory all children before the first deletion, not one page at a time.
    policies = list_all(ctrl.list_policies, "policies", policyEngineId=engine_id, maxResults=50) if engine_id else []
    targets = (
        list_all(ctrl.list_gateway_targets, "items", gatewayIdentifier=gateway_id, maxResults=50) if gateway_id else []
    )
    # Engine and gateway must be dedicated to this lab: every policy/target is
    # removed, including tutorial policies added after setup.py completed.
    if engine_id:
        for policy in policies:
            params = {"policyEngineId": engine_id, "policyId": policy["policyId"]}
            delete_if_present(ctrl.delete_policy, **params)
            wait_deleted(lambda params=params: ctrl.get_policy(**params), "Policy")

    if gateway_id:
        for target in targets:
            params = {"gatewayIdentifier": gateway_id, "targetId": target["targetId"]}
            delete_if_present(ctrl.delete_gateway_target, **params)
            wait_deleted(lambda params=params: ctrl.get_gateway_target(**params), "Gateway target")
        # Remove the gateway attachment before deleting its engine.
        delete_if_present(ctrl.delete_gateway, gatewayIdentifier=gateway_id)
        wait_deleted(lambda: ctrl.get_gateway(gatewayIdentifier=gateway_id), "Gateway")

    if engine_id:
        delete_if_present(ctrl.delete_policy_engine, policyEngineId=engine_id)
        wait_deleted(lambda: ctrl.get_policy_engine(policyEngineId=engine_id), "Policy engine")

    if cfg.get("role_arn"):
        role_name = cfg["role_name"]
        delete_if_present(iam.delete_role_policy, RoleName=role_name, PolicyName="GatewayExecutionPolicy")
        delete_if_present(iam.delete_role, RoleName=role_name)
        wait_deleted(lambda: iam.get_role(RoleName=role_name), "Gateway role")


def main() -> None:
    sdk = verified_policy_session()  # No clients/deletion with a divergent model selection.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cognito", action="store_true", help="Also delete COGNITO_STACK_NAME (must be dedicated to this lab)"
    )
    args = parser.parse_args()
    if not CONFIG_FILE.exists():
        print("No setup_config.json; nothing recorded to clean up.")
        return
    cfg = json.loads(CONFIG_FILE.read_text())
    region = os.environ.get("REGION", cfg.get("region", "us-east-1"))
    if not cfg.get("account_id") or cfg.get("region") != region or not cfg.get("role_name"):
        raise RuntimeError("Verify legacy state ownership/account/region before cleanup; see README")
    stack_name = os.environ.get("COGNITO_STACK_NAME")
    if args.cognito and not stack_name:
        raise RuntimeError("--cognito requires an explicit COGNITO_STACK_NAME")
    session = boto3.Session(botocore_session=sdk, region_name=region)
    account_id = session.client("sts").get_caller_identity()["Account"]
    if account_id != cfg["account_id"]:
        raise RuntimeError("Refusing cleanup: AWS account does not match setup_config.json")
    cfn = None
    if args.cognito:
        # Optional convenience path requires a separately audited stack ledger.
        # A caller-supplied same-account stack name is not ownership proof.
        stack_id = cfg.get("cognito_stack_id")
        if not stack_id:
            raise RuntimeError("--cognito requires recorded cognito_stack_id and matching ownership tag")
        expected = f"arn:aws:cloudformation:{region}:{account_id}:stack/{stack_name}/"
        if not stack_id.startswith(expected) or not stack_id[len(expected) :]:
            raise RuntimeError("Cognito stack ARN/name/account/region mismatch")
        cfn = session.client("cloudformation")
        stack = cfn.describe_stacks(StackName=stack_id)["Stacks"][0]
        require_equal(stack["StackId"], stack_id, "Cognito stack ID")
        require_equal(stack["StackName"], stack_name, "Cognito stack name")
        tags = {t["Key"]: t["Value"] for t in stack.get("Tags", [])}
        require_equal(tags.get(OWNER_TAG), cfg.get("ownership_id"), "Cognito ownership")
        if not cfg.get("ownership_id"):
            raise RuntimeError("Missing ownership_id")
    cleanup(session.client("bedrock-agentcore-control", api_version=API_VERSION), session.client("iam"), cfg)
    if cfn:
        cfn.delete_stack(StackName=stack_id)
        cfn.get_waiter("stack_delete_complete").wait(StackName=stack_id)
    CONFIG_FILE.unlink()
    print("Recorded setup resources deleted. CLI resources still require README cleanup.")


if __name__ == "__main__":
    main()
