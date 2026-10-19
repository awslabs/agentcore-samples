from aws_cdk import (
    Duration,
    Stack,
    CfnOutput,
    aws_lambda as lambda_,
)
from constructs import Construct
import os


class InterceptorStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Lambda function for toon interceptor (TypeScript/Node.js)
        self.interceptor_function = lambda_.Function(
            self,
            "ToonInterceptorFunction",
            runtime=lambda_.Runtime.NODEJS_24_X,
            handler="lambda_function.lambda_handler",
            code=lambda_.Code.from_asset(
                os.path.join(
                    os.path.dirname(__file__), "..", "lambda", "toon_interceptor"
                ),
                bundling={
                    "image": lambda_.Runtime.NODEJS_24_X.bundling_image,
                    "environment": {
                        "npm_config_cache": "/tmp/.npm",
                    },
                    "command": [
                        "bash",
                        "-c",
                        "npm install && npm run build && cp -r dist/* /asset-output/ && cp -r node_modules /asset-output/ && cp package.json /asset-output/",
                    ],
                },
            ),
            timeout=Duration.seconds(30),
            memory_size=256,
        )

        # Outputs
        CfnOutput(
            self,
            "InterceptorFunctionName",
            value=self.interceptor_function.function_name,
            description="Name of the Toon Interceptor Lambda function",
        )

        CfnOutput(
            self,
            "InterceptorFunctionArn",
            value=self.interceptor_function.function_arn,
            description="ARN of the Toon Interceptor Lambda function",
        )
