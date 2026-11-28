"""Create an owned outbound provider, without reusing a same-name provider.

Prefer API_KEY_VALUE / OAUTH_CLIENT_SECRET environment variables to secret
command-line arguments. --header-name is used when creating the TARGET, not by
Identity; it is retained here as a reminder and recorded for the operator.
"""

import argparse
import os

import boto3
from credential_generation import created_time, secret_arn, verify_generation
from lifecycle import account_for, record_path, reserve, save


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--type", required=True, choices=["oauth", "api-key"])
    parser.add_argument("--discovery-url")
    parser.add_argument("--client-id")
    parser.add_argument(
        "--client-secret", default=os.environ.get("OAUTH_CLIENT_SECRET")
    )
    parser.add_argument("--api-key-value", default=os.environ.get("API_KEY_VALUE"))
    parser.add_argument("--header-name", default="x-api-key")
    args = parser.parse_args(argv)
    try:
        path = record_path("credential", args.name)
        if path.exists():
            raise ValueError(
                f"Ownership record exists: {path}; reconcile before creating"
            )
        if args.type == "oauth" and not all(
            [args.discovery_url, args.client_id, args.client_secret]
        ):
            raise ValueError(
                "OAuth requires discovery URL, client ID and client secret"
            )
        if args.type == "api-key" and not args.api_key_value:
            raise ValueError("Set API_KEY_VALUE or --api-key-value")
    except ValueError as error:
        parser.error(str(error))
    session = boto3.Session()
    control = session.client("bedrock-agentcore-control")
    record = {
        "name": args.name,
        "type": args.type,
        "region": session.region_name,
        "accountId": account_for(session),
        "headerName": args.header_name,
        "status": "CREATE_PENDING",
    }
    reserve(path, record)
    if args.type == "oauth":
        response = control.create_oauth2_credential_provider(
            name=args.name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "oauthDiscovery": {"discoveryUrl": args.discovery_url},
                    "clientId": args.client_id,
                    "clientSecret": args.client_secret,
                }
            },
        )
    else:
        response = control.create_api_key_credential_provider(
            name=args.name, apiKey=args.api_key_value
        )
    record.update(arn=response["credentialProviderArn"], status="CREATE_CONFIRMING")
    save(path, record)
    record["secretArn"] = secret_arn(response, args.type)
    # Save returned identifiers before the follow-up read; never save secret values.
    save(path, record)
    kind = "oauth2" if args.type == "oauth" else "api_key"
    detail = getattr(control, f"get_{kind}_credential_provider")(name=args.name)
    verify_generation(record, detail, require_time=False)
    record.update(createdTime=created_time(detail), status="CREATED")
    save(path, record)
    print(f"Created {record['arn']}; ownership record: {path}")


if __name__ == "__main__":
    main()
