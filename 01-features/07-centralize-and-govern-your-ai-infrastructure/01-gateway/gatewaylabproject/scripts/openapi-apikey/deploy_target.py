"""Deploy the NASA OpenAPI target with API Key outbound auth.

Creates:
1. API key credential provider for NASA API key
2. Uploads OpenAPI spec to S3
3. Creates the OpenAPI gateway target with API key outbound auth

Requires NASA_API_KEY, GATEWAY_ID in environment.

Usage:
    uv run python scripts/openapi-apikey/deploy_target.py
"""

import json
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val


def main():
    load_env()

    nasa_api_key = get_required_env("NASA_API_KEY")
    gateway_id = get_required_env("GATEWAY_ID")

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    account_id = admin.account_id

    print("--- Creating API Key credential provider ---")
    cred = admin.client.create_api_key_credential_provider(
        name="nasa-openapi-api-key",
        apiKey=nasa_api_key,
    )
    cred_arn = cred["credentialProviderArn"]
    print(f"  Credential ARN: {cred_arn}")

    print("\n--- Uploading OpenAPI spec to S3 ---")
    s3 = boto3.client("s3", region_name=region)
    bucket_name = f"agentcore-openapi-specs-{account_id}-{region}"

    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        print(f"  Created bucket: {bucket_name}")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f"  Bucket already exists: {bucket_name}")

    spec_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "openapi-specs",
        "nasa_mars_insights_openapi.json",
    )
    s3.upload_file(spec_path, bucket_name, "nasa_mars_insights_openapi.json")
    spec_s3_uri = f"s3://{bucket_name}/nasa_mars_insights_openapi.json"
    print(f"  Uploaded spec: {spec_s3_uri}")

    print("\n--- Granting S3 read to gateway role ---")
    gw = admin.client.get_gateway(gatewayIdentifier=gateway_id)
    role_name = gw["roleArn"].split("/")[-1]
    s3_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": f"arn:aws:s3:::{bucket_name}/*",
            }
        ],
    }
    admin.iam.put_role_policy(
        RoleName=role_name,
        PolicyName="OpenApiSpecS3ReadPolicy",
        PolicyDocument=json.dumps(s3_policy),
    )
    print(f"  Granted s3:GetObject to {role_name}")

    print("\n--- Granting API key credential permissions ---")
    apikey_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetApiKeyCredential",
                    "bedrock-agentcore:GetResourceApiKey",
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "secretsmanager:GetSecretValue",
                ],
                "Resource": "*",
            }
        ],
    }
    admin.iam.put_role_policy(
        RoleName=role_name,
        PolicyName="ApiKeyCredentialPolicy",
        PolicyDocument=json.dumps(apikey_policy),
    )
    print(f"  Granted API key permissions to {role_name}")

    time.sleep(5)

    print("\n--- Creating OpenAPI target ---")
    target = admin.client.create_gateway_target(
        name="openapi-apikey-nasa-target",
        gatewayIdentifier=gateway_id,
        targetConfiguration={"mcp": {"openApiSchema": {"s3": {"uri": spec_s3_uri}}}},
        credentialProviderConfigurations=[
            {
                "credentialProviderType": "API_KEY",
                "credentialProvider": {
                    "apiKeyCredentialProvider": {
                        "providerArn": cred_arn,
                        "credentialParameterName": "api_key",
                        "credentialLocation": "QUERY_PARAMETER",
                    }
                },
            }
        ],
    )
    print(f"  Target ID: {target['targetId']}")

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    env_vars["GATEWAY_ID"] = gateway_id
    env_vars["CREDENTIAL_PROVIDER_ARN"] = cred_arn
    env_vars["S3_BUCKET"] = bucket_name
    env_vars["TARGET_ID"] = target["targetId"]
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    print("\n  Saved to .env")


if __name__ == "__main__":
    main()
