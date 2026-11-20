"""Delete the resources recorded by this deployment. Usage: python cleanup.py"""

import json
import time

import boto3
import deployment_state as receipt


def verify_role(iam, name, role_id):
    # An ARN/name can be reused; RoleId changes if a role is re-created.
    if iam.get_role(RoleName=name)["Role"]["RoleId"] != role_id:
        raise RuntimeError(f"Refusing cleanup: {name} is not the role created by this deployment.")


def remove_role(iam, name, role_id, policy):
    verify_role(iam, name, role_id)
    policies = iam.list_role_policies(RoleName=name)
    if policies.get("IsTruncated") or set(policies["PolicyNames"]) - {policy}:
        raise RuntimeError(f"Refusing to remove {name}: it has additional inline policies.")
    if policy in policies["PolicyNames"]:
        iam.delete_role_policy(RoleName=name, PolicyName=policy)
    verify_role(iam, name, role_id)
    iam.delete_role(RoleName=name)


def remove_and_wait(get, delete, missing, *, created_at=None, **params):
    """Do not remove a dependent resource until the service reports absence."""
    try:
        resource = get(**params)
        if created_at and resource["createdAt"].isoformat() != created_at:
            raise RuntimeError("Refusing to delete a replacement endpoint.")
        if resource["status"] != "DELETING":
            delete(**params)
        for _ in range(60):
            status = get(**params)["status"]
            if status == "DELETE_FAILED":
                raise RuntimeError("Service reported DELETE_FAILED; keeping the deployment receipt.")
            time.sleep(5)
    except missing:
        return
    raise TimeoutError("Deletion is not confirmed. Retry cleanup with the same runtime_config.json.")


@receipt.exclusive_operation()
def main():
    state = json.loads(receipt.CONFIG.read_text())
    if state.get("pending") or not state.get("role_id"):
        raise SystemExit("Missing ownership receipt or unconfirmed create. Reconcile manually; nothing deleted.")
    session = boto3.Session(region_name=state["region"])
    if session.client("sts").get_caller_identity()["Account"] != state["account_id"]:
        raise SystemExit("Wrong AWS account; nothing deleted.")

    name = state["agent_name"]
    iam = session.client("iam")
    role_name = f"agentcore-{name}-role"
    verify_role(iam, role_name, state["role_id"])

    if "runtime_id" in state:
        control = session.client("bedrock-agentcore-control")
        missing = control.exceptions.ResourceNotFoundException
        params = {"agentRuntimeId": state["runtime_id"]}
        if "endpoint_created_at" in state:
            remove_and_wait(
                control.get_agent_runtime_endpoint,
                control.delete_agent_runtime_endpoint,
                missing,
                **params,
                endpointName="default",
                created_at=state["endpoint_created_at"],
            )
            del state["endpoint_created_at"]
            receipt.save(state)
        remove_and_wait(control.get_agent_runtime, control.delete_agent_runtime, missing, **params)
        del state["runtime_id"], state["runtime_arn"]
        receipt.save(state)

    if "etag" in state:
        session.client("s3").delete_object(
            Bucket=state["bucket"],
            Key=state["key"],
            ExpectedBucketOwner=state["account_id"],
            IfMatch=state["etag"],
            **({"VersionId": state["version_id"]} if "version_id" in state else {}),
        )
        del state["etag"]
        receipt.save(state)

    remove_role(iam, role_name, state["role_id"], f"{name}-policy")
    receipt.CONFIG.unlink()
    print(f"Cleanup complete for {name}. The shared code bucket was retained.")


if __name__ == "__main__":
    main()
