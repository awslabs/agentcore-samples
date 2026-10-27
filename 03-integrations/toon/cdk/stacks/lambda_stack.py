from aws_cdk import (
    Duration,
    Stack,
    CfnOutput,
    aws_lambda as lambda_,
    aws_dynamodb as dynamodb,
)
from constructs import Construct
import os


class LambdaStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        customers_table: dynamodb.ITable,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Lambda function for DynamoDB CRUD operations
        self.dynamodb_crud_function = lambda_.Function(
            self,
            "DynamoDBCrudFunction",
            runtime=lambda_.Runtime.PYTHON_3_14,
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "..", "lambda", "dynamodb_crud")
            ),
            timeout=Duration.seconds(30),
            memory_size=256,
            environment={
                "TABLE_NAME": customers_table.table_name,
            },
        )

        # Grant read permissions to the Lambda function
        customers_table.grant_read_data(self.dynamodb_crud_function)

        # Outputs
        CfnOutput(
            self,
            "CrudFunctionName",
            value=self.dynamodb_crud_function.function_name,
            description="Name of the DynamoDB CRUD Lambda function",
        )

        CfnOutput(
            self,
            "CrudFunctionArn",
            value=self.dynamodb_crud_function.function_arn,
            description="ARN of the DynamoDB CRUD Lambda function",
        )
