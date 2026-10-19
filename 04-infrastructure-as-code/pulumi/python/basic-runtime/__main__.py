"""Basic AgentCore Runtime - Pulumi Python."""

import hashlib
import json
import os
import re

import pulumi
import pulumi_aws as aws

# ============================================================================
# Configuration
# ============================================================================

config = pulumi.Config()
agent_name = config.get("agentName") or "BasicAgent"
network_mode = config.get("networkMode") or "PUBLIC"
image_tag = config.get("imageTag") or "latest"
stack_name = config.get("stackName") or "agentcore-basic"
description = (
    config.get("description") or "Basic AgentCore runtime with a simple Strands agent"
)
environment_variables: dict[str, str] = config.get_object("environmentVariables") or {}
ecr_repository_name = config.get("ecrRepositoryName") or "basic-agent"

# Get the AWS region from the provider configuration
aws_config = pulumi.Config("aws")
aws_region = aws_config.require("region")

# ============================================================================
# Data Sources
# ============================================================================

current_identity = aws.get_caller_identity()
current_region = aws.get_region()

# ============================================================================
# S3 Bucket for Agent Source Code
# ============================================================================

agent_source_bucket = aws.s3.Bucket(
    "agent_source",
    bucket_prefix=f"{stack_name}-source-",
    force_destroy=True,
    tags={
        "Name": f"{stack_name}-agent-source",
        "Purpose": "Store agent source code for CodeBuild",
    },
)

aws.s3.BucketPublicAccessBlock(
    "agent_source",
    bucket=agent_source_bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
)

aws.s3.BucketVersioning(
    "agent_source",
    bucket=agent_source_bucket.id,
    versioning_configuration=aws.s3.BucketVersioningVersioningConfigurationArgs(
        status="Enabled",
    ),
)

# ============================================================================
# Upload Agent Source Code to S3
# ============================================================================

agent_source_object = aws.s3.BucketObjectv2(
    "agent_source",
    bucket=agent_source_bucket.id,
    key="agent-code.zip",
    source=pulumi.FileArchive(os.path.join(os.path.dirname(__file__), "agent-code")),
    tags={
        "Name": "agent-source-code",
    },
)

# ============================================================================
# ECR Repository - Container Registry for Agent Image
# ============================================================================

agent_ecr = aws.ecr.Repository(
    "agent_ecr",
    name=f"{stack_name}-{ecr_repository_name}",
    image_tag_mutability="MUTABLE",
    image_scanning_configuration=aws.ecr.RepositoryImageScanningConfigurationArgs(
        scan_on_push=True,
    ),
    force_delete=True,
    tags={
        "Name": f"{stack_name}-ecr-repository",
        "Module": "ECR",
    },
)

aws.ecr.RepositoryPolicy(
    "agent_ecr",
    repository=agent_ecr.name,
    policy=pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AllowPullFromAccount",
                    "Effect": "Allow",
                    "Principal": {
                        "AWS": f"arn:aws:iam::{current_identity.account_id}:root",
                    },
                    "Action": [
                        "ecr:BatchGetImage",
                        "ecr:GetDownloadUrlForLayer",
                    ],
                },
            ],
        }
    ),
)

aws.ecr.LifecyclePolicy(
    "agent_ecr",
    repository=agent_ecr.name,
    policy=json.dumps(
        {
            "rules": [
                {
                    "rulePriority": 1,
                    "description": "Keep last 5 images",
                    "selection": {
                        "tagStatus": "any",
                        "countType": "imageCountMoreThan",
                        "countNumber": 5,
                    },
                    "action": {
                        "type": "expire",
                    },
                },
            ],
        }
    ),
)

# ============================================================================
# Agent Execution Role - For AgentCore Runtime
# ============================================================================

agent_execution = aws.iam.Role(
    "agent_execution",
    name=f"{stack_name}-agent-execution-role",
    assume_role_policy=pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AssumeRolePolicy",
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "bedrock-agentcore.amazonaws.com",
                    },
                    "Action": "sts:AssumeRole",
                    "Condition": {
                        "StringEquals": {
                            "aws:SourceAccount": current_identity.account_id,
                        },
                        "ArnLike": {
                            "aws:SourceArn": f"arn:aws:bedrock-agentcore:{current_region.region}:{current_identity.account_id}:*",
                        },
                    },
                },
            ],
        }
    ),
    tags={
        "Name": f"{stack_name}-agent-execution-role",
        "Module": "IAM",
    },
)

agent_execution_managed = aws.iam.RolePolicyAttachment(
    "agent_execution_managed",
    role=agent_execution.name,
    policy_arn="arn:aws:iam::aws:policy/BedrockAgentCoreFullAccess",
)

agent_execution_role_policy = aws.iam.RolePolicy(
    "agent_execution",
    name="AgentCoreExecutionPolicy",
    role=agent_execution.id,
    policy=pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "ECRImageAccess",
                    "Effect": "Allow",
                    "Action": [
                        "ecr:BatchGetImage",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchCheckLayerAvailability",
                    ],
                    "Resource": agent_ecr.arn,
                },
                {
                    "Sid": "ECRTokenAccess",
                    "Effect": "Allow",
                    "Action": ["ecr:GetAuthorizationToken"],
                    "Resource": "*",
                },
                {
                    "Sid": "CloudWatchLogs",
                    "Effect": "Allow",
                    "Action": [
                        "logs:DescribeLogStreams",
                        "logs:CreateLogGroup",
                        "logs:DescribeLogGroups",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    "Resource": f"arn:aws:logs:{current_region.region}:{current_identity.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*",
                },
                {
                    "Sid": "XRayTracing",
                    "Effect": "Allow",
                    "Action": [
                        "xray:PutTraceSegments",
                        "xray:PutTelemetryRecords",
                        "xray:GetSamplingRules",
                        "xray:GetSamplingTargets",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "CloudWatchMetrics",
                    "Effect": "Allow",
                    "Action": ["cloudwatch:PutMetricData"],
                    "Resource": "*",
                    "Condition": {
                        "StringEquals": {
                            "cloudwatch:namespace": "bedrock-agentcore",
                        },
                    },
                },
                {
                    "Sid": "BedrockModelInvocation",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "GetAgentAccessToken",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetWorkloadAccessToken",
                        "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                        "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                    ],
                    "Resource": [
                        f"arn:aws:bedrock-agentcore:{current_region.region}:{current_identity.account_id}:workload-identity-directory/default",
                        f"arn:aws:bedrock-agentcore:{current_region.region}:{current_identity.account_id}:workload-identity-directory/default/workload-identity/*",
                    ],
                },
            ],
        }
    ),
)

# ============================================================================
# CodeBuild Service Role - For Docker Image Building
# ============================================================================

codebuild_role = aws.iam.Role(
    "codebuild",
    name=f"{stack_name}-codebuild-role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "codebuild.amazonaws.com",
                    },
                    "Action": "sts:AssumeRole",
                },
            ],
        }
    ),
    tags={
        "Name": f"{stack_name}-codebuild-role",
        "Module": "IAM",
    },
)

codebuild_role_policy = aws.iam.RolePolicy(
    "codebuild",
    name="CodeBuildPolicy",
    role=codebuild_role.id,
    policy=pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "CloudWatchLogs",
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    "Resource": f"arn:aws:logs:{current_region.region}:{current_identity.account_id}:log-group:/aws/codebuild/*",
                },
                {
                    "Sid": "ECRAccess",
                    "Effect": "Allow",
                    "Action": [
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchGetImage",
                        "ecr:GetAuthorizationToken",
                        "ecr:PutImage",
                        "ecr:InitiateLayerUpload",
                        "ecr:UploadLayerPart",
                        "ecr:CompleteLayerUpload",
                    ],
                    "Resource": [agent_ecr.arn, "*"],
                },
                {
                    "Sid": "S3SourceAccess",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                    "Resource": pulumi.Output.concat(agent_source_bucket.arn, "/*"),
                },
                {
                    "Sid": "S3BucketAccess",
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                    "Resource": agent_source_bucket.arn,
                },
            ],
        }
    ),
)

# ============================================================================
# Build Trigger Lambda - Start and Wait for CodeBuild
# ============================================================================

agent_image_project_name = f"{stack_name}-basic-agent-build"

build_trigger_role = aws.iam.Role(
    "build_trigger",
    name=f"{stack_name}-build-trigger-role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "lambda.amazonaws.com",
                    },
                    "Action": "sts:AssumeRole",
                },
            ],
        }
    ),
    inline_policies=[
        aws.iam.RoleInlinePolicyArgs(
            name="BuildTriggerPolicy",
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "ManageBuild",
                            "Effect": "Allow",
                            "Action": [
                                "codebuild:StartBuild",
                                "codebuild:BatchGetBuilds",
                            ],
                            "Resource": f"arn:aws:codebuild:{current_region.region}:{current_identity.account_id}:project/{agent_image_project_name}",
                        },
                    ],
                }
            ),
        ),
    ],
    tags={
        "Name": f"{stack_name}-build-trigger-role",
        "Module": "Lambda",
    },
)

build_trigger_basic_execution = aws.iam.RolePolicyAttachment(
    "build_trigger_basic_execution",
    role=build_trigger_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

build_trigger_function = aws.lambda_.Function(
    "build_trigger",
    name=f"{stack_name}-build-trigger",
    role=build_trigger_role.arn,
    runtime=aws.lambda_.Runtime.PYTHON3D12,
    handler="index.handler",
    timeout=900,
    code=pulumi.FileArchive(
        os.path.join(os.path.dirname(__file__), "lambda/build-trigger")
    ),
    tags={
        "Name": f"{stack_name}-build-trigger",
        "Module": "Lambda",
    },
)

# ============================================================================
# CodeBuild Project - Build and Push Docker Image
# ============================================================================

buildspec_path = os.path.join(os.path.dirname(__file__), "buildspec.yml")
with open(buildspec_path) as f:
    buildspec_content = f.read()
buildspec_fingerprint = hashlib.sha256(buildspec_content.encode()).hexdigest()

agent_image = aws.codebuild.Project(
    "agent_image",
    name=agent_image_project_name,
    description=f"Build basic agent Docker image for {stack_name}",
    service_role=codebuild_role.arn,
    build_timeout=60,
    artifacts=aws.codebuild.ProjectArtifactsArgs(
        type="NO_ARTIFACTS",
    ),
    environment=aws.codebuild.ProjectEnvironmentArgs(
        compute_type="BUILD_GENERAL1_LARGE",
        image="aws/codebuild/amazonlinux2-aarch64-standard:3.0",
        type="ARM_CONTAINER",
        privileged_mode=True,
        image_pull_credentials_type="CODEBUILD",
        environment_variables=[
            aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                name="AWS_DEFAULT_REGION",
                value=current_region.region,
            ),
            aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                name="AWS_ACCOUNT_ID",
                value=current_identity.account_id,
            ),
            aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                name="IMAGE_REPO_NAME",
                value=agent_ecr.name,
            ),
            aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                name="IMAGE_TAG",
                value=image_tag,
            ),
            aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                name="STACK_NAME",
                value=stack_name,
            ),
        ],
    ),
    source=aws.codebuild.ProjectSourceArgs(
        type="S3",
        location=pulumi.Output.concat(
            agent_source_bucket.id, "/", agent_source_object.key
        ),
        buildspec=buildspec_content,
    ),
    logs_config=aws.codebuild.ProjectLogsConfigArgs(
        cloudwatch_logs=aws.codebuild.ProjectLogsConfigCloudwatchLogsArgs(
            group_name=f"/aws/codebuild/{stack_name}-basic-agent-build",
        ),
    ),
    tags={
        "Name": f"{stack_name}-basic-agent-build",
        "Module": "CodeBuild",
    },
)

# ============================================================================
# Trigger CodeBuild - Build Image Before Creating Runtime
# ============================================================================

build_trigger_invocation_input = pulumi.Output.all(
    agent_image.name, current_region.region
).apply(
    lambda args: json.dumps(
        {
            "projectName": args[0],
            "region": args[1],
            "pollIntervalSeconds": 15,
        }
    )
)

trigger_build = aws.lambda_.Invocation(
    "trigger_build",
    function_name=build_trigger_function.name,
    input=build_trigger_invocation_input,
    triggers={
        "sourceVersion": agent_source_object.version_id,
        "imageTag": image_tag,
        "buildspecSha256": buildspec_fingerprint,
    },
    opts=pulumi.ResourceOptions(
        depends_on=[
            agent_image,
            agent_ecr,
            codebuild_role_policy,
            agent_source_object,
            build_trigger_basic_execution,
            build_trigger_function,
        ],
    ),
)

# ============================================================================
# AgentCore Runtime - Main Agent Runtime Resource
# ============================================================================

runtime_name = re.sub(r"-", "_", f"{stack_name}_{agent_name}")

merged_env_vars: dict[str, str] = {
    "AWS_REGION": aws_region,
    "AWS_DEFAULT_REGION": aws_region,
    **environment_variables,
}

basic_agent = aws.bedrock.AgentcoreAgentRuntime(
    "basic_agent",
    agent_runtime_name=runtime_name,
    description=description,
    role_arn=agent_execution.arn,
    agent_runtime_artifact=aws.bedrock.AgentcoreAgentRuntimeAgentRuntimeArtifactArgs(
        container_configuration=aws.bedrock.AgentcoreAgentRuntimeAgentRuntimeArtifactContainerConfigurationArgs(
            container_uri=pulumi.Output.concat(
                agent_ecr.repository_url, ":", image_tag
            ),
        ),
    ),
    network_configuration=aws.bedrock.AgentcoreAgentRuntimeNetworkConfigurationArgs(
        network_mode=network_mode,
    ),
    environment_variables=merged_env_vars,
    opts=pulumi.ResourceOptions(
        depends_on=[
            trigger_build,
            agent_execution_role_policy,
            agent_execution_managed,
        ],
    ),
)

# ============================================================================
# Outputs
# ============================================================================

pulumi.export("agent_runtime_id", basic_agent.agent_runtime_id)
pulumi.export("agent_runtime_arn", basic_agent.agent_runtime_arn)
pulumi.export("agent_runtime_version", basic_agent.agent_runtime_version)
pulumi.export("ecr_repository_url", agent_ecr.repository_url)
pulumi.export("ecr_repository_arn", agent_ecr.arn)
pulumi.export("agent_execution_role_arn", agent_execution.arn)
pulumi.export("codebuild_project_name", agent_image.name)
pulumi.export("codebuild_project_arn", agent_image.arn)
pulumi.export("source_bucket_name", agent_source_bucket.id)
pulumi.export("source_bucket_arn", agent_source_bucket.arn)
pulumi.export("source_object_key", agent_source_object.key)
