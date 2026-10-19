#!/usr/bin/env python3
import aws_cdk as cdk

from cdk.stacks import (
    DynamoDBStack,
    LambdaStack,
    InterceptorStack,
    GatewayStack,
    CognitoStack,
)


app = cdk.App()

# Configuration (can be overridden via context)
environment = app.node.try_get_context("environment") or "dev"
seed_data = app.node.try_get_context("seed_data") != "false"  # Default: true

# Cognito Stack for M2M authentication
cognito_stack = CognitoStack(
    app,
    "CognitoStack",
    environment=environment,
)

dynamodb_stack = DynamoDBStack(
    app,
    "DynamoDBStack",
    seed_data=seed_data,
)

lambda_stack = LambdaStack(
    app,
    "LambdaStack",
    customers_table=dynamodb_stack.customers_table,
)

interceptor_stack = InterceptorStack(
    app,
    "InterceptorStack",
)

GatewayStack(
    app,
    "GatewayStack",
    crud_lambda=lambda_stack.dynamodb_crud_function,
    interceptor_lambda=interceptor_stack.interceptor_function,
    cognito_discovery_url=f"https://cognito-idp.{cdk.Aws.REGION}.amazonaws.com/{cognito_stack.user_pool.user_pool_id}/.well-known/openid-configuration",
    cognito_client_id=cognito_stack.gateway_client.user_pool_client_id,
    environment=environment,
)

app.synth()
