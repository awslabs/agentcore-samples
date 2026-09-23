#!/usr/bin/env python3
"""
Bedrock Agent Stack - Refactored CDK Application

This is the main entry point for the CDK application.
The stack is organized into logical constructs for better maintainability.
"""

import os
import aws_cdk as cdk
from constructs import Construct
from aws_cdk import Stack, Tags, RemovalPolicy, aws_s3 as s3

from stacks.database import DatabaseConstruct
from stacks.messaging import MessagingConstruct
from stacks.agent_runtime import AgentRuntimeConstruct
from stacks.lambdas import LambdasConstruct
from stacks.api import ApiConstruct
from stacks.frontend import FrontendConstruct

# Get the directory where this CDK app file is located
CDK_APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CDK_APP_DIR)


class BedrockAgentStack(Stack):
    """
    Main stack for the Bedrock Agent application.

    This stack creates all the infrastructure needed for:
    - Agent deployment and management
    - Token usage tracking and aggregation
    - Frontend dashboard hosting
    - API Gateway for all operations
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        account_id = self.account
        region = self.region

        # ============================================================
        # Storage Layer
        # ============================================================

        # S3 bucket for agent code
        # Note: Server access logging disabled to avoid deletion issues
        # For production, consider using CloudWatch Logs or S3 Inventory instead
        code_bucket = s3.Bucket(
            self,
            "AgentCodeBucket",
            bucket_name=f"bedrock-agentcore-code-{account_id}-{region}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            enforce_ssl=True,
        )

        # DynamoDB tables
        database = DatabaseConstruct(self, "Database", account_id=account_id, region=region)

        # ============================================================
        # Messaging Layer
        # ============================================================

        messaging = MessagingConstruct(self, "Messaging", account_id=account_id, region=region)

        # ============================================================
        # Agent Runtime
        # ============================================================

        agent_runtime = AgentRuntimeConstruct(
            self,
            "AgentRuntime",
            region=region,
            usage_queue=messaging.usage_queue,
        )

        # ============================================================
        # Lambda Functions
        # ============================================================

        lambdas = LambdasConstruct(
            self,
            "Lambdas",
            cdk_app_dir=CDK_APP_DIR,
            usage_table=database.usage_table,
            aggregation_table=database.aggregation_table,
            agent_config_table=database.agent_config_table,
            agent_details_table=database.agent_details_table,
            usage_queue=messaging.usage_queue,
            code_bucket=code_bucket,
            agent_role_arn=agent_runtime.agent_role.role_arn,
        )

        # Add dependency: build_deploy_agent depends on agent_role
        lambdas.build_deploy_agent.node.add_dependency(agent_runtime.agent_role)

        # ============================================================
        # API Gateway
        # ============================================================

        api = ApiConstruct(
            self,
            "Api",
            async_deploy_lambda=lambdas.async_deploy,
            token_usage_lambda=lambdas.token_usage,
            invoke_agent_lambda=lambdas.invoke_agent,
            get_agent_lambda=lambdas.get_agent,
            list_agents_lambda=lambdas.list_agents,
            delete_agent_lambda=lambdas.delete_agent,
            update_config_lambda=lambdas.update_config,
            set_tenant_limit_lambda=lambdas.set_tenant_limit,
            infrastructure_costs_lambda=lambdas.infrastructure_costs,
        )

        # ============================================================
        # Frontend Hosting
        # ============================================================

        frontend = FrontendConstruct(
            self,
            "Frontend",
            account_id=account_id,
            region=region,
            project_root=PROJECT_ROOT,
            cdk_app_dir=CDK_APP_DIR,
            api=api.api,
            api_key=api.api_key,
        )

        # ============================================================
        # Stack Outputs
        # ============================================================

        self._create_outputs(
            code_bucket=code_bucket,
            database=database,
            messaging=messaging,
            lambdas=lambdas,
            api=api,
            frontend=frontend,
        )

    def _create_outputs(
        self,
        code_bucket: s3.Bucket,
        database: DatabaseConstruct,
        messaging: MessagingConstruct,
        lambdas: LambdasConstruct,
        api: ApiConstruct,
        frontend: FrontendConstruct,
    ) -> None:
        """Create all CloudFormation outputs."""

        outputs = {
            "QueueUrl": (messaging.usage_queue.queue_url, "SQS Queue URL"),
            "TableName": (database.usage_table.table_name, "DynamoDB Table Name"),
            "AggregationTableName": (
                database.aggregation_table.table_name,
                "DynamoDB Aggregation Table Name",
            ),
            "LambdaArn": (
                lambdas.sqs_processor.function_arn,
                "SQS to DynamoDB Lambda Function ARN",
            ),
            "StreamProcessorLambdaArn": (
                lambdas.stream_processor.function_arn,
                "DynamoDB Stream Processor Lambda ARN",
            ),
            "BuildDeployAgentLambdaArn": (
                lambdas.build_deploy_agent.function_arn,
                "Build & Deploy Agent Lambda ARN",
            ),
            "CodeBucket": (code_bucket.bucket_name, "S3 Bucket for Agent Code"),
            "ApiEndpoint": (api.api.url, "API Gateway Endpoint URL"),
            "ApiKeyId": (api.api_key.key_id, "API Key ID"),
            "DeployEndpoint": (
                f"{api.api.url}deploy?tenantId=<TENANT_ID>",
                "Deploy Agent Endpoint",
            ),
            "FrontendUrl": (
                f"https://{frontend.distribution.distribution_domain_name}",
                "Frontend Dashboard URL",
            ),
            "FrontendBucketName": (
                frontend.bucket.bucket_name,
                "S3 Bucket for Frontend",
            ),
        }

        for name, (value, description) in outputs.items():
            cdk.CfnOutput(self, name, value=value, description=description)


# ============================================================
# App Entry Point
# ============================================================

app = cdk.App()

stack = BedrockAgentStack(
    app,
    "BedrockAgentStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-west-2"),
    ),
)

# Add stack-level tags for cost allocation and organization
Tags.of(stack).add("Project", "BedrockAgentCore")
Tags.of(stack).add("Environment", "Production")
Tags.of(stack).add("ManagedBy", "CDK")
Tags.of(stack).add("CostCenter", "AI-Operations")

app.synth()
