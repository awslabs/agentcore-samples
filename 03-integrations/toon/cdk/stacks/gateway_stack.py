from aws_cdk import (
    Duration,
    Stack,
    CfnOutput,
    CustomResource,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_ssm as ssm,
    custom_resources as cr,
)
from constructs import Construct
import os


class GatewayStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        crud_lambda: lambda_.IFunction,
        interceptor_lambda: lambda_.IFunction,
        cognito_discovery_url: str,
        cognito_client_id: str,
        environment: str = "dev",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # IAM role for Bedrock AgentCore Gateway
        self.gateway_role = iam.Role(
            self,
            "BedrockAgentCoreGatewayRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:*"
                    },
                },
            ),
            description="IAM role for Bedrock AgentCore Gateway",
        )

        # Grant Lambda invoke permissions to the gateway role
        crud_lambda.grant_invoke(self.gateway_role)
        interceptor_lambda.grant_invoke(self.gateway_role)

        # Grant Bedrock AgentCore full access to the gateway role
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                sid="BedrockAgentCoreFullAccess",
                actions=["bedrock-agentcore:*"],
                resources=["*"],
            )
        )

        # Grant Secrets Manager access to the gateway role
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                sid="GetSecretValue",
                actions=["secretsmanager:GetSecretValue"],
                resources=["*"],
            )
        )

        # Lambda function for Custom Resource
        custom_resource_lambda = lambda_.Function(
            self,
            "GatewayCustomResourceFunction",
            runtime=lambda_.Runtime.PYTHON_3_14,
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset(
                os.path.join(
                    os.path.dirname(__file__), "..", "lambda", "gateway_custom_resource"
                )
            ),
            timeout=Duration.minutes(5),
            memory_size=256,
        )

        # Grant permissions to manage Bedrock AgentCore resources
        custom_resource_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:*"],
                resources=["*"],
            )
        )

        # Grant PassRole permission for the gateway role
        custom_resource_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[self.gateway_role.role_arn],
            )
        )

        # Tool schema for the CRUD Lambda
        tool_schema = [
            {
                "name": "get_customer",
                "description": "Retrieve a customer profile using customer ID and optionally email address",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {
                            "type": "string",
                            "description": "The unique identifier for the customer",
                        },
                        "email": {
                            "type": "string",
                            "description": "The customer's email address (optional, for exact match)",
                        },
                    },
                    "required": ["customer_id"],
                },
            },
            {
                "name": "query_by_region",
                "description": "Query customers by geographic region (Northeast, Southeast, Midwest, South, Southwest, West, Northwest, Mountain)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "region": {
                            "type": "string",
                            "description": "The geographic region to filter by",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of records to return (default: 50)",
                        },
                    },
                    "required": ["region"],
                },
            },
            {
                "name": "query_by_tier",
                "description": "Query customers by subscription tier (Free, Basic, Standard, Premium, Enterprise), sorted by monthly spend",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "subscription_tier": {
                            "type": "string",
                            "description": "The subscription tier to filter by",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of records to return (default: 50)",
                        },
                    },
                    "required": ["subscription_tier"],
                },
            },
            {
                "name": "batch_get_customers",
                "description": "Retrieve all customer records from the database",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "scan_customers",
                "description": "Scan the customers table with pagination to retrieve all records",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of records to return per page (default: 50)",
                        },
                        "last_evaluated_key": {
                            "type": "object",
                            "description": "The pagination key from previous scan to continue from",
                        },
                    },
                    "required": [],
                },
            },
        ]

        # Custom Resource Provider
        provider = cr.Provider(
            self,
            "GatewayProvider",
            on_event_handler=custom_resource_lambda,
        )

        # Custom Resource for the Gateway
        gateway_resource = CustomResource(
            self,
            "BedrockAgentCoreGateway",
            service_token=provider.service_token,
            properties={
                "GatewayName": "toon-mcp-gateway",
                "RoleArn": self.gateway_role.role_arn,
                "DiscoveryUrl": cognito_discovery_url,
                "AllowedClients": [cognito_client_id],
                "InterceptorLambdaArn": interceptor_lambda.function_arn,
                "CrudLambdaArn": crud_lambda.function_arn,
                "TargetName": "customers-crud",
                "TargetDescription": "DynamoDB CRUD operations for Customers table",
                "ToolSchema": tool_schema,
            },
        )

        # SSM Parameter for Gateway URL
        ssm.StringParameter(
            self,
            "GatewayUrlParameter",
            parameter_name=f"/app/toon/{environment}/gateway/url",
            string_value=gateway_resource.get_att_string("GatewayUrl"),
            description="Bedrock AgentCore Gateway MCP Endpoint URL",
        )

        # Outputs
        CfnOutput(
            self,
            "GatewayId",
            value=gateway_resource.get_att_string("GatewayId"),
            description="Bedrock AgentCore Gateway ID",
        )

        CfnOutput(
            self,
            "GatewayUrl",
            value=gateway_resource.get_att_string("GatewayUrl"),
            description="Bedrock AgentCore Gateway MCP Endpoint URL",
        )

        CfnOutput(
            self,
            "GatewayRoleArn",
            value=self.gateway_role.role_arn,
            description="IAM Role ARN for the Gateway",
        )
