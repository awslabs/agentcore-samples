"""Create Zendesk OAuth credential provider and OpenAPI target.

Zendesk does not have an OIDC discovery endpoint, so we use
authorizationServerMetadata with explicit token endpoint.

Requires GATEWAY_ID, ZENDESK_DOMAIN, ZENDESK_TOKEN_ENDPOINT,
ZENDESK_CLIENT_ID, ZENDESK_SECRET in environment.

Usage:
    uv run python scripts/openapi-oauth/deploy_target.py
"""

import json
import os
import sys

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

    gateway_id = get_required_env("GATEWAY_ID")
    zendesk_domain = get_required_env("ZENDESK_DOMAIN")
    zendesk_token_endpoint = get_required_env("ZENDESK_TOKEN_ENDPOINT")
    zendesk_client_id = get_required_env("ZENDESK_CLIENT_ID")
    zendesk_secret = get_required_env("ZENDESK_SECRET")

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    print("--- Creating Zendesk OAuth credential provider ---")
    provider_config = {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {
                "authorizationServerMetadata": {
                    "issuer": zendesk_domain,
                    "authorizationEndpoint": f"{zendesk_domain}/oauth/authorizations/new",
                    "tokenEndpoint": zendesk_token_endpoint,
                    "responseTypes": ["token"],
                }
            },
            "clientId": zendesk_client_id,
            "clientSecret": zendesk_secret,
        }
    }

    response = control.create_oauth2_credential_provider(
        name="zendesk-oauth-credential",
        credentialProviderVendor="CustomOauth2",
        oauth2ProviderConfigInput=provider_config,
    )
    cred_arn = response["credentialProviderArn"]
    print(f"  ARN: {cred_arn}")

    print("\n--- Uploading OpenAPI spec to S3 ---")
    s3 = boto3.client("s3", region_name=region)
    bucket_name = f"agentcore-openapi-specs-{admin.account_id}-{region}"

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
        "Zendesk-support-apis.yaml",
    )
    with open(spec_path) as f:
        spec_content = f.read()
    spec_content = spec_content.replace(
        "https://ZENDESK_DOMAIN_PLACEHOLDER", zendesk_domain
    )
    s3.put_object(
        Bucket=bucket_name, Key="Zendesk-support-apis.yaml", Body=spec_content
    )
    spec_s3_uri = f"s3://{bucket_name}/Zendesk-support-apis.yaml"
    print(f"  Uploaded (with server URL set to {zendesk_domain}): {spec_s3_uri}")

    print("\n--- Granting S3 read to gateway role ---")
    gw = control.get_gateway(gatewayIdentifier=gateway_id)
    role_name = gw["roleArn"].split("/")[-1]
    admin.iam.put_role_policy(
        RoleName=role_name,
        PolicyName="OpenApiSpecS3ReadPolicy",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject"],
                        "Resource": f"arn:aws:s3:::{bucket_name}/*",
                    }
                ],
            }
        ),
    )
    print(f"  Granted s3:GetObject to {role_name}")

    print("\n--- Granting OAuth credential permissions to gateway role ---")
    admin.iam.put_role_policy(
        RoleName=role_name,
        PolicyName="OAuthCredentialPolicy",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "bedrock-agentcore:GetResourceOauth2Token",
                            "bedrock-agentcore:GetWorkloadAccessToken",
                            "secretsmanager:GetSecretValue",
                        ],
                        "Resource": "*",
                    }
                ],
            }
        ),
    )
    print(f"  Granted OAuth credential permissions to {role_name}")

    print("\n--- Creating OpenAPI target with OAuth outbound auth ---")
    target = control.create_gateway_target(
        name="openapi-zendesk-target",
        gatewayIdentifier=gateway_id,
        targetConfiguration={"mcp": {"openApiSchema": {"s3": {"uri": spec_s3_uri}}}},
        credentialProviderConfigurations=[
            {
                "credentialProviderType": "OAUTH",
                "credentialProvider": {
                    "oauthCredentialProvider": {
                        "providerArn": cred_arn,
                        "scopes": ["tickets:read", "read", "tickets:write", "write"],
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
    env_vars["ZENDESK_CREDENTIAL_ARN"] = cred_arn
    env_vars["TARGET_ID"] = target["targetId"]
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    print("\n  Saved to .env")


if __name__ == "__main__":
    main()
