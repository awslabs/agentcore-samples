import boto3
import json
import time
import botocore.exceptions


def assume_role(role_arn, session_name="my-session"):
    """Assume an IAM role and return temporary credentials."""
    sts = boto3.client("sts")
    response = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
    )
    creds = response["Credentials"]
    print(f"Assumed role: {response['AssumedRoleUser']['Arn']}")

    return creds


def assume_role_only(AWS_REGION, role_arn, session_name="test-session"):
    """Assume an IAM role"""
    sts_client = boto3.client("sts", region_name=AWS_REGION)
    response = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
    )
    return response


def pp(response):
    """Pretty-print API response, stripping ResponseMetadata."""
    data = {k: v for k, v in response.items() if k != "ResponseMetadata"}
    print(json.dumps(data, indent=2, default=str))


def wait_for_record_ready(publisher_cp_client, registry_id, record_id, interval=5, timeout=120):
    """Poll GetRegistryRecord until the record exits CREATING/UPDATING status."""
    deadline = time.time() + timeout
    while True:
        resp = publisher_cp_client.get_registry_record(registryId=registry_id, recordId=record_id)
        status = resp["status"]
        print(f"  Record {record_id} status: {status}")
        if status not in ("CREATING", "UPDATING"):
            return resp
        if time.time() >= deadline:
            raise TimeoutError(f"Record {record_id} still in {status} after {timeout}s.")
        time.sleep(interval)


print("Helper functions defined: pp, wait_for_record_ready")


def filter_pending_records(records):
    """Return only records with status PENDING_APPROVAL."""
    return [r for r in records if r.get("status") == "PENDING_APPROVAL"]


def list_records_with_ids(client, registry_id, **kwargs):
    """Wrapper around list_registry_records that extracts recordId from raw HTTP response.

    The preview SDK model uses 'registryRecordId' but the service returns 'recordId'.
    This function parses the raw JSON to get the actual record IDs.
    """
    import json as _json

    original_make_request = client._endpoint.make_request
    raw_body = {}

    def capture_request(operation_model, request_dict):
        result = original_make_request(operation_model, request_dict)
        http_response = result[0]
        raw_body["data"] = _json.loads(http_response.content.decode("utf-8"))
        return result

    client._endpoint.make_request = capture_request
    try:
        client.list_registry_records(registryId=registry_id, **kwargs)
    finally:
        client._endpoint.make_request = original_make_request

    return raw_body.get("data", {}).get("registryRecords", [])


def get_or_select_registry(cp_client, registry_id=None, AWS_REGION="us-west-2"):
    """List registries and return (registry_id, registry_arn) for a READY registry.

    Args:
        cp_client: Bedrock AgentCore control plane client.
        registry_id: Optional specific registry ID to use. If None, picks the first READY one.
        aws_region: AWS region (used in error messages).

    Returns:
        Tuple of (registry_id, registry_arn).

    Raises:
        ValueError: If specified registry not found or not READY.
        RuntimeError: If no READY registry exists.
    """
    try:
        resp = cp_client.list_registries()
        all_registries = resp.get("registries", [])
        print(f"Found {len(all_registries)} registries:\n")
        for reg in all_registries:
            print(f"  [{reg['status']}] {reg['name']} ({reg['registryId']})")

        ready = [r for r in all_registries if r["status"] == "READY"]

        if registry_id:
            match = [r for r in all_registries if r["registryId"] == registry_id]
            if not match:
                raise ValueError(f"Registry {registry_id} not found.")
            if match[0]["status"] != "READY":
                raise ValueError(f"Registry {registry_id} is {match[0]['status']}, not READY.")
            rid, rarn = match[0]["registryId"], match[0]["registryArn"]
            print(f"\n✅ Using specified registry: {rid}")
        elif ready:
            rid, rarn = ready[0]["registryId"], ready[0]["registryArn"]
            print(f"\n✅ Using registry: {ready[0]['name']} (ID: {rid})")
        else:
            raise RuntimeError("No READY registry available. Run notebook 02 first.")

        print(f"\nRegistry ID:  {rid}")
        print(f"Registry ARN: {rarn}")
        return rid, rarn

    except botocore.exceptions.EndpointConnectionError as e:
        print(f"❌ Cannot reach bedrock-agentcore-control in {AWS_REGION}. Error: {e}")
        raise
    except botocore.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        print(f"❌ Error listing registries: {code} — {e}")
        if code == "AccessDeniedException":
            print("   Verify admin_persona has bedrock-agentcore:ListRegistries permission.")
        raise


def build_trust_policy(sagemaker_role_arn):
    """Build a trust policy allowing both the SageMaker role and AgentCore service."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            },
            {
                "Effect": "Allow",
                "Principal": {"AWS": sagemaker_role_arn},
                "Action": "sts:AssumeRole",
            },
        ],
    }


def build_permissions_policy(actions):
    """Build a permissions policy for the given actions."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": actions,
                "Resource": "*",
            }
        ],
    }


def create_or_update_persona_role(iam_client, role_name, policy_name, actions, trust_policy, ACCOUNT_ID):
    """Create an IAM role or update it if it already exists."""
    try:
        resp = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=f"AgentCore Registry - {role_name}",
        )
        role_arn = resp["Role"]["Arn"]
        print(f"  Created role: {role_arn}")
    except iam_client.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
        print(f"  Role already exists: {role_arn} — updating...")
        iam_client.update_assume_role_policy(
            RoleName=role_name,
            PolicyDocument=json.dumps(trust_policy),
        )

    # Attach/update the inline permissions policy
    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName=policy_name,
        PolicyDocument=json.dumps(build_permissions_policy(actions)),
    )
    print(f"  Attached policy: {policy_name}")
    return role_arn


def extract_role_arn(caller_arn):
    """Get the actual IAM role ARN from the caller identity.

    The assumed-role ARN format loses the role path (e.g., /service-role/).
    We extract the role name and look it up via IAM to get the full ARN.
    """
    if ":assumed-role/" in caller_arn:
        role_name = caller_arn.split(":")[-1].split("/")[1]
        # Look up the actual role to get the full ARN with path
        iam = boto3.client("iam")
        role_info = iam.get_role(RoleName=role_name)
        return role_info["Role"]["Arn"]
    return caller_arn
