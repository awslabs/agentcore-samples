"""Database construct for DynamoDB tables"""

from constructs import Construct
from aws_cdk import RemovalPolicy, aws_dynamodb as dynamodb


class DatabaseConstruct(Construct):
    """Construct for all DynamoDB tables used in the application."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        account_id: str,
        region: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Token usage table with streams enabled
        self.usage_table = dynamodb.Table(
            self,
            "TokenUsageTable",
            table_name=f"token-usage-{account_id}-{region}",
            partition_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            stream=dynamodb.StreamViewType.NEW_IMAGE,
            point_in_time_recovery=True,
        )

        # Token aggregation table
        self.aggregation_table = dynamodb.Table(
            self,
            "TokenAggregationTable",
            table_name=f"token-aggregation-{account_id}-{region}",
            partition_key=dynamodb.Attribute(name="aggregation_key", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=True,
        )

        # Agent configurations table (runtime config)
        self.agent_config_table = dynamodb.Table(
            self,
            "AgentConfigTable",
            table_name=f"agent-configurations-{account_id}-{region}",
            partition_key=dynamodb.Attribute(name="tenantId", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="agentRuntimeId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=True,
        )

        # Agent details table
        self.agent_details_table = dynamodb.Table(
            self,
            "AgentDetailsTable",
            table_name=f"agent-details-{account_id}-{region}",
            partition_key=dynamodb.Attribute(name="tenantId", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="agentRuntimeId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=True,
        )
