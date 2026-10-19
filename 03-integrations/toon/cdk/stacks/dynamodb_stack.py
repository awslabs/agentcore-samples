from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput,
    CustomResource,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    custom_resources as cr,
)
from constructs import Construct
import os


class DynamoDBStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        seed_data: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # DynamoDB table for customer data
        self.customers_table = dynamodb.Table(
            self,
            "CustomersTable",
            partition_key=dynamodb.Attribute(
                name="customer_id",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="email",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Global Secondary Index for querying by region
        self.customers_table.add_global_secondary_index(
            index_name="RegionIndex",
            partition_key=dynamodb.Attribute(
                name="region",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="created_at",
                type=dynamodb.AttributeType.STRING,
            ),
        )

        # Global Secondary Index for querying by subscription tier
        self.customers_table.add_global_secondary_index(
            index_name="SubscriptionTierIndex",
            partition_key=dynamodb.Attribute(
                name="subscription_tier",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="monthly_spend",
                type=dynamodb.AttributeType.NUMBER,
            ),
        )

        # Output the table name
        CfnOutput(
            self,
            "CustomersTableName",
            value=self.customers_table.table_name,
            description="Name of the Customers DynamoDB table",
        )

        CfnOutput(
            self,
            "CustomersTableArn",
            value=self.customers_table.table_arn,
            description="ARN of the Customers DynamoDB table",
        )

        # Seed data Custom Resource (optional)
        if seed_data:
            seed_lambda = lambda_.Function(
                self,
                "SeedDataFunction",
                runtime=lambda_.Runtime.PYTHON_3_14,
                handler="index.lambda_handler",
                code=lambda_.Code.from_asset(
                    os.path.join(os.path.dirname(__file__), "..", "lambda", "seed_data")
                ),
                timeout=Duration.minutes(5),
                memory_size=256,
                environment={
                    "TABLE_NAME": self.customers_table.table_name,
                },
            )

            # Grant write permissions to seed Lambda
            self.customers_table.grant_write_data(seed_lambda)

            # Custom Resource Provider
            seed_provider = cr.Provider(
                self,
                "SeedDataProvider",
                on_event_handler=seed_lambda,
            )

            # Custom Resource to trigger seeding
            seed_resource = CustomResource(
                self,
                "SeedDataResource",
                service_token=seed_provider.service_token,
                properties={
                    "TableName": self.customers_table.table_name,
                    "NumRecords": "120",
                },
            )

            CfnOutput(
                self,
                "SeedDataRecordsInserted",
                value=seed_resource.get_att_string("RecordsInserted"),
                description="Number of mock records seeded into the table",
            )
