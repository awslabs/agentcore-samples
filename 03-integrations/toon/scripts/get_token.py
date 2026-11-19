#!/usr/bin/env python3
"""Get a bearer token for the MCP Gateway."""

import boto3
import requests
import json
import argparse


def get_cognito_config(environment="dev"):
    """Fetch Cognito client config from Secrets Manager."""
    secrets_client = boto3.client("secretsmanager")
    secret_name = f"toon-{environment}/gateway/client-config"

    response = secrets_client.get_secret_value(SecretId=secret_name)
    return json.loads(response["SecretString"])


def fetch_access_token(client_id, client_secret, token_url):
    """Fetch OAuth access token using client credentials flow."""
    response = requests.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def main():
    parser = argparse.ArgumentParser(description="Get bearer token for MCP Gateway")
    parser.add_argument(
        "--environment", "-e", default="dev", help="Environment (dev, test, prod)"
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Only print the token (no extra output)",
    )
    args = parser.parse_args()

    if not args.quiet:
        print(
            f"Fetching Cognito config for environment: {args.environment}", flush=True
        )

    config = get_cognito_config(args.environment)

    if not args.quiet:
        print("Fetching access token...", flush=True)

    access_token = fetch_access_token(
        config["client_id"],
        config["client_secret"],
        config["token_endpoint"],
    )

    if args.quiet:
        print(access_token)
    else:
        print(f"\nGateway Bearer Token:\n{access_token}")


if __name__ == "__main__":
    main()
