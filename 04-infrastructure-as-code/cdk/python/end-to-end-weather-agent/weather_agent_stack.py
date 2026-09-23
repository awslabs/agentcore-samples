from aws_cdk import (
    Stack,
    aws_ecr as ecr,
    aws_s3 as s3,
    aws_codebuild as codebuild,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3_assets as s3_assets,
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
from agentcore_role import AgentCoreRole


class WeatherAgentStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Parameters
        agent_name = CfnParameter(
            self,
            "AgentName",
            type="String",
            default="WeatherAgent",
            description="Name for the agent runtime",
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

        memory_name = CfnParameter(
            self,
            "MemoryName",
            type="String",
            default="WeatherAgentMemory",
            description="Name for the AgentCore memory resource",
        )

        # S3 Results Bucket
        results_bucket = s3.Bucket(
            self,
            "ResultsBucket",
            bucket_name=f"{self.stack_name.lower()}-results-{self.account}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=False,
            public_read_access=False,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # ECR Repository
        ecr_repository = ecr.Repository(
            self,
            "ECRRepository",
            repository_name=f"{self.stack_name.lower()}-weather-agent",
            image_tag_mutability=ecr.TagMutability.MUTABLE,
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
            image_scan_on_push=True,
        )

        # S3 Asset for source code
        source_asset = s3_assets.Asset(self, "SourceAsset", path="./agent-code")

        # CodeBuild Role
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
                        iam.PolicyStatement(
                            sid="S3SourceAccess",
                            effect=iam.Effect.ALLOW,
                            actions=["s3:GetObject"],
                            resources=[f"{source_asset.bucket.bucket_arn}/*"],
                        ),
                    ]
                )
            },
        )

        # CodeBuild Project
        build_project = codebuild.Project(
            self,
            "AgentImageBuildProject",
            project_name=f"{self.stack_name}-weather-agent-build",
            description=f"Build weather agent Docker image for {self.stack_name}",
            role=codebuild_role,
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxArmBuildImage.AMAZON_LINUX_2_STANDARD_3_0,
                compute_type=codebuild.ComputeType.LARGE,
                privileged=True,
            ),
            source=codebuild.Source.s3(bucket=source_asset.bucket, path=source_asset.s3_object_key),
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
                                "echo Building the Docker image for weather agent ARM64...",
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
            environment_variables={
                "AWS_DEFAULT_REGION": codebuild.BuildEnvironmentVariable(value=self.region),
                "AWS_ACCOUNT_ID": codebuild.BuildEnvironmentVariable(value=self.account),
                "IMAGE_REPO_NAME": codebuild.BuildEnvironmentVariable(value=ecr_repository.repository_name),
                "IMAGE_TAG": codebuild.BuildEnvironmentVariable(value=image_tag.value_as_string),
                "STACK_NAME": codebuild.BuildEnvironmentVariable(value=self.stack_name),
            },
        )

        # Lambda function to trigger and wait for CodeBuild
        build_trigger_function = lambda_.Function(
            self,
            "BuildTriggerFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="infra_utils.build_trigger_lambda.handler",
            timeout=Duration.minutes(15),
            code=lambda_.Code.from_asset(".", exclude=["*.pyc", "__pycache__", "cdk.out"]),
            initial_policy=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["codebuild:StartBuild", "codebuild:BatchGetBuilds"],
                    resources=[build_project.project_arn],
                )
            ],
        )

        # Custom Resource using the Lambda function
        trigger_build = CustomResource(
            self,
            "TriggerImageBuild",
            service_token=build_trigger_function.function_arn,
            properties={"ProjectName": build_project.project_name},
        )

        # Create AgentCore execution role with S3 permissions
        agent_role = AgentCoreRole(self, "AgentCoreRole", s3_bucket_arn=results_bucket.bucket_arn)

        # Browser Tool
        browser_tool = bedrockagentcore.CfnBrowserCustom(
            self,
            "BrowserTool",
            name=f"{self.stack_name.replace('-', '_')}_browser",
            network_configuration=bedrockagentcore.CfnBrowserCustom.BrowserNetworkConfigurationProperty(
                network_mode=network_mode.value_as_string
            ),
            description=f"Browser tool for {self.stack_name} weather agent",
        )

        # Code Interpreter Tool
        code_interpreter_tool = bedrockagentcore.CfnCodeInterpreterCustom(
            self,
            "CodeInterpreterTool",
            name=f"{self.stack_name.replace('-', '_')}_code_interpreter",
            network_configuration=bedrockagentcore.CfnCodeInterpreterCustom.CodeInterpreterNetworkConfigurationProperty(
                network_mode=network_mode.value_as_string
            ),
            description=f"Code interpreter tool for {self.stack_name} weather agent",
        )

        # Memory
        memory = bedrockagentcore.CfnMemory(
            self,
            "Memory",
            name=f"{self.stack_name.replace('-', '_')}_{memory_name.value_as_string}",
            description=f"Memory for {self.stack_name} weather agent",
            event_expiry_duration=30,
        )

        # Memory Initializer Lambda
        memory_initializer_function = lambda_.Function(
            self,
            "MemoryInitializerFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="infra_utils.memory_initializer_lambda.handler",
            timeout=Duration.minutes(5),
            code=lambda_.Code.from_asset(".", exclude=["*.pyc", "__pycache__", "cdk.out"]),
            initial_policy=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "bedrock-agentcore:CreateEvent",
                        "bedrock-agentcore:ListEvents",
                        "bedrock-agentcore:GetMemory",
                    ],
                    resources=["*"],
                )
            ],
        )

        # Initialize Memory with preferences
        initialize_memory = CustomResource(
            self,
            "InitializeMemory",
            service_token=memory_initializer_function.function_arn,
            properties={"MemoryId": memory.attr_memory_id, "Region": self.region},
        )
        initialize_memory.node.add_dependency(memory)

        # Create AgentCore Runtime
        agent_runtime = bedrockagentcore.CfnRuntime(
            self,
            "AgentRuntime",
            agent_runtime_name=f"{self.stack_name.replace('-', '_')}_{agent_name.value_as_string}",
            agent_runtime_artifact=bedrockagentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=bedrockagentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=f"{ecr_repository.repository_uri}:{image_tag.value_as_string}"
                )
            ),
            network_configuration=bedrockagentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode=network_mode.value_as_string
            ),
            protocol_configuration="HTTP",
            role_arn=agent_role.role_arn,
            description=f"Weather agent runtime for {self.stack_name}",
            environment_variables={
                "AWS_DEFAULT_REGION": self.region,
                "BROWSER_ID": browser_tool.attr_browser_id,
                "CODE_INTERPRETER_ID": code_interpreter_tool.attr_code_interpreter_id,
                "MEMORY_ID": memory.attr_memory_id,
                "RESULTS_BUCKET": results_bucket.bucket_name,
            },
        )

        agent_runtime.node.add_dependency(trigger_build)
        agent_runtime.node.add_dependency(browser_tool)
        agent_runtime.node.add_dependency(code_interpreter_tool)
        agent_runtime.node.add_dependency(initialize_memory)

        # Outputs
        CfnOutput(
            self,
            "AgentRuntimeId",
            description="ID of the created agent runtime",
            value=agent_runtime.attr_agent_runtime_id,
        )

        CfnOutput(
            self,
            "AgentRuntimeArn",
            description="ARN of the created agent runtime",
            value=agent_runtime.attr_agent_runtime_arn,
        )

        CfnOutput(
            self,
            "BrowserId",
            description="ID of the browser tool",
            value=browser_tool.attr_browser_id,
        )

        CfnOutput(
            self,
            "CodeInterpreterId",
            description="ID of the code interpreter tool",
            value=code_interpreter_tool.attr_code_interpreter_id,
        )

        CfnOutput(
            self,
            "MemoryId",
            description="ID of the memory resource",
            value=memory.attr_memory_id,
        )

        CfnOutput(
            self,
            "ResultsBucketName",
            description="Name of the S3 results bucket",
            value=results_bucket.bucket_name,
        )

        CfnOutput(
            self,
            "AgentRoleArn",
            description="ARN of the agent execution role",
            value=agent_role.role_arn,
        )
