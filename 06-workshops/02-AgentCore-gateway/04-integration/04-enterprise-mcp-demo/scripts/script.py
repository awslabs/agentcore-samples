import boto3

output = """
EnterpriseMcpInfraStack.ApiGatewayDefaultUrl =
EnterpriseMcpInfraStack.ApiGatewayEndpoint =
EnterpriseMcpInfraStack.ApiGatewayId =
EnterpriseMcpInfraStack.CognitoDomain =
EnterpriseMcpInfraStack.CognitoDomainUrl =
EnterpriseMcpInfraStack.DeploymentTypeOutput =
EnterpriseMcpInfraStack.DiscoveryUrl =
EnterpriseMcpInfraStack.EndpointUrl =
EnterpriseMcpInfraStack.Gateway =
EnterpriseMcpInfraStack.PreTokenGenerationLambdaArn =
EnterpriseMcpInfraStack.PreTokenGenerationLambdaName = E
EnterpriseMcpInfraStack.ProxyLambdaName =
EnterpriseMcpInfraStack.UserPoolArn =
EnterpriseMcpInfraStack.UserPoolId =
EnterpriseMcpInfraStack.VSCodeClientId =
EnterpriseMcpInfraStack.VSCodeMcpConfig = {
  "servers": {
    "enterprise-mcp-server": {
      "type": "http",
      "url": ""
    }
  }
}
"""

config = {}
for el in output.split("\n"):
    if "=" in el:
        key, value = el.split("=", 1)
        config[key.strip().replace("EnterpriseMcpInfraStack.", "")] = value.strip()


# Users to create - using email as username for Cognito
users = [
    {
        "COGNITO_USERNAME": "vscode-admin@example.com",
        "COGNITO_PASSWORD": "TempPassword123!",
    },
    {
        "COGNITO_USERNAME": "vscode-user@example.com",
        "COGNITO_PASSWORD": "TempPassword1234!",
    },
]

cognito = boto3.client("cognito-idp")
user_pool_id = config["UserPoolId"]

# Create users with email as username
for user in users:
    COGNITO_USERNAME = user["COGNITO_USERNAME"]
    COGNITO_PASSWORD = user["COGNITO_PASSWORD"]
    try:
        # Create user with email as username
        cognito.admin_create_user(
            UserPoolId=user_pool_id,
            Username=COGNITO_USERNAME,
            TemporaryPassword=COGNITO_PASSWORD,
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "email", "Value": f"{COGNITO_USERNAME}"},
                {"Name": "email_verified", "Value": "true"},
            ],
        )
        cognito.admin_set_user_password(
            UserPoolId=user_pool_id,
            Username=COGNITO_USERNAME,
            Password=COGNITO_PASSWORD,
            Permanent=True,
        )
        print(f"✓ User created: {user['COGNITO_USERNAME']}")
    except cognito.exceptions.UsernameExistsException:
        print(f"✓ User exists: {user['COGNITO_USERNAME']}")
