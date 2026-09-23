"""
This program sets up the environment variables to be used by other programs.
It gets the values from Cloudformation output and oAuth discovery endpoint.
"""

import boto3
import os
import argparse
from urllib.parse import urlparse
import requests

# setting parameters
parser = argparse.ArgumentParser(
    prog="setup_env_variables",
    description="Setup environment variables for FHIR tools",
    epilog="Input Parameters",
)

parser.add_argument("--cfn_name", help="Name of cloudformation template")
parser.add_argument("--region", default="us-east-1", help="The AWS region to be used")
parser.add_argument(
    "--openapi_spec_file",
    default="./temp-fhir-openapi-spec.yaml",
    help="Path of OpenAPI spec file",
)
parser.add_argument("--profile", help="AWS Credentials Profile Name (optional)")


def main():
    apigateway_endpoint = ""
    apigateway_cognito_lambda = ""

    env_vars = {
        "aws_default_region": args.region,
        "gateway_iam_role": "",
        "cognito_discovery_url": "",
        "cognito_issuer": "",
        "cognito_auth_endpoint": "",
        "cognito_token_url": "",
        "cognito_user_pool_id": "",
        "cognito_client_id": "",
        "cognito_auth_scope": "",
        "healthlake_endpoint": "",
        "openapi_spec_file": args.openapi_spec_file,
    }

    # create boto3 session and client
    if args.profile is None:
        session = boto3.Session()  # using default profile
    else:
        session = boto3.Session(profile_name=args.profile)
        env_vars["awscred_profile_name"] = args.profile

    print(f"Getting output variables from Cloudformation stack name: {args.cfn_name}")
    cfn_client = session.client("cloudformation", region_name=args.region)

    next_token = "start"
    while next_token != "end":
        if next_token == "start":
            response = cfn_client.describe_stacks(StackName=args.cfn_name)
        else:
            response = cfn_client.describe_stacks(StackName=args.cfn_name, NextToken=next_token)

        next_token = "end" if "NextToken" not in response else response["NextToken"]

        for stack in response["Stacks"]:
            if stack["StackName"] == args.cfn_name:
                cfn_output = stack["Outputs"]

    for output in cfn_output:
        if output["OutputKey"] == "IAMRolePrimitivesArn":
            env_vars["gateway_iam_role"] = output["OutputValue"]
        elif output["OutputKey"] == "oAuthDiscoveryURL":
            env_vars["cognito_discovery_url"] = output["OutputValue"]
        elif output["OutputKey"] == "oAuthIssuer":
            env_vars["cognito_issuer"] = output["OutputValue"]
        elif output["OutputKey"] == "oAuthEndpoint":
            env_vars["cognito_auth_endpoint"] = output["OutputValue"]
        elif output["OutputKey"] == "oAuthTokenURL":
            env_vars["cognito_token_url"] = output["OutputValue"]
        elif output["OutputKey"] == "APIClientId":
            env_vars["cognito_client_id"] = output["OutputValue"]
        elif output["OutputKey"] == "oAuthScope":
            env_vars["cognito_auth_scope"] = output["OutputValue"]
        elif output["OutputKey"] == "HealthLakeEndpoint":
            env_vars["healthlake_endpoint"] = output["OutputValue"]
        elif output["OutputKey"] == "UserPoolId":
            env_vars["cognito_user_pool_id"] = output["OutputValue"]
        elif output["OutputKey"] == "ApiUrl":
            apigateway_endpoint = output["OutputValue"]
        elif output["OutputKey"] == "APIGWCognitoLambdaName":
            apigateway_cognito_lambda = output["OutputValue"]

    # print(env_vars)

    print(
        f"Getting oAuth issuer and auth endpoint using OpenID discovery endpoint: {env_vars['cognito_discovery_url']}"
    )
    response = requests.get(env_vars["cognito_discovery_url"])
    response_json = response.json()

    if "authorization_endpoint" in response_json:
        env_vars["cognito_auth_endpoint"] = response_json["authorization_endpoint"]

    if "issuer" in response_json:
        env_vars["cognito_issuer"] = response_json["issuer"]

    print("Creating .env file")
    # Open the .env file in write mode
    with open(".env", "w") as f:
        # Write each key-value pair to a new line
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")

    print(".env file created/updated successfully.")
    print(f"Please note down the APIEndpoint: {apigateway_endpoint} and update the OpenAPI spec accordingly")
    print(
        f"Please note down the APIGWCognitoLambdaName: {apigateway_cognito_lambda} as it would be needed in subsequent steps"
    )


def validate_url(url_string):
    try:
        result = urlparse(url_string)
        if all([result.scheme, result.netloc]):
            return (url_string, 0)
        else:
            return (f"Invalid URL format: '{url_string}'", 0)
    except ValueError:
        return (f"Invalid URL format: '{url_string}'", 0)


if __name__ == "__main__":
    args = parser.parse_args()

    # Validations
    if args.cfn_name is None:
        raise Exception("Cloudformation template name is required")

    if args.region is None:
        raise Exception("AWS Region is required")
    elif args.region != "us-east-1" and args.region != "us-west-2":
        raise Exception("Only regions us-east-1 and us-west-2 are supported for now")

    if args.openapi_spec_file is None:
        raise Exception("OpenAPI spec file path required")
    else:
        if not os.path.exists(args.openapi_spec_file):
            raise Exception("Invalid OpenAPI spec file path")

    main()
