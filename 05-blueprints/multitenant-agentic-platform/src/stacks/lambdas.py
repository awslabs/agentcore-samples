"""Lambda functions construct"""

import os
from constructs import Construct
from aws_cdk import (
    Duration,
    Size,
    RemovalPolicy,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_event_sources,
    aws_iam as iam,
    aws_logs as logs,
    aws_dynamodb as dynamodb,
    aws_sqs as sqs,
    aws_s3 as s3,
)


class LambdasConstruct(Construct):
    """Construct for all Lambda functions used in the application."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        cdk_app_dir: str,
        usage_table: dynamodb.Table,
        aggregation_table: dynamodb.Table,
        agent_config_table: dynamodb.Table,
        agent_details_table: dynamodb.Table,
        usage_queue: sqs.Queue,
        code_bucket: s3.Bucket,
        agent_role_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.cdk_app_dir = cdk_app_dir

        # SQS to DynamoDB processor
        self.sqs_processor = self._create_lambda(
            "SQSToDynamoDBProcessor",
            "sqs-to-dynamodb-processor",
            "lambda_functions/sqs_to_dynamodb",
            environment={"TABLE_NAME": usage_table.table_name},
        )
        usage_table.grant_write_data(self.sqs_processor)
        self.sqs_processor.add_event_source(lambda_event_sources.SqsEventSource(usage_queue, batch_size=10))

        # DynamoDB stream processor
        self.stream_processor = self._create_lambda(
            "DynamoDBStreamProcessor",
            "dynamodb-stream-processor",
            "lambda_functions/dynamodb_stream_processor",
            environment={"AGGREGATION_TABLE_NAME": aggregation_table.table_name},
        )
        usage_table.grant_stream_read(self.stream_processor)
        aggregation_table.grant_read_write_data(self.stream_processor)
        self.stream_processor.add_event_source(
            lambda_event_sources.DynamoEventSource(
                usage_table,
                starting_position=lambda_.StartingPosition.LATEST,
                batch_size=10,
                retry_attempts=3,
            )
        )

        # Build and deploy agent Lambda (special config - high memory/storage)
        build_deploy_log_group = logs.LogGroup(
            self,
            "BuildDeployAgentLogGroup",
            log_group_name="/aws/lambda/build-deploy-bedrock-agent",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.build_deploy_agent = lambda_.Function(
            self,
            "BuildDeployAgentLambda",
            function_name="build-deploy-bedrock-agent",
            runtime=lambda_.Runtime.PYTHON_3_10,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(os.path.join(cdk_app_dir, "lambda_functions/build_deploy_agent")),
            timeout=Duration.minutes(15),
            memory_size=3008,
            log_group=build_deploy_log_group,
            ephemeral_storage_size=Size.mebibytes(10240),
            environment={
                "AGENT_NAME": "sqs",
                "QUEUE_URL": usage_queue.queue_url,
                "ROLE_ARN": agent_role_arn,
                "BUCKET_NAME": code_bucket.bucket_name,
                "AGENT_CONFIG_TABLE_NAME": agent_config_table.table_name,
                "AGENT_DETAILS_TABLE_NAME": agent_details_table.table_name,
            },
        )
        code_bucket.grant_read_write(self.build_deploy_agent)
        agent_details_table.grant_write_data(self.build_deploy_agent)
        agent_config_table.grant_read_write_data(self.build_deploy_agent)

        # Add Bedrock and IAM permissions
        self.build_deploy_agent.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore-control:*",
                    "bedrock-agentcore:*",
                ],
                resources=["*"],
            )
        )
        self.build_deploy_agent.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[agent_role_arn],
            )
        )
        self.build_deploy_agent.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "iam:CreateServiceLinkedRole",
                ],
                resources=["arn:aws:iam::*:role/aws-service-role/bedrock-agentcore.amazonaws.com/*"],
                conditions={"StringLike": {"iam:AWSServiceName": "bedrock-agentcore.amazonaws.com"}},
            )
        )
        self.build_deploy_agent.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "iam:GetRole",
                ],
                resources=["arn:aws:iam::*:role/aws-service-role/bedrock-agentcore.amazonaws.com/*"],
            )
        )
        self.build_deploy_agent.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "ce:ListCostAllocationTags",
                    "ce:UpdateCostAllocationTagsStatus",
                ],
                resources=["*"],
            )
        )

        # Async deploy Lambda
        self.async_deploy = self._create_lambda(
            "AsyncDeployLambda",
            "async-deploy-agent",
            "lambda_functions/async_deploy_agent",
            timeout_seconds=10,
            environment={"BUILD_DEPLOY_FUNCTION_NAME": self.build_deploy_agent.function_name},
        )
        self.build_deploy_agent.grant_invoke(self.async_deploy)

        # Token usage Lambda
        self.token_usage = self._create_lambda(
            "TokenUsageLambda",
            "get-token-usage",
            "lambda_functions/token_usage",
            environment={"AGGREGATION_TABLE_NAME": aggregation_table.table_name},
        )
        aggregation_table.grant_read_data(self.token_usage)

        # Invoke agent Lambda
        self.invoke_agent = self._create_lambda(
            "InvokeAgentLambda",
            "invoke-bedrock-agent",
            "lambda_functions/invoke_agent",
            timeout_seconds=60,
            memory_size=512,
            environment={"AGGREGATION_TABLE_NAME": aggregation_table.table_name},
        )
        self.invoke_agent.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:*"],
                resources=["*"],
            )
        )
        aggregation_table.grant_read_data(self.invoke_agent)

        # Get agent details Lambda
        self.get_agent = self._create_lambda(
            "GetAgentLambda",
            "get-agent-details",
            "lambda_functions/get_agent_details",
            environment={"AGENT_DETAILS_TABLE_NAME": agent_details_table.table_name},
        )
        agent_details_table.grant_read_data(self.get_agent)

        # List agents Lambda
        self.list_agents = self._create_lambda(
            "ListAgentsLambda",
            "list-agents",
            "lambda_functions/list_agents",
            environment={"AGENT_DETAILS_TABLE_NAME": agent_details_table.table_name},
        )
        agent_details_table.grant_read_data(self.list_agents)

        # Delete agent Lambda
        self.delete_agent = self._create_lambda(
            "DeleteAgentLambda",
            "delete-agent",
            "lambda_functions/delete_agent",
            timeout_seconds=60,
            environment={"AGENT_DETAILS_TABLE_NAME": agent_details_table.table_name},
        )
        agent_details_table.grant_read_write_data(self.delete_agent)
        self.delete_agent.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:DeleteAgentRuntime",
                    "bedrock-agentcore:GetAgentRuntime",
                ],
                resources=["*"],
            )
        )

        # Update config Lambda
        self.update_config = self._create_lambda(
            "UpdateConfigLambda",
            "update-agent-config",
            "lambda_functions/update_agent_config",
            environment={"AGENT_CONFIG_TABLE_NAME": agent_config_table.table_name},
        )
        agent_config_table.grant_read_write_data(self.update_config)

        # Set tenant limit Lambda
        self.set_tenant_limit = self._create_lambda(
            "SetTenantLimitLambda",
            "set-tenant-limit",
            "lambda_functions/set_tenant_limit",
            environment={"AGGREGATION_TABLE_NAME": aggregation_table.table_name},
        )
        aggregation_table.grant_read_write_data(self.set_tenant_limit)

        # Infrastructure costs Lambda
        self.infrastructure_costs = self._create_lambda(
            "InfrastructureCostsLambda",
            "get-infrastructure-costs",
            "lambda_functions/infrastructure_costs",
            timeout_seconds=30,
            environment={"AGGREGATION_TABLE_NAME": aggregation_table.table_name},
        )
        aggregation_table.grant_read_data(self.infrastructure_costs)
        # Grant Cost Explorer permissions
        self.infrastructure_costs.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ce:GetCostAndUsage"],
                resources=["*"],
            )
        )

    def _create_lambda(
        self,
        id: str,
        function_name: str,
        handler_path: str,
        timeout_seconds: int = 30,
        memory_size: int = 256,
        environment: dict = None,
    ) -> lambda_.Function:
        """Factory method to create Lambda functions with common configuration."""
        # Create log group explicitly
        log_group = logs.LogGroup(
            self,
            f"{id}LogGroup",
            log_group_name=f"/aws/lambda/{function_name}",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        return lambda_.Function(
            self,
            id,
            function_name=function_name,
            runtime=lambda_.Runtime.PYTHON_3_10,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(os.path.join(self.cdk_app_dir, handler_path)),
            timeout=Duration.seconds(timeout_seconds),
            memory_size=memory_size,
            log_group=log_group,
            environment=environment or {},
        )
