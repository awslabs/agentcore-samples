"""Create an SDK-owned private Gateway target; never fall back to public egress.

Managed mode requires --vpc-id and --subnet-ids. Self-managed mode requires
--resource-configuration-identifier (an rcfg-* ID or Resource Configuration ARN,
NOT a Resource Gateway ARN). Resource Configurations remain SDK-owned.
"""

import argparse
import json
import os
import re
import uuid
from urllib.parse import urlsplit

import boto3
from lifecycle import (
    account_for,
    load,
    record_path,
    reserve,
    save,
    verified_session,
    wait_target,
)


def csv(value):
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(not part for part in parts):
        raise ValueError("Comma-separated lists must not contain empty entries")
    return parts


def build_request(args, gateway_id):
    """Pure validation/construction, deliberately before Session or client creation."""
    managed_requested = any(
        value is not None
        for value in (
            args.vpc_id,
            args.subnet_ids,
            args.security_group_ids,
            args.routing_domain,
        )
    )
    if managed_requested and args.resource_configuration_identifier is not None:
        raise ValueError("Choose managed VPC OR self-managed Lattice, not both")
    if managed_requested:
        if not args.vpc_id or not args.subnet_ids:
            raise ValueError("Managed VPC requires --vpc-id and --subnet-ids")
        if not re.fullmatch(r"vpc-[0-9a-f]+", args.vpc_id):
            raise ValueError("Invalid VPC ID")
        subnets = csv(args.subnet_ids)
        if any(not re.fullmatch(r"subnet-[0-9a-f]+", item) for item in subnets):
            raise ValueError("Invalid subnet ID")
        managed = {
            "vpcIdentifier": args.vpc_id,
            "subnetIds": subnets,
            "endpointIpAddressType": "IPV4",
        }
        if args.security_group_ids is not None:
            groups = csv(args.security_group_ids)
            if any(not re.fullmatch(r"sg-[0-9a-f]+", item) for item in groups):
                raise ValueError("Invalid security group ID")
            managed["securityGroupIds"] = groups
        if args.routing_domain is not None:
            if not re.fullmatch(r"[A-Za-z0-9.-]+", args.routing_domain):
                raise ValueError("--routing-domain must be a DNS name, not a URL")
            managed["routingDomain"] = args.routing_domain
        private = {"managedVpcResource": managed}
    else:
        identifier = args.resource_configuration_identifier or ""
        if not re.fullmatch(
            r"(?:arn:[^:]+:vpc-lattice:[^:]+:\d{12}:resourceconfiguration/)?rcfg-[0-9a-z]{17}",
            identifier,
        ):
            raise ValueError(
                "Provide a Resource Configuration ARN/rcfg-* ID or complete managed VPC inputs"
            )
        private = {
            "selfManagedLatticeResource": {
                "resourceConfigurationIdentifier": identifier
            }
        }

    if args.lambda_arn:
        if (
            args.endpoint
            or args.openapi_schema_file
            or args.listing_mode
            or not args.tool_schema_file
        ):
            raise ValueError(
                "Lambda requires --tool-schema-file and cannot use endpoint/OpenAPI/listing mode"
            )
        tools = json.loads(args.tool_schema_file.read_text())
        if not isinstance(tools, list) or not tools:
            raise ValueError("Lambda tool schema must be a nonempty JSON array")
        target = {
            "mcp": {
                "lambda": {
                    "lambdaArn": args.lambda_arn,
                    "toolSchema": {"inlinePayload": tools},
                }
            }
        }
    else:
        url = urlsplit(args.endpoint or "")
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
        ):
            raise ValueError(
                "--endpoint must be an HTTPS URL without credentials or fragment"
            )
        if args.tool_schema_file:
            raise ValueError("--tool-schema-file requires --lambda-arn")
        if args.openapi_schema_file:
            if args.listing_mode:
                raise ValueError("--listing-mode is only supported for MCP servers")
            schema = json.loads(args.openapi_schema_file.read_text())
            if not schema.get("openapi") or not schema.get("paths"):
                raise ValueError("Expected an OpenAPI document with paths")
            schema["servers"] = [{"url": args.endpoint}]
            target = {"mcp": {"openApiSchema": {"inlinePayload": json.dumps(schema)}}}
        else:
            server = {"endpoint": args.endpoint}
            if args.listing_mode:
                server["listingMode"] = args.listing_mode
            target = {"mcp": {"mcpServer": server}}

    credentials = []
    if args.credential_provider_arn:
        if args.lambda_arn:
            raise ValueError(
                "Lambda targets use GATEWAY_IAM_ROLE, not an outbound provider"
            )
        if "/apikeycredentialprovider/" in args.credential_provider_arn:
            if not args.openapi_schema_file or args.scopes:
                raise ValueError(
                    "API key credentials require OpenAPI and cannot use OAuth scopes"
                )
            credentials = [
                {
                    "credentialProviderType": "API_KEY",
                    "credentialProvider": {
                        "apiKeyCredentialProvider": {
                            "providerArn": args.credential_provider_arn,
                            "credentialParameterName": args.header_name,
                            "credentialLocation": "HEADER",
                        }
                    },
                }
            ]
        elif "/oauth2credentialprovider/" in args.credential_provider_arn:
            credentials = [
                {
                    "credentialProviderType": "OAUTH",
                    "credentialProvider": {
                        "oauthCredentialProvider": {
                            "providerArn": args.credential_provider_arn,
                            "scopes": csv(args.scopes) if args.scopes else [],
                        }
                    },
                }
            ]
        else:
            raise ValueError("Expected an API key or OAuth2 credential provider ARN")
    elif args.scopes:
        raise ValueError("--scopes requires an OAuth2 credential provider")
    elif args.lambda_arn:
        credentials = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
    request = {
        "gatewayIdentifier": gateway_id,
        "name": args.name,
        "targetConfiguration": target,
        "privateEndpoint": private,
    }
    if credentials:
        request["credentialProviderConfigurations"] = credentials
    return request


def main(argv=None):
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Retry the recorded request/token or wait for its recorded target",
    )
    parser.add_argument("--endpoint")
    parser.add_argument("--lambda-arn")
    parser.add_argument("--tool-schema-file", type=Path)
    parser.add_argument("--openapi-schema-file", type=Path)
    parser.add_argument("--vpc-id")
    parser.add_argument("--subnet-ids")
    parser.add_argument("--security-group-ids")
    parser.add_argument("--routing-domain")
    parser.add_argument("--resource-configuration-identifier")
    parser.add_argument("--credential-provider-arn")
    parser.add_argument("--scopes")
    parser.add_argument("--header-name", default="x-api-key")
    parser.add_argument("--listing-mode", choices=["DYNAMIC", "DEFAULT"])
    args = parser.parse_args(argv)
    try:
        gateway_id = os.environ.get("GATEWAY_ID")
        if not gateway_id:
            raise ValueError("Export GATEWAY_ID first")
        path = record_path("target", args.name)
        if args.resume:
            if any(
                getattr(args, key) is not None
                for key in (
                    "endpoint",
                    "lambda_arn",
                    "tool_schema_file",
                    "openapi_schema_file",
                    "vpc_id",
                    "subnet_ids",
                    "security_group_ids",
                    "routing_domain",
                    "resource_configuration_identifier",
                    "credential_provider_arn",
                    "scopes",
                    "listing_mode",
                )
            ):
                raise ValueError(
                    "--resume uses the recorded configuration; do not supply new target options"
                )
            record = load(path)
            if record["gatewayId"] != gateway_id or record["status"] == "DELETED":
                raise ValueError(
                    "Cannot resume a deleted target or a different gateway"
                )
            request = record["request"]
        else:
            request = build_request(args, gateway_id)
            if path.exists():
                raise ValueError(
                    f"Ownership record already exists: {path}; use --resume or clean up first"
                )
    except (ValueError, OSError) as error:
        parser.error(str(error))

    session = verified_session(record) if args.resume else boto3.Session()
    control = session.client("bedrock-agentcore-control")
    # Reuse the persisted token after a lost response; never search/adopt by name.
    if not args.resume:
        request["clientToken"] = str(uuid.uuid4())
        record = {
            "name": args.name,
            "gatewayId": gateway_id,
            "region": session.region_name,
            "accountId": account_for(session),
            "status": "CREATE_PENDING",
            "request": request,
        }
        reserve(path, record)
    try:
        if not record.get("targetId"):
            response = control.create_gateway_target(**request)
            record.update(targetId=response["targetId"], status=response["status"])
            save(path, record)
        print(f"Target ID: {record['targetId']}; recovery record: {path}", flush=True)
        wait_target(control, record, path)
    except Exception:
        print(
            f"Creation did not complete. Keep {path}; do not reuse a conflicting target.",
            flush=True,
        )
        raise
    print(f"Target {args.name} is READY")


if __name__ == "__main__":
    main()
