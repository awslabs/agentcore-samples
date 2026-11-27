"""Set up and verify entitlement-based discovery for IAM and JWT callers."""

import argparse
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import boto3
from botocore import UNSIGNED
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

JsonObject = dict[str, Any]
LOGGER = logging.getLogger(__name__)
POLICY_NAME = "RegistryDiscovery"


# IAM scenario
IAM_SCHEMA = {
    "type": "object",
    "properties": {
        "stage": {"type": "string", "enum": ["production", "preview"]},
        "under_review": {"type": "boolean"},
    },
}

IAM_VISIBILITY = {
    "version": "1",
    "statements": [
        {
            "sid": "prod-agents",
            "effect": "Allow",
            "principal": {"iam:principalTag/environment": {"StringEquals": "prod"}},
            "resource": {"customMetadata/stage": {"StringEquals": "production"}},
        },
        {
            "sid": "dev-agents",
            "effect": "Allow",
            "principal": {"iam:principalTag/environment": {"StringEquals": "dev"}},
            "resource": {"customMetadata/stage": {"StringEquals": ["production", "preview"]}},
        },
        {
            "sid": "hide-under-review",
            "effect": "Deny",
            "principal": "*",
            "resource": {"customMetadata/under_review": {"Bool": True}},
        },
    ],
}

IAM_RECORDS = {
    "payments-mcp": {"stage": "production", "under_review": False},
    "search-mcp": {"stage": "production", "under_review": False},
    "code-assistant-mcp": {"stage": "preview", "under_review": False},
    "data-export-mcp": {"stage": "production", "under_review": True},
}

IAM_EXPECTED = {
    "prod": {"payments-mcp", "search-mcp"},
    "dev": {"payments-mcp", "search-mcp", "code-assistant-mcp"},
    "untagged": set(),
}


# JWT scenario
JWT_SCHEMA = {
    "type": "object",
    "properties": {
        "data_classification": {
            "type": "string",
            "enum": ["financial", "PII", "engineering", "public"],
        },
        "compliance": {"type": "string", "enum": ["SOX", "HIPAA", "Both"]},
        "sensitivity": {"type": "string", "enum": ["low", "medium", "high"]},
    },
}


def sensitivity_statement(sid: str, agent_sensitivity: str, record_sensitivities: list[str]) -> JsonObject:
    """Allow a caller to discover records within their sensitivity and access claims."""
    return {
        "sid": sid,
        "effect": "Allow",
        "principal": {"jwt:claim/sensitivity": {"StringEquals": agent_sensitivity}},
        "resource": {
            "customMetadata/data_classification": {"StringEquals": {"ref": "jwt:claim/data_access"}},
            "customMetadata/compliance": {"StringEqualsIfExists": {"ref": "jwt:claim/compliance"}},
            "customMetadata/sensitivity": {"StringEquals": record_sensitivities},
        },
    }


JWT_VISIBILITY = {
    "version": "1",
    "statements": [
        sensitivity_statement("sensitivity-low", "low", ["low"]),
        sensitivity_statement("sensitivity-medium", "medium", ["low", "medium"]),
        sensitivity_statement("sensitivity-high", "high", ["low", "medium", "high"]),
        {
            "sid": "public-tools",
            "effect": "Allow",
            "principal": "*",
            "resource": {"customMetadata/data_classification": {"StringEquals": "public"}},
        },
        {
            "sid": "sox-and-hipaa-tools",
            "effect": "Allow",
            "principal": {
                "jwt:claim/compliance": {"ContainsAll": ["SOX", "HIPAA"]},
                "jwt:claim/sensitivity": {"StringEquals": ["medium", "high"]},
            },
            "resource": {
                "customMetadata/data_classification": {"StringEquals": {"ref": "jwt:claim/data_access"}},
                "customMetadata/compliance": {"StringEquals": "Both"},
                "customMetadata/sensitivity": {"StringEquals": ["low", "medium"]},
            },
        },
    ],
}

JWT_RECORDS = {
    "budget-forecasting-mcp": {
        "data_classification": "financial",
        "compliance": "SOX",
        "sensitivity": "high",
    },
    "payroll-processing-mcp": {
        "data_classification": "PII",
        "compliance": "SOX",
        "sensitivity": "high",
    },
    "benefits-enrollment-mcp": {
        "data_classification": "PII",
        "compliance": "HIPAA",
        "sensitivity": "medium",
    },
    "benefits-payroll-sync-mcp": {
        "data_classification": "PII",
        "compliance": "Both",
        "sensitivity": "medium",
    },
    "deployment-pipeline-mcp": {
        "data_classification": "engineering",
        "sensitivity": "medium",
    },
    "incident-management-mcp": {
        "data_classification": "engineering",
        "sensitivity": "medium",
    },
    "it-helpdesk-mcp": {"data_classification": "public", "sensitivity": "low"},
}

ENGINEERING = {"deployment-pipeline-mcp", "incident-management-mcp", "it-helpdesk-mcp"}
JWT_EXPECTED = {
    "finance": ENGINEERING | {"budget-forecasting-mcp"},
    "engineering": ENGINEERING,
    "hr-case": {"benefits-enrollment-mcp", "it-helpdesk-mcp"},
    "hr-audit": {
        "benefits-enrollment-mcp",
        "benefits-payroll-sync-mcp",
        "it-helpdesk-mcp",
    },
}


def log_event(event: str, **fields: Any) -> None:
    """Emit one structured log entry without credentials or auth headers."""
    LOGGER.info(json.dumps({"event": event, **fields}, default=str))


def wait_for_status(
    read: Callable[[], JsonObject],
    expected: str,
    timeout: float,
    poll_seconds: float = 3,
) -> JsonObject:
    """Wait for an asynchronous registry or record transition with a deadline."""
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        try:
            response = read()
        except ClientError as error:
            if error.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
            response = {"status": "NOT_FOUND"}
        status = response["status"]
        if status == expected:
            return response
        if status.endswith("_FAILED") or status in {"REJECTED", "DEPRECATED"}:
            raise RuntimeError(f"Resource entered {status}: {response.get('statusReason', '')}")
        if status != previous:
            log_event("waiting_for_status", status=status, expected=expected)
            previous = status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for {expected}; last state: {status}")
        time.sleep(min(poll_seconds, remaining))


def save_state(path: Path, state: JsonObject) -> None:
    """Persist resource identifiers atomically so partial setup can be cleaned up."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(path)


def approve(client: BaseClient, curator: BaseClient, request: JsonObject, timeout: float) -> None:
    """Submit the draft as publisher and approve it as curator."""

    def transition(write: Callable[[], JsonObject], status: str) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                write()
                return
            except ClientError as error:
                detail = error.response["Error"]
                remaining = deadline - time.monotonic()
                if (
                    detail["Code"] != "ConflictException"
                    or "concurrent update detected" not in detail.get("Message", "").lower()
                    or remaining <= 0
                ):
                    raise
                if read()["status"] == status:
                    return
                time.sleep(min(3, remaining))

    read = lambda: client.get_registry_record(**request)
    wait_for_status(read, "DRAFT", timeout)
    transition(lambda: client.submit_registry_record_for_approval(**request), "PENDING_APPROVAL")
    wait_for_status(read, "PENDING_APPROVAL", timeout)
    transition(
        lambda: curator.update_registry_record_status(
            **request, status="APPROVED", statusReason="Reviewed sample metadata"
        ),
        "APPROVED",
    )
    wait_for_status(read, "APPROVED", timeout)


def setup(args: argparse.Namespace, client: BaseClient, curator: BaseClient) -> None:
    """Create the selected registry, set its record visibility configuration, and publish the fixtures."""
    if args.state.exists():
        raise ValueError(f"State already exists: {args.state}")
    if args.scenario == "jwt":
        if not args.discovery_url or not args.allowed_client:
            raise ValueError("JWT setup requires --discovery-url and one or more --allowed-client values")
        auth = {
            "authorizerType": "CUSTOM_JWT",
            "authorizerConfiguration": {
                "customJWTAuthorizer": {
                    "discoveryUrl": args.discovery_url,
                    "allowedClients": args.allowed_client,
                }
            },
        }
        schema, visibility, records = JWT_SCHEMA, JWT_VISIBILITY, JWT_RECORDS
    else:
        auth = {"authorizerType": "AWS_IAM"}
        schema, visibility, records = IAM_SCHEMA, IAM_VISIBILITY, IAM_RECORDS
    arn = client.create_registry(
        name=args.name,
        discoveryConfiguration=auth,
        approvalConfiguration={"autoApprovalRules": []},
        customMetadataSchemaConfiguration={"defaultSchema": json.dumps(schema)},
    )["registryArn"]
    state = {
        "registryArn": arn,
        "scenario": args.scenario,
        "records": [],
        "promoted": False,
    }
    save_state(args.state, state)
    read = lambda: client.get_registry(registryId=arn)
    wait_for_status(read, "READY", args.timeout)
    client.update_registry(
        registryId=arn,
        recordVisibilityConfiguration={"put": {"document": json.dumps(visibility), "revision": "NO_CONFIG"}},
    )
    wait_for_status(read, "READY", args.timeout)
    for name, metadata in records.items():
        result = client.create_registry_record(
            registryId=arn,
            name=name,
            recordType="MCP",
            recordVersion="1.0.0",
            customMetadata=metadata,
            descriptors={
                "mcpServer": {
                    "dataSchemaVersion": "2025-12-11",
                    "data": json.dumps(
                        {
                            "name": f"io.example/{name}",
                            "version": "1.0.0",
                            "description": name.replace("-", " "),
                        }
                    ),
                }
            },
        )
        record_id = result["recordArn"].rsplit("/", 1)[-1]
        state["records"].append({"name": name, "recordId": record_id})
        save_state(args.state, state)
        approve(client, curator, {"registryId": arn, "recordId": record_id}, args.timeout)


def promote(
    owner: BaseClient,
    curator: BaseClient,
    state: JsonObject,
    state_file: Path,
    timeout: float,
) -> None:
    """Update and approve a new version of the preview record."""
    record_id = next(record["recordId"] for record in state["records"] if record["name"] == "code-assistant-mcp")
    request = {"registryId": state["registryArn"], "recordId": record_id}
    owner.update_registry_record(
        **request,
        recordVersion="1.1.0",
        customMetadata={"optionalValue": {"stage": "production", "under_review": False}},
    )
    approve(owner, curator, request, timeout)
    state["promoted"] = True
    save_state(state_file, state)


def jwt_client(region: str, token: str) -> BaseClient:
    """Create a Registry discovery client that uses a bearer token instead of SigV4."""
    client = boto3.client(
        "agent-registry",
        region_name=region,
        config=Config(signature_version=UNSIGNED, retries={"mode": "adaptive"}),
    )
    url = urlsplit(client.meta.endpoint_url)
    if url.scheme != "https" or not url.hostname or url.username is not None:
        raise ValueError("JWT discovery requires an HTTPS service endpoint without credentials")

    def authorize(params: JsonObject, **kwargs: object) -> None:
        params["headers"]["Authorization"] = f"Bearer {token}"

    client.meta.events.register("before-call.agent-registry", authorize)
    return client


def matches(results: JsonObject, state: JsonObject, expected: set[str]) -> bool:
    """Require exact List and BatchGet catalogs and a filtered Search subset."""
    hidden = {record["recordId"] for record in state["records"] if record["name"] not in expected}
    errors = results["batchErrors"]
    if any(error["errorCode"] != "RESOURCE_NOT_FOUND" for error in errors):
        raise RuntimeError(f"Unexpected BatchGet errors: {errors}")
    return (
        all({record["name"] for record in results[key]} == expected for key in ("list", "batchGet"))
        and {record["recordId"] for record in errors} == hidden
        and {record["name"] for record in results["search"]} <= expected
    )


def discover(client: BaseClient, state: JsonObject, *, query: str = "", registry_id: str | None = None) -> JsonObject:
    """Read BatchGet, Search and every List page as the authenticated caller."""
    # Full ARNs also work for cross-account SigV4 discovery.
    registry_id = registry_id or state["registryArn"]
    record_ids = [record["recordId"] for record in state["records"]]
    if not record_ids:
        raise ValueError("The state file contains no records; setup was incomplete.")
    batch = client.batch_get_discoverable_registry_record(
        entries=[{"registryId": registry_id, "recordIds": record_ids}]
    )
    search = client.search_discoverable_registry_records(registryIds=[registry_id], searchQuery=query, maxResults=20)
    listed = []
    next_token = None
    while True:
        request: JsonObject = {"registryId": registry_id, "maxResults": 100}
        if next_token:
            request["nextToken"] = next_token
        page = client.list_discoverable_registry_records(**request)
        listed.extend(page["registryRecords"])
        next_token = page.get("nextToken")
        # A short or empty page can still have a cursor.
        if not next_token:
            break
    return {
        "batchGet": batch["registryRecords"],
        "batchErrors": batch["errors"],
        "search": search["registryRecords"],
        "list": listed,
    }


def create_roles(iam: BaseClient, state: JsonObject, state_file: Path, prefix: str, trusted_arn: str) -> None:
    """Create roles with the same discovery permissions and different tags."""
    if state["scenario"] != "iam" or not state["records"]:
        raise ValueError("Set up the IAM registry and publish its records first")
    registry = state["registryArn"]
    discover_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "agent-registry:ListDiscoverableRegistryRecords",
                    "agent-registry:SearchDiscoverableRegistryRecords",
                ],
                "Resource": registry,
            },
            {
                "Effect": "Allow",
                "Action": "agent-registry:GetDiscoverableRegistryRecord",
                "Resource": [f"{registry}/record/{record['recordId']}" for record in state["records"]],
            },
        ],
    }
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "sts:AssumeRole",
                "Principal": {"AWS": trusted_arn},
            }
        ],
    }
    roles = state.setdefault("roles", {})
    for caller in ("prod", "dev", "untagged"):
        if caller not in roles:
            role = iam.create_role(
                RoleName=f"{prefix}-{caller}",
                AssumeRolePolicyDocument=json.dumps(trust),
                Tags=[] if caller == "untagged" else [{"Key": "environment", "Value": caller}],
            )["Role"]
            roles[caller] = {"arn": role["Arn"], "name": role["RoleName"]}
            save_state(state_file, state)
        iam.put_role_policy(
            RoleName=roles[caller]["name"],
            PolicyName=POLICY_NAME,
            PolicyDocument=json.dumps(discover_policy),
        )
    print(json.dumps(roles, indent=2))


def delete_roles(iam: BaseClient, state: JsonObject) -> None:
    """Remove only the IAM roles recorded for this sample run."""
    for role in state.get("roles", {}).values():
        for operation in (
            lambda role=role: iam.delete_role_policy(RoleName=role["name"], PolicyName=POLICY_NAME),
            lambda role=role: iam.delete_role(RoleName=role["name"]),
        ):
            try:
                operation()
            except ClientError as error:
                if error.response["Error"]["Code"] != "NoSuchEntity":
                    raise


def cleanup(client: BaseClient, state: JsonObject, timeout: float) -> None:
    """Delete the sample's recorded resources and wait for the registry to be removed."""
    registry_arn = state["registryArn"]
    for record in state["records"]:
        try:
            client.delete_registry_record(registryId=registry_arn, recordId=record["recordId"])
        except ClientError as error:
            if error.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
    try:
        client.delete_registry(registryId=registry_arn)
    except ClientError as error:
        if error.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    else:
        wait_for_status(lambda: client.get_registry(registryId=registry_arn), "NOT_FOUND", timeout)
    log_event("registry_deleted", registryArn=registry_arn)


def main() -> None:
    """Parse commands and run one scenario against the regional AWS endpoints."""
    logging.basicConfig(format="%(message)s")
    LOGGER.setLevel(logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("setup", "promote", "cleanup"):
        sub = commands.add_parser(command)
        sub.add_argument("--owner-profile", required=True)
        if command != "cleanup":
            sub.add_argument("--curator-profile")
        if command == "setup":
            sub.add_argument("--scenario", choices=("iam", "jwt"), required=True)
            sub.add_argument("--name", required=True)
            sub.add_argument("--discovery-url")
            sub.add_argument("--allowed-client", action="append")
    roles = commands.add_parser("create-roles")
    roles.add_argument("--owner-profile", required=True)
    roles.add_argument("--prefix", required=True)
    roles.add_argument("--trusted-arn", required=True)
    check = commands.add_parser("verify")
    check.add_argument("--caller", required=True, choices=tuple(IAM_EXPECTED) + tuple(JWT_EXPECTED))
    check.add_argument("--profile")
    check.add_argument("--token-env", default="EBD_ACCESS_TOKEN")
    check.add_argument("--query", help="Semantic search query to verify")
    args = parser.parse_args()

    def sdk(service: str, profile: str | None = None) -> BaseClient:
        return boto3.Session(profile_name=profile).client(
            service,
            region_name=args.region,
            config=Config(retries={"mode": "adaptive"}),
        )

    if args.command == "setup":
        owner = sdk("agent-registry-control", args.owner_profile)
        curator = sdk("agent-registry-control", args.curator_profile) if args.curator_profile else owner
        setup(args, owner, curator)
        return
    state = json.loads(args.state.read_text())
    if args.command == "create-roles":
        iam = boto3.Session(profile_name=args.owner_profile).client("iam", region_name=args.region)
        create_roles(iam, state, args.state, args.prefix, args.trusted_arn)
        return
    if args.command == "verify":
        if state["scenario"] == "iam":
            if not args.profile or args.caller not in IAM_EXPECTED:
                parser.error("IAM verification requires an IAM caller and --profile")
            client = sdk("agent-registry", args.profile)
            expected = IAM_EXPECTED[args.caller].copy()
            if args.caller == "prod" and state["promoted"]:
                expected.add("code-assistant-mcp")
            query = args.query or "payments and search"
            registry_id = state["registryArn"]
        else:
            if args.caller not in JWT_EXPECTED or not os.environ.get(args.token_env):
                parser.error("JWT verification requires a JWT caller and an access token in --token-env")
            expected = JWT_EXPECTED[args.caller]
            client = jwt_client(args.region, os.environ[args.token_env])
            query = (
                args.query
                or {
                    "finance": "budget forecasting",
                    "engineering": "deployment pipeline",
                    "hr-case": "benefits enrollment",
                    "hr-audit": "benefits and payroll sync",
                }[args.caller]
            )
            registry_id = state["registryArn"].rsplit("/", 1)[-1]
        deadline = time.monotonic() + args.timeout
        while True:
            results = discover(client, state, query=query, registry_id=registry_id)
            if matches(results, state, expected) and (not expected or args.query or results["search"]):
                print(
                    json.dumps(
                        {
                            key: sorted(record["name"] for record in results[key])
                            for key in ("list", "batchGet", "search")
                        },
                        indent=2,
                    )
                )
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Expected catalog {sorted(expected)}; received {results}")
            time.sleep(3)
    owner = sdk("agent-registry-control", args.owner_profile)
    if args.command == "cleanup":
        cleanup(owner, state, args.timeout)
        if state.get("roles"):
            iam = boto3.Session(profile_name=args.owner_profile).client("iam", region_name=args.region)
            delete_roles(iam, state)
        return
    if state["scenario"] != "iam" or state["promoted"]:
        parser.error("Promotion requires an IAM registry with an unpromoted preview record")
    curator = sdk("agent-registry-control", args.curator_profile) if args.curator_profile else owner
    promote(owner, curator, state, args.state, args.timeout)


if __name__ == "__main__":
    main()
