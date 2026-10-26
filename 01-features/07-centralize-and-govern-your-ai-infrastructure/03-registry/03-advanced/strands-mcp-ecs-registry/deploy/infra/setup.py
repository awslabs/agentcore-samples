"""setup.py — One-time Registry Setup and S3 Artifact Upload

Run this ONCE after the CloudFormation stack is deployed to:
  1. Upload SKILL.md and supporting artifacts to S3
  2. Create the AWS Agent Registry
  3. Publish the MCP record (synchronizationType="URL" — registry crawler assumes the agent
     ECS task role and signs the request with IAM SigV4 / execute-api to fetch the live MCP
     server manifest from the API Gateway URL. Tool schemas are auto-populated from the server.
     The crawled URL is stored in descriptors.mcp.server.inlineContent → remotes[0].url.
     Agents read it from the registry at startup via search_registry_records.)
  4. Publish the AGENT_SKILLS record (stores full SKILL.md as inlineContent)
  5. Approve both records
  6. Store REGISTRY_ARN and SKILLS_BUCKET in SSM Parameter Store

The MCP server URL is stored in the Registry MCP record — not SSM.
Agents read the MCP URL from Registry at startup via search_registry_records.

Usage:
  python setup.py \
    --region us-east-1 \
    --bucket my-skills-bucket \
    --apigw-url https://xyz.execute-api.us-east-1.amazonaws.com/mcp

After running, only two SSM params are needed by ECS task definitions:
  REGISTRY_ARN  = <printed after registry creation>
  SKILLS_BUCKET = <your bucket name>
"""

import argparse
import json
import os
import time

from boto3.session import Session

SKILL_NAME = "quarterly-kpi-calculator"
SKILLS_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "my_skills", SKILL_NAME)


def separator(title):
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def wait_for_record(registry_client, registry_id, record_id, target="DRAFT"):
    print(f"  Waiting for record to reach {target}...")
    while True:
        r = registry_client.get_registry_record(registryId=registry_id, recordId=record_id)
        status = r["status"]
        print(f"    status: {status}")
        if status == target:
            return
        if status.endswith("_FAILED") or status == "FAILED":
            raise RuntimeError(f"Record failed: {status}")
        time.sleep(5)


def upload_skill_artifacts(s3_client, bucket: str, skill_name: str, skills_root: str):
    separator(f"Uploading skill artifacts to s3://{bucket}/skills/{skill_name}/")
    for dirpath, _, filenames in os.walk(skills_root):
        for fname in filenames:
            if fname.upper() == "SKILL.MD":
                continue  # SKILL.md is stored in registry, not S3
            local_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(local_path, skills_root)
            s3_key = f"skills/{skill_name}/{rel_path}"
            s3_client.upload_file(local_path, bucket, s3_key)
            print(f"  Uploaded: {local_path} → s3://{bucket}/{s3_key}")


def create_registry(registry_client) -> tuple[str, str]:
    separator("Creating Registry")
    resp = registry_client.create_registry(
        name="financial-skills-registry",
        description="Registry for financial skills and MCP tools",
        approvalConfiguration={"autoApprovalRules": []},
    )
    arn = resp["registryArn"]
    rid = arn.split("/")[-1]
    print(f"  Registry ARN : {arn}")
    print(f"  Registry ID  : {rid}")

    print("  Waiting for READY...")
    while True:
        r = registry_client.get_registry(registryId=rid)
        status = r["status"]
        print(f"    status: {status}")
        if status == "READY":
            break
        if status.endswith("_FAILED"):
            raise RuntimeError(f"Registry creation failed: {status}")
        time.sleep(5)
    return arn, rid


def publish_mcp_record(registry_client, registry_id: str, apigw_url: str, region: str, account_id: str) -> str:
    """Publish MCP record using synchronizationType=URL with IAM credential provider.

    The registry crawls the API Gateway HTTPS endpoint using the agent ECS task role
    to sign the request (IAM SigV4 / execute-api service). This auto-populates the
    tool schemas from the live MCP server without needing to supply them manually.

    The resulting registry record stores the server URL. The agent reads the URL
    from this record at startup to connect to the MCP server.
    """
    separator("Publishing MCP Record (URL sync with IAM auth)")
    print(f"  API Gateway URL: {apigw_url}")

    task_role_arn = f"arn:aws:iam::{account_id}:role/financial-agent-agent-task-role"
    print(f"  IAM Role ARN:   {task_role_arn}")

    resp = registry_client.create_registry_record(
        registryId=registry_id,
        name="financial-tools-mcp",
        description=(
            "MCP server providing financial tools: "
            "get_financial_data (quarterly P&L data retrieval), "
            "get_kpi_benchmarks (industry benchmark thresholds and formulas). "
            "Server endpoint: " + apigw_url
        ),
        recordType="MCP",
        descriptors={
            "mcpServer": {
                "source": {
                    "fromUrl": {
                        "url": apigw_url,
                        "credentialProviderConfigurations": [
                            {
                                "credentialProviderType": "IAM",
                                "credentialProvider": {
                                    "iamCredentialProvider": {
                                        "roleArn": task_role_arn,
                                        "service": "execute-api",
                                        "region": region,
                                    }
                                },
                            }
                        ],
                    }
                }
            }
        },
        recordVersion="1.0",
    )
    record_id = resp["recordArn"].split("/")[-1]
    print(f"  MCP Record ID: {record_id}")
    wait_for_record(registry_client, registry_id, record_id, "DRAFT")
    return record_id


def publish_skill_record(registry_client, registry_id: str, skills_root: str) -> str:
    separator("Publishing AGENT_SKILLS Record")
    skill_md_path = os.path.join(skills_root, "SKILL.md")
    with open(skill_md_path, encoding="utf-8") as f:
        skill_md = f.read()
    print(f"  SKILL.md: {len(skill_md)} chars")

    resp = registry_client.create_registry_record(
        registryId=registry_id,
        name=SKILL_NAME,
        description=(
            "Calculates quarterly financial KPIs from P&L data. "
            "Use for Gross Margin %, EBITDA Margin %, Operating Expense Ratio, "
            "Revenue Growth % QoQ, quarterly performance review, or P&L analysis."
        ),
        recordType="SKILL",
        descriptors={
            "agentSkillsDefinition": {
                "data": json.dumps({"packages": []}),
                "additionalData": {"skillMd": {"data": skill_md}},
            }
        },
        recordVersion="1.0",
    )
    record_id = resp["recordArn"].split("/")[-1]
    print(f"  AGENT_SKILLS Record ID: {record_id}")
    wait_for_record(registry_client, registry_id, record_id, "DRAFT")
    return record_id


def approve_record(registry_client, registry_id: str, record_id: str, label: str):
    registry_client.submit_registry_record_for_approval(registryId=registry_id, recordId=record_id)
    print(f"  {label}: Submitted → PENDING_APPROVAL")
    registry_client.update_registry_record_status(
        registryId=registry_id,
        recordId=record_id,
        status="APPROVED",
        statusReason="Approved by admin during setup",
    )
    print(f"  {label}: Approved → APPROVED")


def store_ssm(ssm_client, name: str, value: str):
    ssm_client.put_parameter(
        Name=name,
        Value=value,
        Type="String",
        Overwrite=True,
    )
    print(f"  SSM: {name} = {value[:60]}...")


def main():
    parser = argparse.ArgumentParser(description="Setup Agent Registry and S3 artifacts")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--bucket", required=True, help="S3 bucket for skill artifacts")
    parser.add_argument(
        "--apigw-url",
        required=True,
        help="API Gateway HTTPS URL for MCP server, e.g. https://xyz.execute-api.us-east-1.amazonaws.com/mcp",
    )
    parser.add_argument("--skip-s3", action="store_true", help="Skip S3 artifact upload (already done)")
    parser.add_argument(
        "--registry-arn",
        default="",
        help="Existing registry ARN (skip registry creation)",
    )
    args = parser.parse_args()

    if not args.apigw_url.startswith("https://"):
        raise SystemExit("--apigw-url must start with https://")

    session = Session(region_name=args.region)
    registry_client = session.client("agent-registry-control")
    s3_client = session.client("s3")
    ssm_client = session.client("ssm")

    # 1. Upload skill artifacts to S3
    if args.skip_s3:
        separator("Skipping S3 upload (--skip-s3 set)")
    else:
        upload_skill_artifacts(s3_client, args.bucket, SKILL_NAME, SKILLS_ROOT)

    # 2. Create registry (or reuse existing)
    if args.registry_arn:
        separator("Reusing existing Registry")
        registry_arn = args.registry_arn
        registry_id = registry_arn.split("/")[-1]
        print(f"  Registry ARN : {registry_arn}")
        print(f"  Registry ID  : {registry_id}")
    else:
        registry_arn, registry_id = create_registry(registry_client)

    # 3. Publish and approve MCP record
    #    Registry crawls the API GW URL with IAM SigV4 to auto-populate tool schemas.
    #    Agents read the MCP URL from this record at startup via search_registry_records.
    account_id = session.client("sts").get_caller_identity()["Account"]
    mcp_record_id = publish_mcp_record(registry_client, registry_id, args.apigw_url, args.region, account_id)
    approve_record(registry_client, registry_id, mcp_record_id, "MCP")

    # 4. Publish and approve AGENT_SKILLS record
    skill_record_id = publish_skill_record(registry_client, registry_id, SKILLS_ROOT)
    approve_record(registry_client, registry_id, skill_record_id, "AGENT_SKILLS")

    # 5. Store minimal config in SSM — only REGISTRY_ARN and SKILLS_BUCKET.
    #    MCP server URL is stored in the Registry MCP record, not SSM.
    separator("Storing config in SSM Parameter Store")
    store_ssm(ssm_client, "/financial-agent/registry-arn", registry_arn)
    store_ssm(ssm_client, "/financial-agent/skills-bucket", args.bucket)

    separator("Setup complete")
    print(f"""
SSM parameters written:
  /financial-agent/registry-arn   = {registry_arn}
  /financial-agent/skills-bucket  = {args.bucket}

MCP server URL is stored in the Registry MCP record (not SSM).
Agents discover it via search_registry_records at startup.

Note: Search index takes ~100s to reflect approved records.
""")


if __name__ == "__main__":
    main()
