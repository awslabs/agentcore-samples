#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Create/update/delete the OAuth2 credential provider with a customer-owned secret.

Terraform's aws_bedrockagentcore_oauth2_credential_provider (provider 6.58) only
exposes an inline `client_secret` for the CustomOauth2 vendor — it cannot express
`clientSecretSource=EXTERNAL` (bring-your-own Secrets Manager secret). So this
script provisions the provider through the raw API instead, referencing a Secrets
Manager secret WE own. Terraform runs it from a null_resource, the same pattern as
the Registry record and the Gateway inference target.

Flow (non-delete):
  1. Read the Cognito M2M client secret (describe_user_pool_client).
  2. Write it as JSON into our own Secrets Manager secret ({<jsonKey>: <secret>}).
  3. Create (or update) the credential provider with clientSecretSource=EXTERNAL
     pointing at that secret. AgentCore then reads OUR secret at token time.

This keeps the client secret out of Terraform state entirely (the only remaining
copy is the one Cognito records on its own client resource, which is unavoidable
for a Cognito-generated secret), and gives us a secret we can rotate.
"""

import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError


def log(message: str) -> None:
    print(f"[oauth-provider] {message}", flush=True)


def provider_exists(cp, name: str) -> bool:
    try:
        cp.get_oauth2_credential_provider(name=name)
        return True
    except cp.exceptions.ResourceNotFoundException:
        return False


def config_input(discovery_url: str, client_id: str, secret_arn: str, json_key: str) -> dict:
    return {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {"discoveryUrl": discovery_url},
            "clientId": client_id,
            "clientSecretSource": "EXTERNAL",
            "clientSecretConfig": {"secretId": secret_arn, "jsonKey": json_key},
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--user-pool-id")
    parser.add_argument("--client-id")
    parser.add_argument("--secret-arn")
    parser.add_argument("--json-key", default="clientSecret")
    parser.add_argument("--discovery-url")
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args()

    cp = boto3.client("bedrock-agentcore-control", region_name=args.region)

    try:
        if args.delete:
            try:
                cp.delete_oauth2_credential_provider(name=args.name)
                log(f"{args.name}: deleted")
            except cp.exceptions.ResourceNotFoundException:
                log(f"{args.name}: not present, nothing to delete")
            return 0

        for required in ("user_pool_id", "client_id", "secret_arn", "discovery_url"):
            if not getattr(args, required):
                parser.error(f"--{required.replace('_', '-')} is required to create/update")

        # 1. Read the Cognito-generated client secret.
        idp = boto3.client("cognito-idp", region_name=args.region)
        client = idp.describe_user_pool_client(
            UserPoolId=args.user_pool_id, ClientId=args.client_id
        )["UserPoolClient"]
        client_secret = client["ClientSecret"]

        # 2. Store it in OUR Secrets Manager secret as JSON.
        sm = boto3.client("secretsmanager", region_name=args.region)
        sm.put_secret_value(
            SecretId=args.secret_arn,
            SecretString=json.dumps({args.json_key: client_secret}),
        )
        log(f"wrote client secret into {args.secret_arn} (key '{args.json_key}')")

        # 3. Create or update the EXTERNAL-secret provider.
        cfg = config_input(args.discovery_url, args.client_id, args.secret_arn, args.json_key)
        if provider_exists(cp, args.name):
            cp.update_oauth2_credential_provider(
                name=args.name,
                credentialProviderVendor="CustomOauth2",
                oauth2ProviderConfigInput=cfg,
            )
            log(f"{args.name}: updated (EXTERNAL secret)")
        else:
            cp.create_oauth2_credential_provider(
                name=args.name,
                credentialProviderVendor="CustomOauth2",
                oauth2ProviderConfigInput=cfg,
            )
            log(f"{args.name}: created (EXTERNAL secret)")
        return 0
    except ClientError as exc:
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
