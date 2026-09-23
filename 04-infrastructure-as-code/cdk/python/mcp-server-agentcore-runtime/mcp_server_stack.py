from aws_cdk import (
    Stack,
    aws_ecr as ecr,
    aws_codebuild as codebuild,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_cognito as cognito,
    aws_bedrockagentcore as bedrockagentcore,
    CustomResource,
    CfnParameter,
    CfnOutput,
    Duration,
    RemovalPolicy,
)
from constructs import Construct
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "infra_utils"))


class MCPServerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Parameters
        agent_name = CfnParameter(
            self,
            "AgentName",
            type="String",
            default="MCPServerAgent",
            description="Name for the MCP server runtime",
        )

        image_tag = CfnParameter(
            self,
            "ImageTag",
            type="String",
            default="latest",
            description="Tag for the Docker image",
        )

        network_mode = CfnParameter(
            self,
            "NetworkMode",
            type="String",
            default="PUBLIC",
            description="Network mode for AgentCore resources",
            allowed_values=["PUBLIC", "PRIVATE"],
        )

        ecr_repository_name = CfnParameter(
            self,
            "ECRRepositoryName",
            type="String",
            default="mcp-server",
            description="Name of the ECR repository",
        )

        # ECR Repository
        ecr_repository = ecr.Repository(
            self,
            "ECRRepository",
            repository_name=f"{self.stack_name.lower()}-{ecr_repository_name.value_as_string}",
            image_tag_mutability=ecr.TagMutability.MUTABLE,
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
            image_scan_on_push=True,
        )

        # Cognito User Pool
        cognito_user_pool = cognito.UserPool(
            self,
            "CognitoUserPool",
            user_pool_name=f"{self.stack_name}-user-pool",
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_uppercase=False,
                require_lowercase=False,
                require_digits=False,
                require_symbols=False,
            ),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=False, mutable=True)
            ),
        )

        # Cognito User Pool Client
        cognito_user_pool_client = cognito.CfnUserPoolClient(
            self,
            "CognitoUserPoolClient",
            client_name=f"{self.stack_name}-client",
            user_pool_id=cognito_user_pool.user_pool_id,
            generate_secret=False,
            explicit_auth_flows=[
                "ALLOW_USER_PASSWORD_AUTH",
                "ALLOW_REFRESH_TOKEN_AUTH",
            ],
            prevent_user_existence_errors="ENABLED",
        )

        # Cognito User
        cognito_user = cognito.CfnUserPoolUser(
            self,
            "CognitoUser",
            user_pool_id=cognito_user_pool.user_pool_id,
            username="testuser",
            message_action="SUPPRESS",
        )
        # IAM Roles
        # Agent Execution Role
        agent_execution_role = iam.Role(
            self,
            "AgentExecutionRole",
            role_name=f"{self.stack_name}-agent-execution-role",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("BedrockAgentCoreFullAccess")],
            inline_policies={
                "AgentCoreExecutionPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="ECRImageAccess",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "ecr:BatchGetImage",
                                "ecr:GetDownloadUrlForLayer",
                                "ecr:BatchCheckLayerAvailability",
                            ],
                            resources=[ecr_repository.repository_arn],
                        ),
                        iam.PolicyStatement(
                            sid="ECRTokenAccess",
                            effect=iam.Effect.ALLOW,
                            actions=["ecr:GetAuthorizationToken"],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            sid="CloudWatchLogs",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "logs:DescribeLogStreams",
                                "logs:CreateLogGroup",
                                "logs:DescribeLogGroups",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                            ],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            sid="XRayTracing",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "xray:PutTraceSegments",
                                "xray:PutTelemetryRecords",
                                "xray:GetSamplingRules",
                                "xray:GetSamplingTargets",
                            ],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            sid="CloudWatchMetrics",
                            effect=iam.Effect.ALLOW,
                            actions=["cloudwatch:PutMetricData"],
                            resources=["*"],
                            conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
                        ),
                    ]
                )
            },
        )

        # CodeBuild Service Role
        codebuild_role = iam.Role(
            self,
            "CodeBuildRole",
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
                                "logs:PutLogEvents",
                            ],
                            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/codebuild/*"],
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
                                "ecr:CompleteLayerUpload",
                            ],
                            resources=[ecr_repository.repository_arn, "*"],
                        ),
                    ]
                )
            },
        )

        # Lambda Custom Resource Role
        custom_resource_role = iam.Role(
            self,
            "CustomResourceRole",
            role_name=f"{self.stack_name}-custom-resource-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
            inline_policies={
                "CustomResourcePolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="CodeBuildAccess",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "codebuild:StartBuild",
                                "codebuild:BatchGetBuilds",
                                "codebuild:BatchGetProjects",
                            ],
                            resources=["*"],  # Will be updated after CodeBuild project is created
                        ),
                        iam.PolicyStatement(
                            sid="CognitoAccess",
                            effect=iam.Effect.ALLOW,
                            actions=["cognito-idp:AdminSetUserPassword"],
                            resources=[cognito_user_pool.user_pool_arn],
                        ),
                    ]
                )
            },
        )
        # Lambda Functions
        # CodeBuild Trigger Function
        codebuild_trigger_function = lambda_.Function(
            self,
            "CodeBuildTriggerFunction",
            function_name=f"{self.stack_name}-codebuild-trigger",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="build_trigger_lambda.handler",
            code=lambda_.Code.from_asset(os.path.join(os.path.dirname(__file__), "infra_utils")),
            timeout=Duration.minutes(15),
            role=custom_resource_role,
            description="Triggers CodeBuild projects as CloudFormation custom resource",
        )

        # Cognito Password Setter Function
        cognito_password_setter_function = lambda_.Function(
            self,
            "CognitoPasswordSetterFunction",
            function_name=f"{self.stack_name}-cognito-password-setter",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index.handler",
            timeout=Duration.minutes(5),
            role=custom_resource_role,
            description="Sets Cognito user password",
            code=lambda_.Code.from_inline("""
import boto3
import cfnresponse
import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def handler(event, context):
    logger.info('Received event: %s', json.dumps(event))

    try:
        if event['RequestType'] == 'Delete':
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            return

        user_pool_id = event['ResourceProperties']['UserPoolId']
        username = event['ResourceProperties']['Username']
        password = event['ResourceProperties']['Password']

        cognito = boto3.client('cognito-idp')

        # Set permanent password
        cognito.admin_set_user_password(
            UserPoolId=user_pool_id,
            Username=username,
            Password=password,
            Permanent=True
        )

        logger.info(f"Password set successfully for user: {username}")

        cfnresponse.send(event, context, cfnresponse.SUCCESS, {
            'Status': 'SUCCESS'
        })

    except Exception as e:
        logger.error('Error: %s', str(e))
        cfnresponse.send(event, context, cfnresponse.FAILED, {
            'Error': str(e)
        })
            """),
        )
        # CodeBuild Project for MCP Server
        mcp_server_build_project = codebuild.Project(
            self,
            "MCPServerImageBuildProject",
            project_name=f"{self.stack_name}-mcp-server-build",
            description=f"Build MCP server Docker image for {self.stack_name}",
            role=codebuild_role,
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxArmBuildImage.AMAZON_LINUX_2_STANDARD_3_0,
                compute_type=codebuild.ComputeType.LARGE,
                privileged=True,
            ),
            environment_variables={
                "AWS_DEFAULT_REGION": codebuild.BuildEnvironmentVariable(value=self.region),
                "AWS_ACCOUNT_ID": codebuild.BuildEnvironmentVariable(value=self.account),
                "IMAGE_REPO_NAME": codebuild.BuildEnvironmentVariable(value=ecr_repository.repository_name),
                "IMAGE_TAG": codebuild.BuildEnvironmentVariable(value=image_tag.value_as_string),
                "STACK_NAME": codebuild.BuildEnvironmentVariable(value=self.stack_name),
            },
            build_spec=codebuild.BuildSpec.from_object(
                {
                    "version": "0.2",
                    "phases": {
                        "pre_build": {
                            "commands": [
                                "echo Logging in to Amazon ECR...",
                                "aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com",
                            ]
                        },
                        "build": {
                            "commands": [
                                "echo Build started on `date`",
                                "echo Building the Docker image for MCP server ARM64...",
                                # Create requirements.txt
                                """cat > requirements.txt << 'EOF'
mcp>=1.10.0
boto3
bedrock-agentcore
EOF""",
                                # Create mcp_server.py
                                """cat > mcp_server.py << 'EOF'
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

mcp = FastMCP(host="0.0.0.0", stateless_http=True)

@mcp.tool()
def add_numbers(a: int, b: int) -> int:
    \"\"\"Add two numbers together\"\"\"
    return a + b

@mcp.tool()
def multiply_numbers(a: int, b: int) -> int:
    \"\"\"Multiply two numbers together\"\"\"
    return a * b

@mcp.tool()
def greet_user(name: str) -> str:
    \"\"\"Greet a user by name\"\"\"
    return f"Hello, {name}! Nice to meet you."

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
EOF""",
                                # Create Dockerfile
                                """cat > Dockerfile << 'EOF'
FROM public.ecr.aws/docker/library/python:3.11-slim
WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

ENV AWS_REGION=us-west-2
ENV AWS_DEFAULT_REGION=us-west-2

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8000

COPY . .

CMD ["python", "-m", "mcp_server"]
EOF""",
                                "echo Building ARM64 image...",
                                "docker build -t $IMAGE_REPO_NAME:$IMAGE_TAG .",
                                "docker tag $IMAGE_REPO_NAME:$IMAGE_TAG $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME:$IMAGE_TAG",
                            ]
                        },
                        "post_build": {
                            "commands": [
                                "echo Build completed on `date`",
                                "echo Pushing the Docker image...",
                                "docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME:$IMAGE_TAG",
                                "echo ARM64 Docker image pushed successfully",
                            ]
                        },
                    },
                }
            ),
        )
        # Custom Resources
        # Set Cognito User Password
        set_cognito_user_password = CustomResource(
            self,
            "SetCognitoUserPassword",
            service_token=cognito_password_setter_function.function_arn,
            properties={
                "UserPoolId": cognito_user_pool.user_pool_id,
                "Username": "testuser",
                "Password": "MyPassword123!",
            },
        )
        set_cognito_user_password.node.add_dependency(cognito_user)

        # Trigger Image Build
        trigger_image_build = CustomResource(
            self,
            "TriggerImageBuild",
            service_token=codebuild_trigger_function.function_arn,
            properties={
                "ProjectName": mcp_server_build_project.project_name,
                "WaitForCompletion": "true",
            },
        )
        trigger_image_build.node.add_dependency(ecr_repository)
        trigger_image_build.node.add_dependency(mcp_server_build_project)

        # MCP Server Runtime
        mcp_server_runtime = bedrockagentcore.CfnRuntime(
            self,
            "MCPServerRuntime",
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
            protocol_configuration="MCP",
            authorizer_configuration=bedrockagentcore.CfnRuntime.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=bedrockagentcore.CfnRuntime.CustomJWTAuthorizerConfigurationProperty(
                    allowed_clients=[cognito_user_pool_client.ref],
                    discovery_url=f"https://cognito-idp.{self.region}.amazonaws.com/{cognito_user_pool.user_pool_id}/.well-known/openid-configuration",
                )
            ),
            description=f"MCP server runtime for {self.stack_name}",
        )
        mcp_server_runtime.node.add_dependency(trigger_image_build)
        # Outputs
        CfnOutput(
            self,
            "MCPServerRuntimeId",
            description="ID of the created MCP server runtime",
            value=mcp_server_runtime.attr_agent_runtime_id,
            export_name=f"{self.stack_name}-MCPServerRuntimeId",
        )

        CfnOutput(
            self,
            "MCPServerRuntimeArn",
            description="ARN of the created MCP server runtime",
            value=mcp_server_runtime.attr_agent_runtime_arn,
            export_name=f"{self.stack_name}-MCPServerRuntimeArn",
        )

        CfnOutput(
            self,
            "MCPServerInvocationURL",
            description="URL to invoke the MCP server",
            value=f"https://bedrock-agentcore.{self.region}.amazonaws.com/runtimes/ENCODED_ARN/invocations?qualifier=DEFAULT",
        )

        CfnOutput(
            self,
            "ECRRepositoryUri",
            description="URI of the ECR repository",
            value=ecr_repository.repository_uri,
            export_name=f"{self.stack_name}-ECRRepositoryUri",
        )

        CfnOutput(
            self,
            "AgentExecutionRoleArn",
            description="ARN of the agent execution role",
            value=agent_execution_role.role_arn,
            export_name=f"{self.stack_name}-AgentExecutionRoleArn",
        )

        CfnOutput(
            self,
            "CognitoUserPoolId",
            description="ID of the Cognito User Pool",
            value=cognito_user_pool.user_pool_id,
            export_name=f"{self.stack_name}-CognitoUserPoolId",
        )

        CfnOutput(
            self,
            "CognitoUserPoolClientId",
            description="ID of the Cognito User Pool Client",
            value=cognito_user_pool_client.ref,
            export_name=f"{self.stack_name}-CognitoUserPoolClientId",
        )

        CfnOutput(
            self,
            "CognitoDiscoveryUrl",
            description="Cognito OIDC Discovery URL",
            value=f"https://cognito-idp.{self.region}.amazonaws.com/{cognito_user_pool.user_pool_id}/.well-known/openid-configuration",
            export_name=f"{self.stack_name}-CognitoDiscoveryUrl",
        )

        CfnOutput(
            self,
            "TestUsername",
            description="Test username for authentication",
            value="testuser",
        )

        CfnOutput(
            self,
            "TestPassword",
            description="Test password for authentication",
            value="MyPassword123!",
        )

        CfnOutput(
            self,
            "GetTokenCommand",
            description="Command to get authentication token",
            value=f"python get_token.py {cognito_user_pool_client.ref} testuser MyPassword123!",
        )
