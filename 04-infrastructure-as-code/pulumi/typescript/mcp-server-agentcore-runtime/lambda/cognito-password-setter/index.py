import json
import logging

import boto3


LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)


def handler(event, _context):
    LOGGER.info("Received event: %s", json.dumps(event))

    user_pool_id = event["userPoolId"]
    username = event["username"]
    password = event["password"]
    region = event.get("region")

    cognito = boto3.client("cognito-idp", region_name=region)
    cognito.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )

    LOGGER.info("Password set successfully for user: %s", username)
    return {"status": "SUCCESS", "username": username}
