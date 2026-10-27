"""
Custom Resource Lambda to update Secrets Manager with Cognito client secret.
"""

import json
import boto3


def lambda_handler(event, context):
    """Handle Custom Resource events."""
    try:
        print(f"Received event: {json.dumps(event)}")

        request_type = event["RequestType"]
        if request_type == "Delete":
            return {
                "PhysicalResourceId": event.get(
                    "PhysicalResourceId", "cognito-secret-updater"
                )
            }

        properties = event["ResourceProperties"]
        user_pool_id = properties["UserPoolId"]
        client_id = properties["ClientId"]
        secret_arn = properties["SecretArn"]

        cognito_client = boto3.client("cognito-idp")
        secrets_client = boto3.client("secretsmanager")

        # Get client secret from Cognito
        response = cognito_client.describe_user_pool_client(
            UserPoolId=user_pool_id, ClientId=client_id
        )

        user_pool_client = response["UserPoolClient"]

        # Get existing secret value
        secret_response = secrets_client.get_secret_value(SecretId=secret_arn)
        secret_data = json.loads(secret_response["SecretString"])

        # Update with client secret
        if "ClientSecret" in user_pool_client:
            secret_data["client_secret"] = user_pool_client["ClientSecret"]
            print("Updated client secret in Secrets Manager")
        else:
            secret_data["client_secret"] = "NOT_APPLICABLE_PUBLIC_CLIENT"
            print("Public client - no secret to update")

        # Update the secret
        secrets_client.update_secret(
            SecretId=secret_arn, SecretString=json.dumps(secret_data, indent=2)
        )

        return {
            "PhysicalResourceId": f"secret-updater-{client_id}",
            "Data": {"ClientSecretUpdated": "true"},
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        raise e
