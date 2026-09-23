from aws_cdk import (
    Stack,
    aws_ecr as ecr,
    aws_codebuild as codebuild,
    aws_iam as iam,
    aws_lambda as lambda_,
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


class MultiAgentStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Parameters
        agent1_name = CfnParameter(
            self,
            "Agent1Name",
            type="String",
            default="OrchestratorAgent",
            description="Name for the orchestrator agent runtime (agent1)",
        )

        agent2_name = CfnParameter(
            self,
            "Agent2Name",
            type="String",
            default="SpecialistAgent",
            description="Name for the specialist agent runtime (agent2)",
        )

        image_tag = CfnParameter(
            self,
            "ImageTag",
            type="String",
            default="latest",
            description="Tag for the Docker images",
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
            default="multi-agent",
            description="Base name of the ECR repositories",
        )

        # ECR Repositories
        ecr_repository_agent1 = ecr.Repository(
            self,
            "ECRRepositoryAgent1",
            repository_name=f"{self.stack_name.lower()}-{ecr_repository_name.value_as_string}-agent1",
            image_tag_mutability=ecr.TagMutability.MUTABLE,
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
            image_scan_on_push=True,
        )

        ecr_repository_agent2 = ecr.Repository(
            self,
            "ECRRepositoryAgent2",
            repository_name=f"{self.stack_name.lower()}-{ecr_repository_name.value_as_string}-agent2",
            image_tag_mutability=ecr.TagMutability.MUTABLE,
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
            image_scan_on_push=True,
        )
        # IAM Roles
        # Agent1 Execution Role (with permissions to invoke Agent2)
        agent1_execution_role = iam.Role(
            self,
            "Agent1ExecutionRole",
            role_name=f"{self.stack_name}-agent1-execution-role",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("BedrockAgentCoreFullAccess")],
            inline_policies={
                "Agent1ExecutionPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="ECRImageAccess",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "ecr:BatchGetImage",
                                "ecr:GetDownloadUrlForLayer",
                                "ecr:BatchCheckLayerAvailability",
                            ],
                            resources=[ecr_repository_agent1.repository_arn],
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
                        iam.PolicyStatement(
                            sid="GetAgentAccessToken",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock-agentcore:GetWorkloadAccessToken",
                                "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                                "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                            ],
                            resources=[
                                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default",
                                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default/workload-identity/*",
                            ],
                        ),
                        iam.PolicyStatement(
                            sid="BedrockModelInvocation",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:InvokeModel",
                                "bedrock:InvokeModelWithResponseStream",
                            ],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            sid="InvokeAgent2Runtime",
                            effect=iam.Effect.ALLOW,
                            actions=["bedrock-agentcore:InvokeAgentRuntime"],
                            resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/*"],
                        ),
                    ]
                )
            },
        )
        # Agent2 Execution Role (basic permissions)
        agent2_execution_role = iam.Role(
            self,
            "Agent2ExecutionRole",
            role_name=f"{self.stack_name}-agent2-execution-role",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("BedrockAgentCoreFullAccess")],
            inline_policies={
                "Agent2ExecutionPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="ECRImageAccess",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "ecr:BatchGetImage",
                                "ecr:GetDownloadUrlForLayer",
                                "ecr:BatchCheckLayerAvailability",
                            ],
                            resources=[ecr_repository_agent2.repository_arn],
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
                        iam.PolicyStatement(
                            sid="GetAgentAccessToken",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock-agentcore:GetWorkloadAccessToken",
                                "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                                "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                            ],
                            resources=[
                                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default",
                                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default/workload-identity/*",
                            ],
                        ),
                        iam.PolicyStatement(
                            sid="BedrockModelInvocation",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:InvokeModel",
                                "bedrock:InvokeModelWithResponseStream",
                            ],
                            resources=["*"],
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
                            resources=[
                                ecr_repository_agent1.repository_arn,
                                ecr_repository_agent2.repository_arn,
                                "*",
                            ],
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
                            resources=["*"],  # Will be updated after CodeBuild projects are created
                        )
                    ]
                )
            },
        )
        # Lambda Function for CodeBuild Trigger
        build_trigger_lambda = lambda_.Function(
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
        # Agent2 Build Project (build first as it's independent)
        agent2_build_project = codebuild.Project(
            self,
            "Agent2ImageBuildProject",
            project_name=f"{self.stack_name}-agent2-build",
            description=f"Build agent2 Docker image for {self.stack_name}",
            role=codebuild_role,
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxArmBuildImage.AMAZON_LINUX_2_STANDARD_3_0,
                compute_type=codebuild.ComputeType.LARGE,
                privileged=True,
            ),
            environment_variables={
                "AWS_DEFAULT_REGION": codebuild.BuildEnvironmentVariable(value=self.region),
                "AWS_ACCOUNT_ID": codebuild.BuildEnvironmentVariable(value=self.account),
                "IMAGE_REPO_NAME": codebuild.BuildEnvironmentVariable(value=ecr_repository_agent2.repository_name),
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
                                "echo Building the Docker image for agent2 ARM64...",
                                # Create requirements.txt
                                """cat > requirements.txt << 'EOF'
strands-agents
boto3>=1.40.0
botocore>=1.40.0
bedrock-agentcore
EOF""",
                                # Create agent2.py
                                """cat > agent2.py << 'EOF'
from strands import Agent
import os
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

def create_specialist_agent() -> Agent:
    \"\"\"Create a specialist agent that handles specific analytical tasks\"\"\"
    system_prompt = \"\"\"You are a specialist analytical agent.
    You are an expert at analyzing data and providing detailed insights.
    When asked questions, provide thorough, well-reasoned responses with specific details.
    Focus on accuracy and completeness in your answers.\"\"\"

    return Agent(
        system_prompt=system_prompt,
        name="SpecialistAgent"
    )

@app.entrypoint
async def invoke(payload=None):
    \"\"\"Main entrypoint for agent2\"\"\"
    try:
        # Get the query from payload
        query = payload.get("prompt", "Hello") if payload else "Hello"

        # Create and use the specialist agent
        agent = create_specialist_agent()
        response = agent(query)

        return {
            "status": "success",
            "agent": "agent2",
            "response": response.message['content'][0]['text']
        }

    except Exception as e:
        return {
            "status": "error",
            "agent": "agent2",
            "error": str(e)
        }

if __name__ == "__main__":
    app.run()
EOF""",
                                # Create Dockerfile
                                """cat > Dockerfile << 'EOF'
FROM public.ecr.aws/docker/library/python:3.11-slim
WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt
RUN pip install aws-opentelemetry-distro>=0.10.1

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080
EXPOSE 8000

COPY . .

CMD ["opentelemetry-instrument", "python", "-m", "agent2"]
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
        # Agent1 Build Project (orchestrator that calls agent2)
        agent1_build_project = codebuild.Project(
            self,
            "Agent1ImageBuildProject",
            project_name=f"{self.stack_name}-agent1-build",
            description=f"Build agent1 Docker image for {self.stack_name}",
            role=codebuild_role,
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxArmBuildImage.AMAZON_LINUX_2_STANDARD_3_0,
                compute_type=codebuild.ComputeType.LARGE,
                privileged=True,
            ),
            environment_variables={
                "AWS_DEFAULT_REGION": codebuild.BuildEnvironmentVariable(value=self.region),
                "AWS_ACCOUNT_ID": codebuild.BuildEnvironmentVariable(value=self.account),
                "IMAGE_REPO_NAME": codebuild.BuildEnvironmentVariable(value=ecr_repository_agent1.repository_name),
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
                                "echo Building the Docker image for agent1 ARM64...",
                                # Create requirements.txt
                                """cat > requirements.txt << 'EOF'
strands-agents
boto3>=1.40.0
botocore>=1.40.0
bedrock-agentcore
EOF""",
                                # Create agent1.py - this is a large block, so I'll split it
                                """cat > agent1.py << 'EOF'
from strands import Agent, tool
from typing import Dict, Any
import boto3
import json
import os
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

# Environment variable for Agent2 ARN (will be set by CloudFormation)
AGENT2_ARN = os.getenv('AGENT2_ARN', '')

def invoke_agent2(query: str) -> str:
    \"\"\"Helper function to invoke agent2 using boto3\"\"\"
    import uuid
    try:
        # Get region from environment or use default
        region = os.getenv('AWS_REGION', 'us-west-2')
        agentcore_client = boto3.client('bedrock-agentcore', region_name=region)

        # Invoke agent2 runtime (using AWS sample format)
        response = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=AGENT2_ARN,
            qualifier="DEFAULT",
            payload=json.dumps({"prompt": query})
        )

        # Handle streaming response (text/event-stream)
        if "text/event-stream" in response.get("contentType", ""):
            result = ""
            for line in response["response"].iter_lines(chunk_size=10):
                if line:
                    line = line.decode("utf-8")
                    # Remove 'data: ' prefix if present
                    if line.startswith("data: "):
                        line = line[6:]
                    result += line
            return result

        # Handle JSON response
        elif response.get("contentType") == "application/json":
            content = []
            for chunk in response.get("response", []):
                content.append(chunk.decode('utf-8'))
            response_data = json.loads(''.join(content))
            return json.dumps(response_data)

        # Handle other response types
        else:
            response_body = response['response'].read()
            return response_body.decode('utf-8')

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        return f"Error invoking agent2: {str(e)}\\nDetails: {error_details}"

@tool
def call_specialist_agent(query: str) -> Dict[str, Any]:
    \"\"\"
    Call the specialist agent (agent2) for detailed analysis or complex tasks.
    Use this tool when you need expert analysis or detailed information.

    Args:
        query: The question or task to send to the specialist agent

    Returns:
        The specialist agent's response
    \"\"\"
    result = invoke_agent2(query)
    return {
        "status": "success",
        "content": [{"text": result}]
    }

def create_orchestrator_agent() -> Agent:
    \"\"\"Create the orchestrator agent with the tool to call agent2\"\"\"
    system_prompt = \"\"\"You are an orchestrator agent.
    You can handle simple queries directly, but for complex analytical tasks,
    you should delegate to the specialist agent using the call_specialist_agent tool.

    Use the specialist agent when:
    - The query requires detailed analysis
    - The query is about complex topics
    - The user explicitly asks for expert analysis

    Handle simple queries (greetings, basic questions) yourself.\"\"\"

    return Agent(
        tools=[call_specialist_agent],
        system_prompt=system_prompt,
        name="OrchestratorAgent"
    )

@app.entrypoint
async def invoke(payload=None):
    \"\"\"Main entrypoint for agent1\"\"\"
    try:
        # Get the query from payload
        query = payload.get("prompt", "Hello, how are you?") if payload else "Hello, how are you?"

        # Create and use the orchestrator agent
        agent = create_orchestrator_agent()
        response = agent(query)

        return {
            "status": "success",
            "agent": "agent1",
            "response": response.message['content'][0]['text']
        }

    except Exception as e:
        return {
            "status": "error",
            "agent": "agent1",
            "error": str(e)
        }

if __name__ == "__main__":
    app.run()
EOF""",
                                # Create Dockerfile
                                """cat > Dockerfile << 'EOF'
FROM public.ecr.aws/docker/library/python:3.11-slim
WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt
RUN pip install aws-opentelemetry-distro>=0.10.1

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080
EXPOSE 8000

COPY . .

CMD ["opentelemetry-instrument", "python", "-m", "agent1"]
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
        # Custom Resources to trigger builds
        trigger_agent2_build = CustomResource(
            self,
            "TriggerAgent2ImageBuild",
            service_token=build_trigger_lambda.function_arn,
            properties={
                "ProjectName": agent2_build_project.project_name,
                "WaitForCompletion": "true",
            },
        )
        trigger_agent2_build.node.add_dependency(ecr_repository_agent2)
        trigger_agent2_build.node.add_dependency(agent2_build_project)

        trigger_agent1_build = CustomResource(
            self,
            "TriggerAgent1ImageBuild",
            service_token=build_trigger_lambda.function_arn,
            properties={
                "ProjectName": agent1_build_project.project_name,
                "WaitForCompletion": "true",
            },
        )
        trigger_agent1_build.node.add_dependency(ecr_repository_agent1)
        trigger_agent1_build.node.add_dependency(agent1_build_project)

        # Agent2 Runtime (deploy first as agent1 depends on it)
        agent2_runtime = bedrockagentcore.CfnRuntime(
            self,
            "Agent2Runtime",
            agent_runtime_name=f"{self.stack_name.replace('-', '_')}_{agent2_name.value_as_string}",
            agent_runtime_artifact=bedrockagentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=bedrockagentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=f"{ecr_repository_agent2.repository_uri}:{image_tag.value_as_string}"
                )
            ),
            role_arn=agent2_execution_role.role_arn,
            network_configuration=bedrockagentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode=network_mode.value_as_string
            ),
            description=f"Specialist agent runtime for {self.stack_name}",
        )
        agent2_runtime.node.add_dependency(trigger_agent2_build)

        # Agent1 Runtime (orchestrator with agent2 ARN as environment variable)
        agent1_runtime = bedrockagentcore.CfnRuntime(
            self,
            "Agent1Runtime",
            agent_runtime_name=f"{self.stack_name.replace('-', '_')}_{agent1_name.value_as_string}",
            agent_runtime_artifact=bedrockagentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=bedrockagentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=f"{ecr_repository_agent1.repository_uri}:{image_tag.value_as_string}"
                )
            ),
            role_arn=agent1_execution_role.role_arn,
            network_configuration=bedrockagentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode=network_mode.value_as_string
            ),
            description=f"Orchestrator agent runtime for {self.stack_name}",
            environment_variables={"AGENT2_ARN": agent2_runtime.attr_agent_runtime_arn},
        )
        agent1_runtime.node.add_dependency(trigger_agent1_build)
        agent1_runtime.node.add_dependency(agent2_runtime)
        # Outputs
        CfnOutput(
            self,
            "Agent1RuntimeId",
            description="ID of agent1 (orchestrator) runtime",
            value=agent1_runtime.attr_agent_runtime_id,
            export_name=f"{self.stack_name}-Agent1RuntimeId",
        )

        CfnOutput(
            self,
            "Agent1RuntimeArn",
            description="ARN of agent1 (orchestrator) runtime",
            value=agent1_runtime.attr_agent_runtime_arn,
            export_name=f"{self.stack_name}-Agent1RuntimeArn",
        )

        CfnOutput(
            self,
            "Agent2RuntimeId",
            description="ID of agent2 (specialist) runtime",
            value=agent2_runtime.attr_agent_runtime_id,
            export_name=f"{self.stack_name}-Agent2RuntimeId",
        )

        CfnOutput(
            self,
            "Agent2RuntimeArn",
            description="ARN of agent2 (specialist) runtime",
            value=agent2_runtime.attr_agent_runtime_arn,
            export_name=f"{self.stack_name}-Agent2RuntimeArn",
        )

        CfnOutput(
            self,
            "Agent1ECRRepositoryUri",
            description="URI of the ECR repository for agent1",
            value=ecr_repository_agent1.repository_uri,
            export_name=f"{self.stack_name}-Agent1ECRRepositoryUri",
        )

        CfnOutput(
            self,
            "Agent2ECRRepositoryUri",
            description="URI of the ECR repository for agent2",
            value=ecr_repository_agent2.repository_uri,
            export_name=f"{self.stack_name}-Agent2ECRRepositoryUri",
        )
