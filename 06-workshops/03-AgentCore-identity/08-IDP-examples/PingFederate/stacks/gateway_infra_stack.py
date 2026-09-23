# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Gateway infrastructure stack — MCP Echo Lambda + IAM role for the gateway."""

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class GatewayInfraStack(Stack):
    """Deploy the infrastructure needed for the AgentCore Gateway demo.

    Creates:
    - A minimal MCP Echo Lambda (gateway target)
    - An IAM role for the gateway (trusts bedrock-agentcore.amazonaws.com)
    """

    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        # --- MCP Echo Lambda ---
        self.mcp_echo_fn = lambda_.Function(
            self,
            "McpEchoFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_asset("lambda/mcp_echo"),
            timeout=Duration.seconds(30),
            memory_size=128,
        )

        # --- Gateway IAM Role ---
        # The condition block is omitted so the role can be used before the
        # gateway ID is known. For production use, add SourceAccount and
        # SourceArn conditions after gateway creation.
        self.gateway_role = iam.Role(
            self,
            "GatewayRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        )

        self.mcp_echo_fn.grant_invoke(self.gateway_role)

        # --- Outputs ---
        CfnOutput(
            self,
            "McpEchoLambdaArn",
            value=self.mcp_echo_fn.function_arn,
            description="Lambda ARN for the MCP Echo gateway target",
        )
        CfnOutput(
            self,
            "GatewayRoleArn",
            value=self.gateway_role.role_arn,
            description="IAM role ARN for the AgentCore Gateway",
        )
