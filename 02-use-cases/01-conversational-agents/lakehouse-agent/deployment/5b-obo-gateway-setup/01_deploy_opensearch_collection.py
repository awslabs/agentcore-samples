#!/usr/bin/env python3
"""
Deploy Amazon OpenSearch Serverless collection for the OBO_Gateway path.

Run by notebook 05b-deploy-notes-gateway.ipynb, Step 1, on both IdP paths.
This script provisions the AOSS resources the OpenSearch_MCP_Server queries:

  1. Encryption policy lakehouse-claim-notes-encryption (AWS-owned KMS key)
  2. Network policy lakehouse-claim-notes-network (public access)
  3. Collection lakehouse-claim-notes (SEARCH type, standby replicas off)
  4. Wait until the collection reaches ACTIVE status (up to 15 minutes),
     so the endpoint stored in SSM is ready to serve requests
  5. Data-access policy lakehouse-claim-notes-data granting BOTH:
       (a) the OpenSearch MCP runtime role by its predictable ARN
           (read-only on collection + index), and
       (b) the loader principal (sts:GetCallerIdentity --> role ARN)
           (write/index permissions for load_sample_opensearch_data.py)

All three policy names are derived from the collection
name; no extra SSM keys are persisted for them.

Order: this script runs BEFORE
the OpenSearch MCP runtime role and deploy (setup_runtime_roles.py create
--role opensearch-mcp, then agentcore_cli_deploy.py opensearch-mcp) — the runtime cannot
deploy until opensearch-collection-{arn,endpoint} are in SSM (the runtime
IAM role's aoss:APIAccessAll statement scopes to a specific collection ARN).

SSM keys produced:
  /app/lakehouse-agent/opensearch-collection-arn
      read by setup_runtime_roles.py to scope the runtime role's
      aoss:APIAccessAll statement
  /app/lakehouse-agent/opensearch-collection-endpoint
      read by agentcore_cli_deploy.py opensearch-mcp, which sets it as the
      runtime's OPENSEARCH_COLLECTION_ENDPOINT environment variable, and by
      4b-mcp-opensearch-server/load_sample_opensearch_data.py

Idempotent: each create call is preceded by a list/get probe; existing
resources are left in place. Re-running the script after a successful first
run is a no-op.

Usage:
    python 01_deploy_opensearch_collection.py
"""

import json
import sys
import time

import boto3

# Collection name — short, kebab-case, scoped to this demo's purpose.
COLLECTION_NAME = "lakehouse-claim-notes"

# The runtime IAM role name is hardcoded in
# 2-lakehouse-tenant-roles-setup/setup_runtime_roles.py (--role opensearch-mcp). The
# role's ARN is fully predictable from the role name (default IAM path).
RUNTIME_ROLE_NAME = "AgentCoreRuntimeRole-opensearch-mcp"

# Index name matches load_sample_opensearch_data.CLAIM_NOTES_INDEX.
CLAIM_NOTES_INDEX = "claim-notes"

# Derived AOSS policy names (derived from collection name; no
# additional SSM keys).
ENCRYPTION_POLICY_NAME = f"{COLLECTION_NAME}-encryption"
NETWORK_POLICY_NAME = f"{COLLECTION_NAME}-network"
DATA_ACCESS_POLICY_NAME = f"{COLLECTION_NAME}-data"

# How long to wait for a new collection to reach ACTIVE, and how often to check.
# Creation usually takes 3-5 minutes but has been seen to take over 10, so the
# wait allows 15. Every poll prints the status and the elapsed time.
COLLECTION_WAIT_SECONDS = 900
COLLECTION_POLL_SECONDS = 15


class SSMConfig:
    """Minimal config loader / persister for AOSS collection setup."""

    def __init__(self):
        session = boto3.Session()
        self.region = session.region_name
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.sts = boto3.client("sts", region_name=self.region)
        self.account_id = self.sts.get_caller_identity()["Account"]

        print("✅ Configuration loaded")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")

    def store_collection_parameters(self, collection_arn: str, collection_endpoint: str):
        """Persist collection ARN + endpoint to SSM."""
        print("\n💾 Storing collection configuration in SSM Parameter Store...")

        parameters = [
            {
                "name": "/app/lakehouse-agent/opensearch-collection-arn",
                "value": collection_arn,
                "description": "AOSS collection ARN for OpenSearch_MCP_Server",
            },
            {
                "name": "/app/lakehouse-agent/opensearch-collection-endpoint",
                "value": collection_endpoint,
                "description": "AOSS collection endpoint URL for OpenSearch_MCP_Server",
            },
        ]

        for param in parameters:
            try:
                self.ssm.put_parameter(
                    Name=param["name"],
                    Value=param["value"],
                    Description=param["description"],
                    Type="String",
                    Overwrite=True,
                )
                print(f"✅ Stored parameter: {param['name']} = {param['value']}")
            except Exception as e:
                print(f"❌ Error storing parameter {param['name']}: {e}")
                raise


def get_runtime_role_arn(account_id: str) -> str:
    """
    Build the predictable runtime role ARN.

    2-lakehouse-tenant-roles-setup/setup_runtime_roles.py creates the
    role with default IAM path (no Path= kwarg), so the ARN is:
        arn:aws:iam::<account>:role/<role-name>
    The role does NOT have to exist at the time this script runs — AOSS
    does not validate principal existence at policy-create time. The
    role-creation step that follows (setup_runtime_roles.py create --role opensearch-mcp) creates the role with
    this exact name, and the data-access policy already authorizes it.
    """
    return f"arn:aws:iam::{account_id}:role/{RUNTIME_ROLE_NAME}"


def get_loader_principal_arn(sts) -> str:
    """
    Resolve the loader principal's IAM role ARN via GetCallerIdentity.

    GetCallerIdentity returns the *session* ARN for an assumed role
    (e.g. arn:aws:sts::<acct>:assumed-role/<role-name>/<session-name>).
    AOSS data-access policies must reference the *role* ARN
    (arn:aws:iam::<acct>:role/<role-name>) — not the session. Convert.

    Returns:
        The loader's IAM role ARN suitable for AOSS data-access-policy
        Principal.
    """
    identity = sts.get_caller_identity()
    arn = identity["Arn"]
    account_id = identity["Account"]

    if ":assumed-role/" in arn:
        # Convert: arn:aws:sts::<acct>:assumed-role/<role>/<session>
        # To:      arn:aws:iam::<acct>:role/<role>
        role_name = arn.split(":assumed-role/")[1].split("/")[0]
        role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
        print(f"   Loader principal (from assumed-role session): {arn}")
        print(f"   Loader principal (resolved to role ARN):      {role_arn}")
        return role_arn

    # Already an IAM user / role ARN — use as-is.
    print(f"   Loader principal: {arn}")
    return arn


def ensure_encryption_policy(aoss, collection_name: str) -> None:
    """
    Idempotent encryption policy create. AWS-owned KMS key.
    """
    print(f"\n🔐 Encryption policy: {ENCRYPTION_POLICY_NAME}")

    try:
        aoss.get_security_policy(name=ENCRYPTION_POLICY_NAME, type="encryption")
        print("   ⏭️  Already exists; skipping create")
        return
    except aoss.exceptions.ResourceNotFoundException:
        pass

    policy_body = {
        "Rules": [{"Resource": [f"collection/{collection_name}"], "ResourceType": "collection"}],
        "AWSOwnedKey": True,
    }

    aoss.create_security_policy(
        name=ENCRYPTION_POLICY_NAME,
        type="encryption",
        policy=json.dumps(policy_body),
        description=f"Encryption policy for {collection_name} (AWS-owned KMS)",
    )
    print("   ✅ Created (AWS-owned KMS key)")


def ensure_network_policy(aoss, collection_name: str) -> None:
    """
    Idempotent network policy create. Public access.
    """
    print(f"\n🌐 Network policy: {NETWORK_POLICY_NAME}")

    try:
        aoss.get_security_policy(name=NETWORK_POLICY_NAME, type="network")
        print("   ⏭️  Already exists; skipping create")
        return
    except aoss.exceptions.ResourceNotFoundException:
        pass

    policy_body = [
        {
            "Rules": [{"Resource": [f"collection/{collection_name}"], "ResourceType": "collection"}],
            "AllowFromPublic": True,
        }
    ]

    aoss.create_security_policy(
        name=NETWORK_POLICY_NAME,
        type="network",
        policy=json.dumps(policy_body),
        description=f"Network policy for {collection_name} (public access)",
    )
    print("   ✅ Created (public access)")


def ensure_collection(aoss, collection_name: str) -> dict:
    """
    Idempotent collection create. Returns {arn, endpoint, id}.
    Waits for ACTIVE status before returning.
    """
    print(f"\n🗄️  Collection: {collection_name}")

    # Probe via list_collections + name filter (AOSS has no get_collection_by_name).
    response = aoss.list_collections(collectionFilters={"name": collection_name})
    summaries = response.get("collectionSummaries", [])

    if summaries:
        existing = summaries[0]
        print(f"   ⏭️  Already exists; id={existing['id']}, status={existing['status']}")
        # Still return the active details (need ARN/endpoint either way)
        collection_id = existing["id"]
    else:
        print("   Creating SEARCH-type collection...")
        create_response = aoss.create_collection(
            name=collection_name,
            type="SEARCH",
            description="Free-text claim-note search for OpenSearch_MCP_Server",
            standbyReplicas="DISABLED",
            tags=[
                {"key": "Application", "value": "lakehouse-agent"},
                {"key": "Purpose", "value": "claim-notes"},
            ],
        )
        collection_id = create_response["createCollectionDetail"]["id"]
        print(f"   ✅ Create initiated; id={collection_id}")

    return wait_for_collection_active(aoss, collection_name, collection_id)


def wait_for_collection_active(aoss, collection_name: str, collection_id: str) -> dict:
    """
    Poll the collection until it is ACTIVE. Returns {id, arn, endpoint}.

    Prints the status and elapsed time on every poll. Raises at once if the
    collection fails or is being deleted. If it is still being created when
    the wait runs out, raises an error that says how to check it and that
    re-running this script is safe.
    """
    wait_minutes = COLLECTION_WAIT_SECONDS // 60
    print(f"   ⏳ Waiting up to {wait_minutes} minutes for the collection to reach ACTIVE status...")
    start = time.time()
    deadline = start + COLLECTION_WAIT_SECONDS
    last_status = None
    while time.time() < deadline:
        details = aoss.batch_get_collection(ids=[collection_id])
        items = details.get("collectionDetails", [])
        elapsed = int(time.time() - start)
        if not items:
            print(f"   ⚠️  batch_get_collection returned no details ({elapsed}s elapsed); retrying...")
            time.sleep(10)
            continue
        item = items[0]
        status = item.get("status")
        last_status = status
        print(f"   Status: {status} ({elapsed}s elapsed)")
        if status == "ACTIVE":
            return {
                "id": item["id"],
                "arn": item["arn"],
                "endpoint": item["collectionEndpoint"],
            }
        if status in ("FAILED", "UPDATE_FAILED"):
            raise RuntimeError(f"Collection {collection_name} (id {collection_id}) reached {status} status")
        if status == "DELETING":
            raise RuntimeError(
                f"Collection {collection_name} (id {collection_id}) is being deleted. "
                "Wait for the delete to finish, then re-run this script."
            )
        time.sleep(COLLECTION_POLL_SECONDS)

    raise RuntimeError(
        f"Collection {collection_name} (id {collection_id}) is still {last_status} after "
        f"{wait_minutes} minutes. It is still being created on the AWS side and usually finishes "
        "on its own. Check its status with:\n"
        f"    aws opensearchserverless batch-get-collection --ids {collection_id}\n"
        "Once it shows ACTIVE, re-run this step. It is safe to re-run: it adopts the existing "
        "collection, then creates the data-access policy and stores the SSM parameters."
    )


def ensure_data_access_policy(aoss, collection_name: str, runtime_role_arn: str, loader_principal_arn: str) -> None:
    """
    Idempotent data-access policy create.

    Grants:
      - Runtime role: read-only (aoss:ReadDocument, aoss:DescribeIndex)
        on collection + indexes.
      - Loader principal: write/create-index (aoss:CreateIndex,
        aoss:WriteDocument, aoss:UpdateIndex, aoss:DescribeIndex) on
        collection + indexes.

    Written once, with both principals in the same policy.
    AOSS does not validate principal existence at policy-create time, so the
    runtime role can be referenced even before setup_runtime_roles.py creates it.
    """
    print(f"\n🛡️  Data-access policy: {DATA_ACCESS_POLICY_NAME}")
    print(f"   Runtime role:     {runtime_role_arn}  (read)")
    print(f"   Loader principal: {loader_principal_arn}  (write/create-index)")

    try:
        aoss.get_access_policy(name=DATA_ACCESS_POLICY_NAME, type="data")
        print("   ⏭️  Already exists; skipping create")
        # The existing policy is not compared against the desired
        # principals. If the loader identity changes between runs, run
        # cleanup (06_cleanup_obo_gateway.py) and then re-deploy.
        return
    except aoss.exceptions.ResourceNotFoundException:
        pass

    policy_body = [
        {
            "Rules": [
                {
                    "Resource": [f"collection/{collection_name}"],
                    "Permission": ["aoss:DescribeCollectionItems"],
                    "ResourceType": "collection",
                },
                {
                    "Resource": [f"index/{collection_name}/*"],
                    "Permission": ["aoss:DescribeIndex", "aoss:ReadDocument"],
                    "ResourceType": "index",
                },
            ],
            "Principal": [runtime_role_arn],
            "Description": "Read-only access for the OpenSearch_MCP_Server runtime role",
        },
        {
            "Rules": [
                {
                    "Resource": [f"collection/{collection_name}"],
                    "Permission": [
                        "aoss:CreateCollectionItems",
                        "aoss:DescribeCollectionItems",
                        "aoss:UpdateCollectionItems",
                    ],
                    "ResourceType": "collection",
                },
                {
                    "Resource": [f"index/{collection_name}/*"],
                    "Permission": [
                        "aoss:CreateIndex",
                        "aoss:DescribeIndex",
                        "aoss:UpdateIndex",
                        "aoss:WriteDocument",
                        "aoss:ReadDocument",
                    ],
                    "ResourceType": "index",
                },
            ],
            "Principal": [loader_principal_arn],
            "Description": "Loader principal: index/document create + read for sample-data load",
        },
    ]

    aoss.create_access_policy(
        name=DATA_ACCESS_POLICY_NAME,
        type="data",
        policy=json.dumps(policy_body),
        description=f"Data-access policy for {collection_name}",
    )
    print("   ✅ Created with 2 rule blocks (runtime read + loader write)")


def main():
    print("=" * 70)
    print("AOSS Collection Setup for OpenSearch_MCP_Server")
    print("=" * 70)

    config = SSMConfig()

    aoss = boto3.client("opensearchserverless", region_name=config.region)

    runtime_role_arn = get_runtime_role_arn(config.account_id)
    print(f"\n📋 Runtime role ARN (predictable): {runtime_role_arn}")

    print("\n🔎 Resolving loader principal via sts:GetCallerIdentity...")
    loader_principal_arn = get_loader_principal_arn(config.sts)

    try:
        # Order matters: encryption + network policies MUST exist before
        # collection creation (AOSS rejects create_collection otherwise).
        print("\n" + "=" * 70)
        print("Step 1: Encryption policy")
        print("=" * 70)
        ensure_encryption_policy(aoss, COLLECTION_NAME)

        print("\n" + "=" * 70)
        print("Step 2: Network policy")
        print("=" * 70)
        ensure_network_policy(aoss, COLLECTION_NAME)

        print("\n" + "=" * 70)
        print("Step 3: Collection")
        print("=" * 70)
        collection = ensure_collection(aoss, COLLECTION_NAME)
        print(f"\n   ARN:      {collection['arn']}")
        print(f"   Endpoint: {collection['endpoint']}")

        print("\n" + "=" * 70)
        print("Step 4: Data-access policy (single write, both principals)")
        print("=" * 70)
        ensure_data_access_policy(aoss, COLLECTION_NAME, runtime_role_arn, loader_principal_arn)

        print("\n" + "=" * 70)
        print("Step 5: SSM Parameter Store")
        print("=" * 70)
        config.store_collection_parameters(collection_arn=collection["arn"], collection_endpoint=collection["endpoint"])

        print("\n" + "=" * 70)
        print("AOSS Collection Setup Complete!")
        print("=" * 70)
        print(f"\n✅ Collection: {COLLECTION_NAME}")
        print(f"   ARN:      {collection['arn']}")
        print(f"   Endpoint: {collection['endpoint']}")
        print("\n📋 Next Steps:")
        print("   1. Create the runtime role and deploy the OpenSearch MCP runtime:")
        print("      setup_runtime_roles.py create --role opensearch-mcp, then agentcore_cli_deploy.py opensearch-mcp")
        print("   2. Verify deploy via 02_verify_opensearch_mcp.py")
        print("   3. Continue notebook 05b cells (oauth provider, OBO gateway, ...)")
        print("\n" + "=" * 70)

    except Exception as e:
        print(f"\n❌ AOSS collection setup failed: {e!s}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
