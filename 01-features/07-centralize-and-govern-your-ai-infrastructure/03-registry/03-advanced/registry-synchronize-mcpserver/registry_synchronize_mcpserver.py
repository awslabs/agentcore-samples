"""Synchronize one MCP descriptor into an existing AWS Agent Registry; never deploy its source."""

import argparse
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote

import boto3
from botocore.exceptions import BotoCoreError, ClientError

RECEIPT = Path("registry-sync-receipt.json")
PUBLIC_URL = "https://knowledge-mcp.global.api.aws"


def source(mode, region, account, runtime_arn=None, role_arn=None, provider_arn=None, scopes=()):
    if mode == "public" and not any((runtime_arn, role_arn, provider_arn, scopes)):
        return {"url": PUBLIC_URL}
    if not re.fullmatch(r"[a-z]{2}-[a-z0-9-]+-\d+", region):
        raise ValueError("Use a supported commercial AWS Region.")
    prefix = f"arn:aws:bedrock-agentcore:{region}:{account}:"
    if not re.fullmatch(re.escape(prefix) + r"runtime/[A-Za-z][A-Za-z0-9_]{0,47}-[A-Za-z0-9]{10}", runtime_arn or ""):
        raise ValueError("Specify an existing Runtime ARN in this account and Region.")
    url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{quote(runtime_arn, safe='')}/invocations?qualifier=DEFAULT"
    if mode == "iam" and role_arn and not provider_arn and not scopes:
        if not re.fullmatch(f"arn:aws:iam::{account}:role/" + r"[\w+=,.@/-]+", role_arn):
            raise ValueError("Use an approved Registry invocation role from this account.")
        config = {
            "credentialProviderType": "IAM",
            "credentialProvider": {
                "iamCredentialProvider": {"roleArn": role_arn, "service": "bedrock-agentcore", "region": region}
            },
        }
    elif mode == "oauth" and provider_arn and scopes and not role_arn:
        if not re.fullmatch(re.escape(prefix) + r"token-vault/[\w.-]+/oauth2credentialprovider/[\w.-]+", provider_arn):
            raise ValueError("Use an approved Identity OAuth provider from this account and Region.")
        config = {
            "credentialProviderType": "OAUTH",
            "credentialProvider": {
                "oauthCredentialProvider": {
                    "providerArn": provider_arn,
                    "grantType": "CLIENT_CREDENTIALS",
                    "scopes": list(scopes),
                }
            },
        }
    else:
        raise ValueError("Choose public without credentials, iam with a role, or oauth with a provider and scope.")
    return {"url": url, "credentialProviderConfigurations": [config]}


@contextmanager
def exclusive():
    lock = RECEIPT.with_suffix(".lock")
    lock.touch(exist_ok=False)
    try:
        yield
    finally:
        lock.unlink()


def save(state):
    with NamedTemporaryFile("w", dir=RECEIPT.parent, delete=False) as f:
        json.dump(state, f, indent=2)
    os.replace(f.name, RECEIPT)


def create(client, registry_id, account, region, from_url=None, *, inline=None):
    if (from_url is None) == (inline is None):
        raise ValueError("Choose exactly one descriptor source")
    if inline:
        kind, key, data = inline
        if (kind, key) not in {("MCP", "mcpServer"), ("AGENT", "a2aAgentCard")} or not isinstance(data, dict):
            raise ValueError("Use an MCP server descriptor or A2A agent card JSON object")
        descriptors = {key: {"data": json.dumps(data)}}
    else:
        kind = "MCP"
        descriptors = {"mcpServer": {"source": {"fromUrl": from_url}}}
    registry = client.get_registry(registryId=registry_id)
    registry_arn = f"arn:aws:agent-registry:{region}:{account}:registry/{registry_id}"
    if registry["registryArn"] != registry_arn or registry["status"] != "READY":
        raise ValueError("Wrong registry, account, Region, or readiness state.")
    if registry.get("approvalConfiguration", {}).get("autoApprovalRules"):
        raise ValueError("Use a sandbox registry with no automatic approval rules.")
    name = "sync-demo-" + uuid.uuid4().hex[:16]
    state = {
        "record_type": kind,
        "registry_id": registry_id,
        "registry_arn": registry_arn,
        "account": account,
        "region": region,
        "name": name,
        "client_token": str(uuid.uuid4()),
    }
    # Exclusive reservation: a failed/interrupted create may still have reached AWS.
    with RECEIPT.open("x") as f:
        json.dump(state, f, indent=2)
    result = client.create_registry_record(
        registryId=registry_id,
        name=name,
        recordType=kind,
        descriptors=descriptors,
        clientToken=state["client_token"],
    )
    arn = result["recordArn"]
    if not re.fullmatch(re.escape(registry_arn) + r"/record/[A-Za-z0-9]{12}", arn):
        raise RuntimeError("Unexpected record ARN. Reconcile the create manually.")
    state.update(record_id=arn.rsplit("/", 1)[1], record_arn=arn)
    save(state)
    return state


def get_owned(client, state):
    if not state.get("record_id"):
        raise RuntimeError("No confirmed record ID. Reconcile the create manually; never adopt by name.")
    record = client.get_registry_record(registryId=state["registry_id"], recordId=state["record_id"])
    if (record["registryArn"], record["recordArn"], record["recordType"]) != (
        state["registry_arn"],
        state["record_arn"],
        state.get("record_type", "MCP"),
    ):
        raise RuntimeError("Record receipt mismatch; nothing changed.")
    return record


def describe(client, state):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        record = get_owned(client, state)
        status = record["status"]
        if status == "DRAFT":
            key = "a2aAgentCard" if record["recordType"] == "AGENT" else "mcpServer"
            descriptor = record.get("descriptors", {}).get(key, {}).get("data")
            if not descriptor:
                raise RuntimeError("Draft has no descriptor data. Check the source and authorization.")
            print(f"DRAFT: {state['record_arn']} ({len(descriptor.encode())} bytes of descriptor metadata)")
            return
        if status not in {"CREATING", "UPDATING"}:
            raise RuntimeError(f"Record reached {status}, not DRAFT. Check the record before continuing.")
        time.sleep(3)
    raise TimeoutError("Synchronization not confirmed. Keep the receipt and retry status or cleanup.")


def cleanup(client, state):
    try:
        record = get_owned(client, state)
        if record["status"] not in {"DRAFT", "CREATE_FAILED"}:
            raise RuntimeError(
                "Not deleting a pending or promoted record. Review its lifecycle with the registry owner."
            )
        client.delete_registry_record(registryId=state["registry_id"], recordId=state["record_id"])
        for _ in range(40):
            get_owned(client, state)
            time.sleep(3)
    except client.exceptions.ResourceNotFoundException:
        RECEIPT.unlink()
        print("Recorded entry is absent; the registry, Runtime and auth resources were left unchanged.")
        return
    raise TimeoutError("Record deletion not confirmed; keep the receipt.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="create and inspect one DRAFT record")
    sync.add_argument("--registry-id", required=True)
    sync.add_argument("--mode", choices=("public", "iam", "oauth"), default="public")
    sync.add_argument("--runtime-arn")
    sync.add_argument("--role-arn")
    sync.add_argument("--provider-arn")
    sync.add_argument("--scope", action="append", default=[])
    publish = commands.add_parser(
        "draft", help="submit one operator-reviewed inline descriptor, without fetching its URL"
    )
    publish.add_argument("--registry-id", required=True)
    publish.add_argument("--kind", choices=("mcp", "a2a"), required=True)
    publish.add_argument("--descriptor", type=Path, required=True)
    commands.add_parser("status")
    commands.add_parser("cleanup")
    args = parser.parse_args()
    with exclusive():
        state = {} if args.command in {"sync", "draft"} else json.loads(RECEIPT.read_text())
        session = boto3.Session(region_name=state.get("region"))
        region = session.region_name
        if not region:
            raise ValueError("Select the registry Region in your AWS profile or AWS_DEFAULT_REGION.")
        account = session.client("sts").get_caller_identity()["Account"]
        if state and account != state["account"]:
            raise ValueError("Wrong account; nothing changed.")
        client = session.client("agent-registry-control")
        if args.command == "cleanup":
            cleanup(client, state)
            return
        if args.command == "sync":
            from_url = source(
                args.mode, region, account, args.runtime_arn, args.role_arn, args.provider_arn, args.scope
            )
            state = create(client, args.registry_id, account, region, from_url)
        elif args.command == "draft":
            kind, key = ("MCP", "mcpServer") if args.kind == "mcp" else ("AGENT", "a2aAgentCard")
            data = json.loads(args.descriptor.read_text(encoding="utf-8"))
            state = create(client, args.registry_id, account, region, inline=(kind, key, data))
        describe(client, state)


if __name__ == "__main__":
    try:
        main()
    except (BotoCoreError, ClientError) as exc:
        # Provider failures can carry request and descriptor details.
        raise SystemExit(
            f"Registry request failed ({type(exc).__name__}). Check status, permissions and your receipt."
        ) from None
