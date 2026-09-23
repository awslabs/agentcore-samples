# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import boto3
import json
import time
from boto3.session import Session
from botocore.exceptions import ClientError
import requests


def get_or_create_user_pool(cognito, USER_POOL_NAME):
    response = cognito.list_user_pools(MaxResults=60)
    for pool in response["UserPools"]:
        if pool["Name"] == USER_POOL_NAME:
            user_pool_id = pool["Id"]
            response = cognito.describe_user_pool(UserPoolId=user_pool_id)

            # Get the domain from user pool description
            user_pool = response.get("UserPool", {})
            domain = user_pool.get("Domain")

            if domain:
                region = user_pool_id.split("_")[0]
                domain_url = f"https://{domain}.auth.{region}.amazoncognito.com"
                print(
                    f"Found domain for user pool {user_pool_id}: {domain} ({domain_url})"
                )
            else:
                print(f"No domains found for user pool {user_pool_id}")
            return pool["Id"]
    print("Creating new user pool")
    created = cognito.create_user_pool(PoolName=USER_POOL_NAME)
    user_pool_id = created["UserPool"]["Id"]
    user_pool_id_without_underscore_lc = user_pool_id.replace("_", "").lower()
    cognito.create_user_pool_domain(
        Domain=user_pool_id_without_underscore_lc, UserPoolId=user_pool_id
    )
    print("Domain created as well")
    return created["UserPool"]["Id"]


def get_or_create_resource_server(
    cognito, user_pool_id, RESOURCE_SERVER_ID, RESOURCE_SERVER_NAME, SCOPES
):
    try:
        cognito.describe_resource_server(
            UserPoolId=user_pool_id, Identifier=RESOURCE_SERVER_ID
        )
        return RESOURCE_SERVER_ID
    except cognito.exceptions.ResourceNotFoundException:
        print("creating new resource server")
        cognito.create_resource_server(
            UserPoolId=user_pool_id,
            Identifier=RESOURCE_SERVER_ID,
            Name=RESOURCE_SERVER_NAME,
            Scopes=SCOPES,
        )
        return RESOURCE_SERVER_ID


def get_or_create_m2m_client(
    cognito, user_pool_id, CLIENT_NAME, RESOURCE_SERVER_ID, SCOPES=None
):
    response = cognito.list_user_pool_clients(UserPoolId=user_pool_id, MaxResults=60)
    for client in response["UserPoolClients"]:
        if client["ClientName"] == CLIENT_NAME:
            describe = cognito.describe_user_pool_client(
                UserPoolId=user_pool_id, ClientId=client["ClientId"]
            )
            return client["ClientId"], describe["UserPoolClient"]["ClientSecret"]
    print("creating new m2m client")

    # Default scopes if not provided (for backward compatibility)
    if SCOPES is None:
        SCOPES = [
            f"{RESOURCE_SERVER_ID}/gateway:read",
            f"{RESOURCE_SERVER_ID}/gateway:write",
        ]

    created = cognito.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=CLIENT_NAME,
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=SCOPES,
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
        ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"],
    )
    return (
        created["UserPoolClient"]["ClientId"],
        created["UserPoolClient"]["ClientSecret"],
    )


def get_token(
    user_pool_id: str,
    client_id: str,
    client_secret: str,
    scope_string: str,
    REGION: str,
) -> dict:
    try:
        user_pool_id_without_underscore = user_pool_id.replace("_", "")
        url = f"https://{user_pool_id_without_underscore}.auth.{REGION}.amazoncognito.com/oauth2/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope_string,
        }
        response = requests.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as err:
        return {"error": str(err)}


def create_agentcore_gateway_role(gateway_name, cred_provider_arn, secret_arn):
    iam_client = boto3.client("iam")
    agentcore_gateway_role_name = f"agentcore-{gateway_name}-role"
    boto_session = Session()
    region = boto_session.region_name
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    identity_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "GetWorkloadAccessToken",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/{gateway_name}-*",
                ],
            },
            {
                "Sid": "GetResourceOauth2Token",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetResourceOauth2Token",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default",
                    cred_provider_arn,
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/{gateway_name}-*",
                ],
            },
            {
                "Sid": "GetSecretValue",
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:GetSecretValue",
                ],
                "Resource": [
                    secret_arn,
                ],
            },
        ],
    }

    gateway_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "GetGateway",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetGateway",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/{gateway_name}-*",
                ],
            },
        ],
    }

    assume_role_policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": f"{account_id}"},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"
                    },
                },
            }
        ],
    }

    assume_role_policy_document_json = json.dumps(assume_role_policy_document)

    try:
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_gateway_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json,
        )
        time.sleep(10)
    except iam_client.exceptions.EntityAlreadyExistsException:
        print("Role already exists -- updating policies")
        agentcore_iam_role = iam_client.get_role(RoleName=agentcore_gateway_role_name)
        iam_client.update_assume_role_policy(
            RoleName=agentcore_gateway_role_name,
            PolicyDocument=assume_role_policy_document_json,
        )

    print(f"Attaching identity policy to {agentcore_gateway_role_name}")
    iam_client.put_role_policy(
        PolicyDocument=json.dumps(identity_policy),
        PolicyName="AgentCoreIdentityPolicy",
        RoleName=agentcore_gateway_role_name,
    )

    print(f"Attaching gateway policy to {agentcore_gateway_role_name}")
    iam_client.put_role_policy(
        PolicyDocument=json.dumps(gateway_policy),
        PolicyName="AgentCoreGatewayPolicy",
        RoleName=agentcore_gateway_role_name,
    )

    return agentcore_iam_role


def get_current_role_arn():
    sts_client = boto3.client("sts")
    role_arn = sts_client.get_caller_identity()["Arn"]
    return role_arn


def create_gateway_invoke_tool_role(role_name, gateway_id, current_arn):
    # Normalize current_arn
    if isinstance(current_arn, (list, set, tuple)):
        current_arn = list(current_arn)[0]
    current_arn = str(current_arn)

    # AWS clients
    boto_session = Session()
    region = boto_session.region_name
    iam_client = boto3.client("iam", region_name=region)
    sts_client = boto3.client("sts")
    account_id = sts_client.get_caller_identity()["Account"]

    # --- Trust policy (AssumeRolePolicyDocument) ---
    assume_role_policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRoleByAgentCore",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": ["sts:AssumeRole"],
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                },
            },
            {
                "Sid": "AllowCallerToAssume",
                "Effect": "Allow",
                "Principal": {"AWS": [current_arn]},
                "Action": ["sts:AssumeRole"],
            },
        ],
    }
    assume_role_policy_json = json.dumps(assume_role_policy_document)

    # ---  Inline role policy (Bedrock gateway invoke) ---
    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeGateway"],
                "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/{gateway_id}",
            }
        ],
    }
    role_policy_json = json.dumps(role_policy)

    # --- Create or update IAM role ---
    try:
        agentcoregw_iam_role = iam_client.create_role(
            RoleName=role_name, AssumeRolePolicyDocument=assume_role_policy_json
        )
        print(f"Created new role: {role_name}")
        time.sleep(3)
    except iam_client.exceptions.EntityAlreadyExistsException:
        print(f"Role '{role_name}' already exists — updating trust and inline policy.")
        iam_client.update_assume_role_policy(
            RoleName=role_name, PolicyDocument=assume_role_policy_json
        )
        for policy_name in iam_client.list_role_policies(RoleName=role_name).get(
            "PolicyNames", []
        ):
            iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        agentcoregw_iam_role = iam_client.get_role(RoleName=role_name)

    # Attach inline role policy (gateway invoke)
    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName="AgentCorePolicy",
        PolicyDocument=role_policy_json,
    )

    role_arn = agentcoregw_iam_role["Role"]["Arn"]

    # ---  Ensure current_arn can assume role (with retry) ---
    arn_parts = current_arn.split(":")
    resource_type, resource_name = arn_parts[5].split("/", 1)

    assume_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": "sts:AssumeRole", "Resource": role_arn}
        ],
    }

    # Attach assume-role policy if user/role
    try:
        if resource_type == "user":
            iam_client.put_user_policy(
                UserName=resource_name,
                PolicyName=f"AllowAssume_{role_name}",
                PolicyDocument=json.dumps(assume_policy),
            )
        elif resource_type == "role":
            iam_client.put_role_policy(
                RoleName=resource_name,
                PolicyName=f"AllowAssume_{role_name}",
                PolicyDocument=json.dumps(assume_policy),
            )
    except ClientError as e:
        print(f"Unable to attach assume-role policy: {e}")
        print(
            "Make sure the caller has iam:PutUserPolicy or iam:PutRolePolicy permission."
        )

    # Retry loop for eventual consistency
    max_retries = 5
    for i in range(max_retries):
        try:
            sts_client.assume_role(RoleArn=role_arn, RoleSessionName="testSession")
            print(f"Caller {current_arn} can now assume role {role_name}")
            break
        except ClientError as e:
            if "AccessDenied" in str(e):
                print(f"Attempt {i + 1}/{max_retries}: AccessDenied, retrying in 3s...")
                time.sleep(3)
            else:
                raise
    else:
        raise RuntimeError(
            f"Failed to assume role {role_name} after {max_retries} retries"
        )

    print(
        f" Role '{role_name}' is ready and {current_arn} can invoke the Bedrock Agent Gateway."
    )
    return agentcoregw_iam_role


def start_callback_and_open_auth(
    auth_url,
    identifier_type,
    identifier_value,
    region,
    callback_server_path="utils/callback_server.py",
):
    import subprocess
    import webbrowser
    import time

    # Kill any existing callback server on port 8080
    existing_pids = subprocess.run(
        ["lsof", "-ti:8080"], capture_output=True, text=True
    ).stdout.strip()
    if existing_pids:
        for pid in existing_pids.split():
            subprocess.run(["kill", "-9", pid], capture_output=True)
        time.sleep(1)

    # Start callback server
    server_process = subprocess.Popen(
        [
            "python",
            callback_server_path,
            f"--{identifier_type}",
            identifier_value,
            "--region",
            region,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(2)
    print("Callback server started on http://localhost:8080/callback")

    # Open authorization URL in browser
    webbrowser.open(auth_url)
    print("Opened authorization URL in browser")
    input("Press Enter after completing authorization in the browser...")

    return server_process


def complete_session_binding(
    response_json,
    access_token,
    region,
    callback_server_path="utils/callback_server.py",
):
    try:
        elicitation = response_json["error"]["data"]["elicitations"][0]
        auth_url = elicitation["url"]
    except (KeyError, IndexError, TypeError):
        print("Authentication was not required — cached credentials were used.")
        return None

    return start_callback_and_open_auth(
        auth_url, "user-token", access_token, region, callback_server_path
    )


def delete_gateway(gateway_client, gatewayId):
    print("Deleting all targets for gateway", gatewayId)
    list_response = gateway_client.list_gateway_targets(
        gatewayIdentifier=gatewayId, maxResults=100
    )
    for item in list_response["items"]:
        targetId = item["targetId"]
        print("Deleting target ", targetId)
        gateway_client.delete_gateway_target(
            gatewayIdentifier=gatewayId, targetId=targetId
        )
        time.sleep(5)
    print("Deleting gateway ", gatewayId)
    gateway_client.delete_gateway(gatewayIdentifier=gatewayId)
