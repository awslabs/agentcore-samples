# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
CDK Stack — Text-to-SQL with Amazon Bedrock AgentCore

Reads table definitions from config/tables.yaml and deploys:
- S3 Data Lake + Athena results bucket
- Glue Database + Tables (dynamic from YAML)
- Amazon Bedrock Guardrails
- Lambda + API Gateway (backend)
- S3 + CloudFront (frontend)
"""

import yaml
from pathlib import Path
from constructs import Construct
from aws_cdk import (
    Stack, Duration, RemovalPolicy, CfnOutput,
    aws_s3 as s3,
    aws_s3_deployment as s3_deploy,
    aws_lambda as lambda_,
    aws_apigateway as apigw,
    aws_iam as iam,
    aws_glue as glue,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_bedrock as bedrock,
)


class TextToSQLStack(Stack):

    def __init__(
        self, scope: Construct, construct_id: str, project_name: str, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.project_name = project_name.lower().replace(" ", "-")

        # Load table definitions from YAML
        self.tables_config = self._load_tables_config()
        self.database_name = self.tables_config.get(
            "database_name", f"{self.project_name.replace('-', '_')}_demo"
        )

        self._create_data_lake()
        self._create_glue_catalog()
        self._create_guardrails()
        self._create_backend()
        self._create_frontend()
        self._create_outputs()

    def _load_tables_config(self):
        """Load config/tables.yaml with table definitions."""
        config_path = Path(__file__).parent.parent / "config" / "tables.yaml"
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"Warning: Could not load tables.yaml: {e}")
            return {"database_name": "demo", "tables": []}

    def _create_data_lake(self):
        self.data_bucket = s3.Bucket(
            self, "DataLakeBucket",
            bucket_name=f"{self.project_name}-text-to-sql-data",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )
        self.athena_results_bucket = s3.Bucket(
            self, "AthenaResultsBucket",
            bucket_name=f"{self.project_name}-text-to-sql-athena",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[
                s3.LifecycleRule(expiration=Duration.days(7), enabled=True)
            ],
        )

    def _create_glue_catalog(self):
        """Create Glue Database and Tables dynamically from tables.yaml."""
        db_description = self.tables_config.get(
            "database_description",
            f"Text-to-SQL database for {self.project_name}",
        )
        s3_prefix = self.tables_config.get("s3_data_prefix", "data")

        self.glue_database = glue.CfnDatabase(
            self, "GlueDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=self.database_name,
                description=db_description,
            ),
        )

        # Create tables dynamically from YAML
        for i, table_def in enumerate(self.tables_config.get("tables", [])):
            table_name = table_def["name"]
            columns = [
                glue.CfnTable.ColumnProperty(
                    name=col["name"],
                    type=col["type"],
                    comment=col.get("comment", ""),
                )
                for col in table_def.get("columns", [])
            ]

            table = glue.CfnTable(
                self, f"Table{i}_{table_name}",
                catalog_id=self.account,
                database_name=self.database_name,
                table_input=glue.CfnTable.TableInputProperty(
                    name=table_name,
                    description=table_def.get("description", ""),
                    table_type="EXTERNAL_TABLE",
                    parameters={"classification": "parquet"},
                    storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                        location=f"s3://{self.data_bucket.bucket_name}/{s3_prefix}/{table_name}/",
                        input_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                        output_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                        serde_info=glue.CfnTable.SerdeInfoProperty(
                            serialization_library="org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
                        ),
                        columns=columns,
                    ),
                ),
            )
            table.add_dependency(self.glue_database)

    def _create_guardrails(self):
        """Create Amazon Bedrock Guardrails for content filtering."""
        self.guardrail = bedrock.CfnGuardrail(
            self, "ContentGuardrail",
            name=f"{self.project_name}-content-guardrail",
            description="Guardrail to filter inappropriate content in queries",
            blocked_input_messaging="Sorry, your query contains content that cannot be processed. Please rephrase your question focusing on data queries.",
            blocked_outputs_messaging="Sorry, I cannot generate that response. Please try a different query.",
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type=t, input_strength="HIGH", output_strength="HIGH"
                    )
                    for t in ["HATE", "SEXUAL", "VIOLENCE", "MISCONDUCT", "INSULTS"]
                ]
            ),
            topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(
                topics_config=[
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="Politics", type="DENY",
                        definition="Discussions about political parties, elections, or political ideologies.",
                        examples=[
                            "What do you think about the president?",
                            "Which is the best political party?",
                        ],
                    ),
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="Religion", type="DENY",
                        definition="Discussions about specific religions or theological debates.",
                        examples=[
                            "Which is the best religion?",
                            "Does God exist?",
                        ],
                    ),
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="Violence", type="DENY",
                        definition="Discussions about violence, weapons, or illegal activities.",
                        examples=[
                            "How to make a bomb?",
                            "How to get weapons?",
                        ],
                    ),
                ],
            ),
            word_policy_config=bedrock.CfnGuardrail.WordPolicyConfigProperty(
                managed_word_lists_config=[
                    bedrock.CfnGuardrail.ManagedWordsConfigProperty(type="PROFANITY")
                ]
            ),
        )
        self.guardrail_version = bedrock.CfnGuardrailVersion(
            self, "GuardrailVersion",
            guardrail_identifier=self.guardrail.attr_guardrail_id,
            description="Version 1",
        )

    def _create_backend(self):
        """Create Lambda + API Gateway."""
        lambda_role = iam.Role(
            self, "LambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )
        # Permissions for Glue, Athena, S3, Amazon Bedrock
        for actions, resources in [
            (
                ["glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables"],
                ["*"],
            ),
            (
                [
                    "athena:StartQueryExecution", "athena:GetQueryExecution",
                    "athena:GetQueryResults", "athena:StopQueryExecution",
                ],
                ["*"],
            ),
            (
                ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"],
                [
                    self.data_bucket.bucket_arn,
                    f"{self.data_bucket.bucket_arn}/*",
                    self.athena_results_bucket.bucket_arn,
                    f"{self.athena_results_bucket.bucket_arn}/*",
                ],
            ),
            (
                ["bedrock:InvokeModel"],
                ["arn:aws:bedrock:*::foundation-model/anthropic.claude-*"],
            ),
            (
                ["bedrock:ApplyGuardrail", "bedrock:GetGuardrail"],
                [self.guardrail.attr_guardrail_arn],
            ),
        ]:
            lambda_role.add_to_policy(
                iam.PolicyStatement(actions=actions, resources=resources)
            )

        self.backend_lambda = lambda_.Function(
            self, "BackendLambda",
            function_name=f"{self.project_name}-text-to-sql-api",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="lambda_handler.handler",
            code=lambda_.Code.from_asset("../lambda_package"),
            role=lambda_role,
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={
                "GLUE_DATABASE_NAME": self.database_name,
                "ATHENA_OUTPUT_LOCATION": f"s3://{self.athena_results_bucket.bucket_name}/results/",
                "PROJECT_NAME": self.project_name,
                "GUARDRAIL_ID": self.guardrail.attr_guardrail_id,
                "GUARDRAIL_VERSION": "DRAFT",
            },
        )

        self.api = apigw.RestApi(
            self, "TextToSQLApi",
            rest_api_name=f"{self.project_name}-text-to-sql-api",
            description="Text-to-SQL GenAI API",
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=apigw.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization"],
            ),
        )
        api_resource = self.api.root.add_resource("api")
        for path in ["query", "examples", "health"]:
            method = "POST" if path == "query" else "GET"
            api_resource.add_resource(path).add_method(
                method, apigw.LambdaIntegration(self.backend_lambda)
            )

    def _create_frontend(self):
        """Create S3 + CloudFront for the frontend."""
        self.frontend_bucket = s3.Bucket(
            self, "FrontendBucket",
            bucket_name=f"{self.project_name}-text-to-sql-frontend",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )
        oai = cloudfront.OriginAccessIdentity(
            self, "OAI", comment=f"OAI for {self.project_name}"
        )
        self.frontend_bucket.grant_read(oai)

        self.distribution = cloudfront.Distribution(
            self, "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(
                    self.frontend_bucket, origin_access_identity=oai
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            ),
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=origins.RestApiOrigin(self.api),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                )
            },
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                )
            ],
        )
        s3_deploy.BucketDeployment(
            self, "DeployFrontend",
            sources=[s3_deploy.Source.asset("../frontend")],
            destination_bucket=self.frontend_bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
        )

    def _create_outputs(self):
        CfnOutput(
            self, "FrontendURL",
            value=f"https://{self.distribution.distribution_domain_name}",
            description="Frontend URL",
        )
        CfnOutput(self, "ApiURL", value=self.api.url, description="API Gateway URL")
        CfnOutput(
            self, "DataBucket",
            value=self.data_bucket.bucket_name,
            description="S3 Data Lake bucket",
        )
        CfnOutput(
            self, "GlueDatabase",
            value=self.database_name,
            description="Glue Database name",
        )
        CfnOutput(
            self, "GuardrailId",
            value=self.guardrail.attr_guardrail_id,
            description="Amazon Bedrock Guardrail ID",
        )
