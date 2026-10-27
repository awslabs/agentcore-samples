from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    CfnParameter,
    CustomResource,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_dynamodb as dynamodb,
    aws_ecr as ecr,
    aws_codebuild as codebuild,
    aws_s3 as s3,
    aws_s3_assets as s3_assets,
    aws_apigateway as apigateway,
    aws_cognito as cognito,
    aws_secretsmanager as secretsmanager,
    aws_bedrockagentcore as bedrockagentcore,
)
from constructs import Construct
from infra_utils.agentcore_role import AgentCoreRole
import boto3


class AgentcoreGatewayWithAuthStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Parameters
        agent_name = CfnParameter(self, "AgentName",
            type="String",
            default="TicketAuthAgent",
            description="Name for the agent runtime"
        )

        image_tag = CfnParameter(self, "ImageTag",
            type="String",
            default="latest",
            description="Tag for the Docker image"
        )

        network_mode = CfnParameter(self, "NetworkMode",
            type="String",
            default="PUBLIC",
            description="Network mode for AgentCore resources",
            allowed_values=["PUBLIC", "PRIVATE"]
        )

        # DynamoDB Table
        ticket_table = dynamodb.Table(self, "TicketTable",
            table_name="tickets-auth-demo",
            partition_key=dynamodb.Attribute(name="RequestId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY
        )

        # Lambda execution role
        lambda_role = iam.Role(self, "LambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ]
        )
        ticket_table.grant_read_write_data(lambda_role)

        # Lambda Functions
        lambda_functions = {}
        
        lambda_functions['create'] = lambda_.Function(self, "CreateTicketLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="create_ticket.handler",
            code=lambda_.Code.from_asset("lambda-functions"),
            role=lambda_role,
            environment={"TABLE_NAME": ticket_table.table_name},
            timeout=Duration.seconds(30)
        )
        
        lambda_functions['get_all'] = lambda_.Function(self, "GetAllTicketsLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="get_all_tickets.handler",
            code=lambda_.Code.from_asset("lambda-functions"),
            role=lambda_role,
            environment={"TABLE_NAME": ticket_table.table_name},
            timeout=Duration.seconds(30)
        )
        
        lambda_functions['get'] = lambda_.Function(self, "GetTicketLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="get_ticket.handler",
            code=lambda_.Code.from_asset("lambda-functions"),
            role=lambda_role,
            environment={"TABLE_NAME": ticket_table.table_name},
            timeout=Duration.seconds(30)
        )
        
        lambda_functions['update'] = lambda_.Function(self, "UpdateTicketLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="update_ticket.handler",
            code=lambda_.Code.from_asset("lambda-functions"),
            role=lambda_role,
            environment={"TABLE_NAME": ticket_table.table_name},
            timeout=Duration.seconds(30)
        )

        # ECR Repository
        ecr_repository = ecr.Repository(self, "ECRRepository",
            repository_name=f"{self.stack_name.lower()}-ticket-agent",
            image_tag_mutability=ecr.TagMutability.MUTABLE,
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
            image_scan_on_push=True
        )

        # S3 Asset for agent source code
        source_asset = s3_assets.Asset(self, "SourceAsset",
            path="./agent-code"
        )

        # CodeBuild Service Role
        codebuild_role = iam.Role(self, "CodeBuildRole",
            role_name=f"{self.stack_name}-codebuild-role",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
            inline_policies={
                "CodeBuildPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="CloudWatchLogs",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents"
                            ],
                            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/codebuild/*"]
                        ),
                        iam.PolicyStatement(
                            sid="ECRAccess",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "ecr:BatchCheckLayerAvailability",
                                "ecr:GetDownloadUrlForLayer",
                                "ecr:BatchGetImage",
                                "ecr:GetAuthorizationToken",
                                "ecr:PutImage",
                                "ecr:InitiateLayerUpload",
                                "ecr:UploadLayerPart",
                                "ecr:CompleteLayerUpload"
                            ],
                            resources=[ecr_repository.repository_arn, "*"]
                        ),
                        iam.PolicyStatement(
                            sid="S3Access",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "s3:GetObject",
                                "s3:GetObjectVersion"
                            ],
                            resources=[f"{source_asset.bucket.bucket_arn}/*"]
                        )
                    ]
                )
            }
        )

        # CodeBuild Project
        build_project = codebuild.Project(self, "ImageBuildProject",
            project_name=f"{self.stack_name}-agent-build",
            description=f"Build ticket agent Docker image for {self.stack_name}",
            role=codebuild_role,
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxArmBuildImage.AMAZON_LINUX_2_STANDARD_3_0,
                compute_type=codebuild.ComputeType.LARGE,
                privileged=True
            ),
            source=codebuild.Source.s3(
                bucket=source_asset.bucket,
                path=source_asset.s3_object_key
            ),
            environment_variables={
                "AWS_DEFAULT_REGION": codebuild.BuildEnvironmentVariable(value=self.region),
                "AWS_ACCOUNT_ID": codebuild.BuildEnvironmentVariable(value=self.account),
                "IMAGE_REPO_NAME": codebuild.BuildEnvironmentVariable(value=ecr_repository.repository_name),
                "IMAGE_TAG": codebuild.BuildEnvironmentVariable(value=image_tag.value_as_string)
            },
            build_spec=codebuild.BuildSpec.from_object({
                "version": "0.2",
                "phases": {
                    "pre_build": {
                        "commands": [
                            "echo Logging in to Amazon ECR...",
                            "aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com"
                        ]
                    },
                    "build": {
                        "commands": [
                            "echo Building Docker image...",
                            "docker build -t $IMAGE_REPO_NAME:$IMAGE_TAG .",
                            "docker tag $IMAGE_REPO_NAME:$IMAGE_TAG $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME:$IMAGE_TAG"
                        ]
                    },
                    "post_build": {
                        "commands": [
                            "echo Pushing Docker image...",
                            "docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME:$IMAGE_TAG"
                        ]
                    }
                }
            })
        )

        # Build Trigger Role
        build_trigger_role = iam.Role(self, "BuildTriggerRole",
            role_name=f"{self.stack_name}-build-trigger-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
            inline_policies={
                "CodeBuildAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="CodeBuildAccess",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "codebuild:StartBuild",
                                "codebuild:BatchGetBuilds",
                                "codebuild:BatchGetProjects"
                            ],
                            resources=[build_project.project_arn]
                        )
                    ]
                )
            }
        )
        
        build_trigger_lambda = lambda_.Function(self, "BuildTriggerLambda",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="build_trigger_lambda.handler",
            code=lambda_.Code.from_asset("./infra_utils"),
            timeout=Duration.minutes(15),
            role=build_trigger_role
        )
        
        trigger_build = CustomResource(self, "TriggerImageBuild",
            service_token=build_trigger_lambda.function_arn,
            service_timeout=Duration.minutes(10),
            properties={
                "ProjectName": build_project.project_name
            }
        )
        trigger_build.node.add_dependency(ecr_repository)
        trigger_build.node.add_dependency(build_project)

        # Gateway IAM Role
        gateway_role = iam.Role(self, "GatewayRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaRole")
            ],
            inline_policies={
                "OAuthWorkloadIdentityAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="WorkloadIdentityTokenAccess",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock-agentcore:GetWorkloadAccessToken",
                                "bedrock-agentcore:GetResourceOauth2Token"
                            ],
                            resources=["*"]
                        ),
                        iam.PolicyStatement(
                            sid="SecretsManagerAccess",
                            effect=iam.Effect.ALLOW,
                            actions=["secretsmanager:GetSecretValue"],
                            resources=["*"]
                        )
                    ]
                )
            }
        )

        # Cognito User Pool
        cognito_user_pool = cognito.UserPool(self, "TicketUserPool",
            user_pool_name="ticket-auth-pool",
            removal_policy=RemovalPolicy.DESTROY
        )
        
        read_scope = cognito.ResourceServerScope(
            scope_name="ticket-read",
            scope_description="Read access to tickets"
        )
        
        write_scope = cognito.ResourceServerScope(
            scope_name="ticket-write",
            scope_description="Write access to tickets"
        )
        
        resource_server = cognito_user_pool.add_resource_server("TicketResourceServer",
            identifier="agentcore-gateway",
            scopes=[read_scope, write_scope]
        )
        
        cognito_domain = cognito.UserPoolDomain(self, "TicketDomain",
            user_pool=cognito_user_pool,
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"ticket-auth-{self.stack_name.lower()}"
            )
        )
        
        cognito_client = cognito_user_pool.add_client("TicketClient",
            generate_secret=True,
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    client_credentials=True
                ),
                scopes=[
                    cognito.OAuthScope.resource_server(resource_server, read_scope),
                    cognito.OAuthScope.resource_server(resource_server, write_scope)
                ]
            )
        )
        
        cognito_secret = secretsmanager.Secret(self, "CognitoClientSecret",
            description="Cognito User Pool Client Secret for OAuth",
            secret_string_value=cognito_client.user_pool_client_secret
        )

        # OAuth Provider Role
        oauth_provider_role = iam.Role(self, "OAuthProviderRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
            inline_policies={
                "BedrockAgentCoreAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="BedrockAgentCoreControl",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock-agentcore:CreateOauth2CredentialProvider",
                                "bedrock-agentcore:DeleteOauth2CredentialProvider",
                                "bedrock-agentcore:CreateTokenVault",
                                "secretsmanager:CreateSecret",
                                "secretsmanager:DeleteSecret",
                                "cognito-idp:DescribeUserPool",
                                "cognito-idp:DescribeUserPoolDomain",
                                "cognito-idp:GetUserPoolMfaConfig"
                            ],
                            resources=["*"]
                        )
                    ]
                )
            }
        )
        
        oauth_provider_lambda = lambda_.Function(self, "OAuthProviderLambda",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="oauth_provider_lambda.handler",
            code=lambda_.Code.from_asset("./infra_utils"),
            timeout=Duration.minutes(5),
            role=oauth_provider_role
        )
        
        cognito_secret.grant_read(oauth_provider_lambda)
        
        oauth_custom_resource = CustomResource(self, "OAuthProviderResource",
            service_token=oauth_provider_lambda.function_arn,
            service_timeout=Duration.minutes(10),
            properties={
                "ClientId": cognito_client.user_pool_client_id,
                "SecretArn": cognito_secret.secret_arn,
                "UserPoolId": cognito_user_pool.user_pool_id
            }
        )
        oauth_custom_resource.node.add_dependency(cognito_client)

        # AgentCore Gateway
        gateway = bedrockagentcore.CfnGateway(self, "TicketGateway",
            name="ticket-auth-gateway",
            authorizer_type="AWS_IAM",
            protocol_type="MCP",
            role_arn=gateway_role.role_arn,
            protocol_configuration={
                "mcp": {
                    "instructions": "Ticket management system with dual authentication",
                    "searchType": "SEMANTIC",
                    "supportedVersions": ["2025-03-26"]
                }
            }
        )

        # Gateway Targets
        targets = {}
        
        # IAM Auth Target: create_ticket
        targets['create'] = bedrockagentcore.CfnGatewayTarget(self, "CreateTicketTarget",
            name="create-ticket-target",
            gateway_identifier=gateway.attr_gateway_identifier,
            credential_provider_configurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
            target_configuration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": lambda_functions['create'].function_arn,
                        "toolSchema": {
                            "inlinePayload": [{
                                "name": "create_ticket",
                                "description": "Create a new support ticket",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "user_id": {"type": "string", "description": "User identifier"},
                                        "description": {"type": "string", "description": "Ticket description"},
                                        "comment": {"type": "string", "description": "Optional comment"}
                                    },
                                    "required": ["user_id", "description"]
                                }
                            }]
                        }
                    }
                }
            }
        )
        
        # IAM Auth Target: get_all_tickets
        targets['get_all'] = bedrockagentcore.CfnGatewayTarget(self, "GetAllTicketsTarget",
            name="get-all-tickets-target",
            gateway_identifier=gateway.attr_gateway_identifier,
            credential_provider_configurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
            target_configuration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": lambda_functions['get_all'].function_arn,
                        "toolSchema": {
                            "inlinePayload": [{
                                "name": "get_all_tickets",
                                "description": "Get all tickets for a user with optional status filtering",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "user_id": {
                                            "type": "string",
                                            "description": "User identifier"
                                        },
                                        "status_filter": {
                                            "type": "string",
                                            "description": "Optional status filter (ApprovalStatus or ImplementationStatus)",
                                            "enum": ["PENDING", "APPROVED", "REJECTED", "NOT_STARTED", "IN_PROGRESS", "COMPLETED"]
                                        }
                                    },
                                    "required": ["user_id"]
                                }
                            }]
                        }
                    }
                }
            }
        )

        # Grant Gateway permission to invoke IAM Lambda functions
        lambda_functions['create'].add_permission("GatewayInvokeCreate",
            principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            action="lambda:InvokeFunction"
        )
        
        lambda_functions['get_all'].add_permission("GatewayInvokeGetAll",
            principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            action="lambda:InvokeFunction"
        )

        # S3 Bucket for YAML storage
        yaml_bucket = s3.Bucket(self, "YAMLBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        # YAML Processor Lambda
        yaml_processor_lambda = lambda_.Function(self, "YAMLProcessorLambda",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="yaml_processor_lambda.handler",
            code=lambda_.Code.from_asset("./infra_utils"),
            timeout=Duration.minutes(5)
        )
        yaml_bucket.grant_read_write(yaml_processor_lambda)
        
        with open("./api-gateway/tickets_api.yaml", 'r') as f:
            template_content = f.read()
        
        yaml_processor = CustomResource(self, "ProcessYAMLResource",
            service_token=yaml_processor_lambda.function_arn,
            service_timeout=Duration.minutes(10),
            properties={
                "GetTicketLambdaArn": f"arn:aws:apigateway:{self.region}:lambda:path/2015-03-31/functions/{lambda_functions['get'].function_arn}/invocations",
                "UpdateTicketLambdaArn": f"arn:aws:apigateway:{self.region}:lambda:path/2015-03-31/functions/{lambda_functions['update'].function_arn}/invocations",
                "UserPoolArn": cognito_user_pool.user_pool_arn,
                "S3Bucket": yaml_bucket.bucket_name,
                "TemplateContent": template_content
            }
        )
        
        processed_yaml_uri = yaml_processor.get_att_string("ProcessedYAMLUri")
        processed_yaml_key = yaml_processor.get_att_string("S3Key")

        # API Gateway
        tickets_api = apigateway.SpecRestApi(self, "TicketsAPI",
            api_definition=apigateway.ApiDefinition.from_bucket(
                bucket=yaml_bucket,
                key=processed_yaml_key
            ),
            deploy_options=apigateway.StageOptions(
                stage_name="v1",
                tracing_enabled=True
            )
        )
        
        api_gateway_url = tickets_api.url

        # Grant API Gateway permission to invoke Lambda
        lambda_functions['get'].add_permission("APIGatewayInvokeGet",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=f"arn:aws:execute-api:{self.region}:{self.account}:{tickets_api.rest_api_id}/*/*"
        )
        
        lambda_functions['update'].add_permission("APIGatewayInvokeUpdate",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=f"arn:aws:execute-api:{self.region}:{self.account}:{tickets_api.rest_api_id}/*/*"
        )

        # URL Updater Lambda
        url_updater_lambda = lambda_.Function(self, "URLUpdaterLambda",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="url_updater_lambda.handler",
            code=lambda_.Code.from_asset("./infra_utils"),
            timeout=Duration.minutes(5)
        )
        yaml_bucket.grant_read_write(url_updater_lambda)
        
        url_updater = CustomResource(self, "URLUpdaterResource",
            service_token=url_updater_lambda.function_arn,
            service_timeout=Duration.minutes(10),
            properties={
                "S3Bucket": yaml_bucket.bucket_name,
                "S3Key": processed_yaml_key,
                "APIGatewayUrl": api_gateway_url
            }
        )
        
        # OAuth Auth Target: get_ticket
        targets['get'] = bedrockagentcore.CfnGatewayTarget(self, "GetTicketTarget",
            name="get-ticket-target",
            gateway_identifier=gateway.attr_gateway_identifier,
            credential_provider_configurations=[{
                "credentialProviderType": "OAUTH",
                "credentialProvider": {
                    "oauthCredentialProvider": {
                        "providerArn": oauth_custom_resource.get_att_string("ProviderArn"),
                        "scopes": ["agentcore-gateway/ticket-read"]
                    }
                }
            }],
            target_configuration={
                "mcp": {
                    "openApiSchema": {
                        "s3": {
                            "uri": processed_yaml_uri,
                            "bucketOwnerAccountId": self.account
                        }
                    }
                }
            }
        )
        targets['get'].node.add_dependency(url_updater)
        
        # OAuth Auth Target: update_ticket
        targets['update'] = bedrockagentcore.CfnGatewayTarget(self, "UpdateTicketTarget",
            name="update-ticket-target",
            gateway_identifier=gateway.attr_gateway_identifier,
            credential_provider_configurations=[{
                "credentialProviderType": "OAUTH",
                "credentialProvider": {
                    "oauthCredentialProvider": {
                        "providerArn": oauth_custom_resource.get_att_string("ProviderArn"),
                        "scopes": ["agentcore-gateway/ticket-write"]
                    }
                }
            }],
            target_configuration={
                "mcp": {
                    "openApiSchema": {
                        "s3": {
                            "uri": processed_yaml_uri,
                            "bucketOwnerAccountId": self.account
                        }
                    }
                }
            }
        )
        targets['update'].node.add_dependency(url_updater)


        agent_execution_role = AgentCoreRole(self, "AgentExecutionRole")

        # Create AgentCore Memory
        agent_memory = bedrockagentcore.CfnMemory(self, "TicketAgentMemory",
            name="TicketAgentMemory",
            event_expiry_duration=123,
            memory_execution_role_arn=agent_execution_role.role_arn,
            description="Memory for ticket management agent",
            memory_strategies=[
                bedrockagentcore.CfnMemory.MemoryStrategyProperty(
                    semantic_memory_strategy=bedrockagentcore.CfnMemory.SemanticMemoryStrategyProperty(
                        name="TicketContext",
                        namespaces=["tickets/{actorId}/context/"]
                    )
                ),
                bedrockagentcore.CfnMemory.MemoryStrategyProperty(
                    user_preference_memory_strategy=bedrockagentcore.CfnMemory.UserPreferenceMemoryStrategyProperty(
                        name="UserPreferences",
                        namespaces=["tickets/{actorId}/preferences/"]
                    )
                )
            ]
        )
        
        memory_id = agent_memory.attr_memory_id
        
        # Add Gateway access permissions
        agent_execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="GatewayAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:InvokeGateway",
                    "bedrock-agentcore:ListGateways",
                    "bedrock-agentcore:GetGateway"
                ],
                resources=[gateway.attr_gateway_arn]
            )
        )
        
        # Add Memory access permissions
        agent_execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="MemoryAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:GetLastKTurns",
                    "bedrock-agentcore:RetrieveMemories",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:StoreMemoryRecords",
                    "bedrock-agentcore:ListEvents"
                ],
                resources=[agent_memory.attr_memory_arn]
            )
        )

        # AgentCore Runtime
        agent_runtime = bedrockagentcore.CfnRuntime(self, "TicketAgentRuntime",
            agent_runtime_name=f"{self.stack_name.replace('-', '_')}_{agent_name.value_as_string}",
            agent_runtime_artifact=bedrockagentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=bedrockagentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=f"{ecr_repository.repository_uri}:{image_tag.value_as_string}"
                )
            ),
            role_arn=agent_execution_role.role_arn,
            network_configuration=bedrockagentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode=network_mode.value_as_string
            ),
            protocol_configuration="HTTP",
            description=f"Ticket agent runtime for {self.stack_name}",
            environment_variables={
                "AWS_DEFAULT_REGION": self.region,
                "GATEWAY_URL": gateway.attr_gateway_url,
                "AGENT_MODEL_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
                "MEMORY_ID": memory_id
            }
        )
        agent_runtime.node.add_dependency(trigger_build)
        agent_runtime.node.add_dependency(gateway)

        # Outputs
        CfnOutput(self, "AgentRuntimeId",
            value=agent_runtime.attr_agent_runtime_id,
            export_name=f"{self.stack_name}-AgentRuntimeId"
        )

        CfnOutput(self, "AgentRuntimeArn",
            value=agent_runtime.attr_agent_runtime_arn,
            description="Full ARN of the AgentCore Runtime for testing",
            export_name=f"{self.stack_name}-AgentRuntimeArn"
        )

        CfnOutput(self, "GatewayUrl",
            value=gateway.attr_gateway_url,
            export_name=f"{self.stack_name}-GatewayUrl"
        )

        CfnOutput(self, "DynamoDBTableName",
            value=ticket_table.table_name,
            export_name=f"{self.stack_name}-DynamoDBTableName"
        )

        CfnOutput(self, "APIGatewayUrl",
            value=tickets_api.url,
            export_name=f"{self.stack_name}-APIGatewayUrl"
        )

        CfnOutput(self, "CognitoUserPoolId",
            value=cognito_user_pool.user_pool_id,
            export_name=f"{self.stack_name}-CognitoUserPoolId"
        )

        CfnOutput(self, "CognitoClientId",
            value=cognito_client.user_pool_client_id,
            export_name=f"{self.stack_name}-CognitoClientId"
        )

        CfnOutput(self, "MemoryId",
            description="AgentCore Memory ID for conversation context",
            value=memory_id,
            export_name=f"{self.stack_name}-MemoryId"
        )
