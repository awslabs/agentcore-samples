#!/usr/bin/env python3
"""
Deploy Amazon OpenSearch Serverless collection for the OBO_Gateway path.

This script provisions the AOSS substrate the OpenSearch_MCP_Server queries:

  1. Encryption policy (AWS-owned KMS key)
  2. Network policy (public access)
  3. Collection (SEARCH type, per design §4)
  4. Wait until the collection reaches ACTIVE status
  5. Data-access policy granting BOTH:
       (a) the OpenSearch MCP runtime role by its predictable ARN
           (read-only on collection + index), and
       (b) the loader principal (sts:GetCallerIdentity --> role ARN)
           (write/index permissions for load_sample_opensearch_data.py)

All three policy names are derived from the collection
name; no extra SSM keys are persisted for them.

Per design §10 reconciliation: this script runs BEFORE
deployment/4b-mcp-opensearch-server/deploy_runtime.py — the runtime cannot
deploy until opensearch-collection-{arn,endpoint} are in SSM (the runtime
IAM role's aoss:APIAccessAll statement scopes to a specific collection ARN).

SSM keys produced:
  /app/lakehouse-agent/opensearch-collection-arn
  /app/lakehouse-agent/opensearch-collection-endpoint

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
# 4b-mcp-opensearch-server/deploy_runtime.py:create_runtime_role(). The
# role's ARN is fully predictable from the role name (default IAM path).
RUNTIME_ROLE_NAME = "AgentCoreRuntimeRole-opensearch-mcp"

# Index name matches load_sample_opensearch_data.CLAIM_NOTES_INDEX.
CLAIM_NOTES_INDEX = "claim-notes"

# Derived AOSS policy names (derived from collection name; no
# additional SSM keys).
ENCRYPTION_POLICY_NAME = f"{COLLECTION_NAME}-encryption"
NETWORK_POLICY_NAME = f"{COLLECTION_NAME}-network"
DATA_ACCESS_POLICY_NAME = f"{COLLECTION_NAME}-data"


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

    4b-mcp-opensearch-server/deploy_runtime.py creates the
    role with default IAM path (no Path= kwarg), so the ARN is:
        arn:aws:iam::<account>:role/<role-name>
    The role does NOT have to exist at the time this script runs — AOSS
    does not validate principal existence at policy-create time. The
    runtime deploy in step 2 (4b/deploy_runtime.py) creates the role with
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

    # Wait for ACTIVE
    print("   ⏳ Waiting for collection to reach ACTIVE status...")
    deadline = time.time() + 600  # 10 minutes
    last_status = None
    while time.time() < deadline:
        details = aoss.batch_get_collection(ids=[collection_id])
        items = details.get("collectionDetails", [])
        if not items:
            print("   ⚠️  batch_get_collection returned no details; retrying...")
            time.sleep(10)
            continue
        item = items[0]
        status = item.get("status")
        if status != last_status:
            print(f"   Status: {status}")
            last_status = status
        if status == "ACTIVE":
            return {
                "id": item["id"],
                "arn": item["arn"],
                "endpoint": item["collectionEndpoint"],
            }
        if status == "FAILED":
            raise RuntimeError(f"Collection {collection_name} reached FAILED status")
        time.sleep(15)

    raise RuntimeError(
        f"Collection {collection_name} did not reach ACTIVE within 10 minutes (last status: {last_status})"
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

    Single-write at step 1 with both principals.
    AOSS does not validate principal existence at policy-create time, so the
    runtime role can be referenced even before 4b/deploy_runtime.py creates it.
    """
    print(f"\n🛡️  Data-access policy: {DATA_ACCESS_POLICY_NAME}")
    print(f"   Runtime role:     {runtime_role_arn}  (read)")
    print(f"   Loader principal: {loader_principal_arn}  (write/create-index)")

    try:
        aoss.get_access_policy(name=DATA_ACCESS_POLICY_NAME, type="data")
        print("   ⏭️  Already exists; skipping create")
        # Note: We do not diff the existing policy against the desired
        # principals. Tutorial-reader recovery path: run cleanup then
        # re-deploy. Drift between runs is the cleanup-script's
        # responsibility.
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
        print("   1. Deploy the OpenSearch MCP runtime (deployment/4b-mcp-opensearch-server/deploy_runtime.py)")
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
