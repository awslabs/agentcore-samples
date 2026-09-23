import boto3
import json


def lambda_handler(event, context):
    arn = event["SecretId"]
    token = event["ClientRequestToken"]
    step = event["Step"]

    service_client = boto3.client("secretsmanager")

    metadata = service_client.describe_secret(SecretId=arn)
    if not metadata["RotationEnabled"]:
        raise ValueError("Secret is not enabled for rotation")

    versions = metadata["VersionIdsToStages"]
    if token not in versions:
        raise ValueError("Secret version has no stage for rotation")

    if "AWSCURRENT" in versions[token]:
        return
    elif "AWSPENDING" not in versions[token]:
        raise ValueError("Secret version not set as AWSPENDING for rotation")

    if step == "createSecret":
        create_secret(service_client, arn, token)
    elif step == "setSecret":
        set_secret(service_client, arn, token)
    elif step == "testSecret":
        test_secret(service_client, arn, token)
    elif step == "finishSecret":
        finish_secret(service_client, arn, token)
    else:
        raise ValueError("Invalid step parameter")


def create_secret(service_client, arn, token):
    service_client.get_secret_value(SecretId=arn, VersionStage="AWSCURRENT")

    try:
        service_client.get_secret_value(SecretId=arn, VersionId=token, VersionStage="AWSPENDING")
    except service_client.exceptions.ResourceNotFoundException:
        current_secret = service_client.get_secret_value(SecretId=arn, VersionStage="AWSCURRENT")
        current_data = json.loads(current_secret["SecretString"])

        # Generate new API key/token (simplified - in real scenario, call external API)
        if "api_key" in current_data:
            current_data["api_key"] = "new-" + current_data["api_key"]
        if "zendesk_api_token" in current_data:
            current_data["zendesk_api_token"] = "new-" + current_data["zendesk_api_token"]

        service_client.put_secret_value(
            SecretId=arn,
            ClientRequestToken=token,
            SecretString=json.dumps(current_data),
            VersionStages=["AWSPENDING"],
        )


def set_secret(service_client, arn, token):
    pass


def test_secret(service_client, arn, token):
    pass


def finish_secret(service_client, arn, token):
    metadata = service_client.describe_secret(SecretId=arn)
    current_version = None
    for version in metadata["VersionIdsToStages"]:
        if "AWSCURRENT" in metadata["VersionIdsToStages"][version]:
            if version == token:
                return
            current_version = version
            break

    service_client.update_secret_version_stage(
        SecretId=arn,
        VersionStage="AWSCURRENT",
        MoveToVersionId=token,
        RemoveFromVersionId=current_version,
    )
