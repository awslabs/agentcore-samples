"""Owned Atlassian USER_FEDERATION provider lifecycle; never adopt a collision.

GetOauth2CredentialProvider has no tags field. A retained DynamoDB record holds
proof from a successful Create (ARN + createdTime), not an inferred name match.
An interrupted Create without that proof is intentionally NOT auto-adopted.
No request, response, client secret, or consent URL is logged.
"""

import hashlib
import json
import os
import time
from datetime import datetime

import boto3
from botocore.config import Config

SDK_CONFIG = Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 2})


def _created(value):
    return str(value.timestamp() if isinstance(value, datetime) else float(value))


def _provider(control, name):
    try:
        return control.get_oauth2_credential_provider(name=name)
    except control.exceptions.ResourceNotFoundException:
        return None


def _ready(control, name, context, expected):
    for attempt in range(10):
        if context and context.get_remaining_time_in_millis() < 20000:
            break
        current = _provider(control, name)
        if current and (
            current["credentialProviderArn"] != expected["credentialProviderArn"]
            or _created(current["createdTime"]) != _created(expected["createdTime"])
        ):
            raise RuntimeError("Provider identity changed while awaiting readiness")
        if current and current.get("status") == "READY":
            return current
        if current and current.get("status", "").endswith("FAILED"):
            raise RuntimeError("Provider reports a failed lifecycle operation; retain evidence")
        if attempt == 9 or (context and context.get_remaining_time_in_millis() < 20000):
            break
        time.sleep(2)
    raise TimeoutError("Provider readiness not observed; retain ownership record")


def _verify(current, record, name):
    if (
        not current
        or record.get("Status", {}).get("S") != "CREATED"
        or current["name"] != name
        or current["credentialProviderArn"] != record["ProviderArn"]["S"]
        or _created(current["createdTime"]) != record["CreatedTime"]["S"]
        or current["credentialProviderVendor"] != "AtlassianOauth2"
    ):
        raise RuntimeError("Provider ownership unproven; retain ledger and investigate, do not adopt")


def handler(event, context):
    request = event["RequestType"]
    if request not in ("Create", "Update", "Delete"):
        raise ValueError("Unsupported lifecycle request")
    props = event["ResourceProperties"]
    # Stack UUID prevents different fresh stacks from claiming the same name.
    owner = hashlib.sha256(f"{event['StackId']}/{event['LogicalResourceId']}".encode()).hexdigest()
    name = f"{props['ProviderName']}-{owner[:24]}"
    physical_id = event.get("PhysicalResourceId")
    if physical_id and physical_id != name:
        raise ValueError("Provider name/ownership cannot be changed in place")
    if request == "Update":
        old = event["OldResourceProperties"]
        if old["ProviderName"] != props["ProviderName"] or old["ClientId"] != props["ClientId"]:
            raise ValueError("Client/provider identity is immutable; only owned secret rotation is supported")
    control = boto3.client("bedrock-agentcore-control", config=SDK_CONFIG)
    ddb = boto3.client("dynamodb", config=SDK_CONFIG)
    table = os.environ["OWNERSHIP_TABLE"]
    key = {"owner": {"S": owner}}
    record = ddb.get_item(TableName=table, Key=key, ConsistentRead=True).get("Item", {})
    current = _provider(control, name)

    if request == "Delete":
        if current:
            _verify(current, record, name)
            # Only not-found is successful absence; denial/timeouts propagate.
            try:
                control.delete_oauth2_credential_provider(name=name)
            except control.exceptions.ResourceNotFoundException:
                pass
            if _provider(control, name) is not None:
                raise RuntimeError("Provider deletion not yet confirmed; retry cleanup")
        if record:
            ddb.update_item(
                TableName=table,
                Key=key,
                UpdateExpression="SET #s = :s",
                ExpressionAttributeNames={"#s": "Status"},
                ExpressionAttributeValues={":s": {"S": "DELETED"}},
            )
        return {"PhysicalResourceId": name}

    if current:
        _verify(current, record, name)
        if request == "Create":
            # Verified delivery retry, not Update-on-conflict. No secret read needed.
            return _result(name, _ready(control, name, context, current))
    elif record:
        raise RuntimeError("Interrupted or missing provider; ownership is unproven, retain ledger")
    elif request != "Create":
        raise RuntimeError("Cannot update an absent provider")
    else:
        # Reserve before mutation, conditionally. A racing/ambiguous attempt must fail.
        ddb.put_item(
            TableName=table,
            Item={**key, "Status": {"S": "CREATING"}, "StackId": {"S": event["StackId"]}, "ProviderName": {"S": name}},
            ConditionExpression="attribute_not_exists(#o)",
            ExpressionAttributeNames={"#o": "owner"},
        )

    secret = boto3.client("secretsmanager", config=SDK_CONFIG).get_secret_value(
        SecretId=props["SecretArn"], VersionId=props["SecretVersionId"]
    )
    if secret.get("VersionId") != props["SecretVersionId"]:
        raise RuntimeError("Secret version was not the requested version")
    client_secret = json.loads(secret["SecretString"])["client_secret"]
    if not isinstance(client_secret, str) or not client_secret:
        raise ValueError("Secret must contain a nonempty client_secret string")
    args = {
        "name": name,
        "credentialProviderVendor": "AtlassianOauth2",
        "oauth2ProviderConfigInput": {
            "atlassianOauth2ProviderConfig": {"clientId": props["ClientId"], "clientSecret": client_secret}
        },
    }
    # Conflict is deliberately not caught. Neither API has an idempotency token.
    if request == "Create":
        response = control.create_oauth2_credential_provider(**args)
        current = _provider(control, name)
        if not current or current["credentialProviderArn"] != response["credentialProviderArn"]:
            raise RuntimeError("Created provider not observable; retain CREATING record")
        ddb.update_item(
            TableName=table,
            Key=key,
            UpdateExpression="SET #s = :s, ProviderArn = :a, CreatedTime = :t, SecretVersionId = :v",
            ConditionExpression="#s = :creating",
            ExpressionAttributeNames={"#s": "Status"},
            ExpressionAttributeValues={
                ":s": {"S": "CREATED"},
                ":creating": {"S": "CREATING"},
                ":a": {"S": current["credentialProviderArn"]},
                ":t": {"S": _created(current["createdTime"])},
                ":v": {"S": props["SecretVersionId"]},
            },
        )
    else:
        response = control.update_oauth2_credential_provider(**args)
        if response["credentialProviderArn"] != record["ProviderArn"]["S"]:
            raise RuntimeError("Updated provider ARN mismatch")
        ddb.update_item(
            TableName=table,
            Key=key,
            UpdateExpression="SET SecretVersionId = :v",
            ExpressionAttributeValues={":v": {"S": props["SecretVersionId"]}},
        )
    return _result(name, _ready(control, name, context, current))


def _result(name, response):
    if not response.get("callbackUrl"):
        raise RuntimeError("Provider callback URL missing; do not report setup complete")
    return {
        "PhysicalResourceId": name,
        "Data": {
            "ProviderName": name,
            "ProviderArn": response["credentialProviderArn"],
            "CallbackUrl": response["callbackUrl"],
            "ClientSecretArn": response["clientSecretArn"]["secretArn"],
        },
    }
