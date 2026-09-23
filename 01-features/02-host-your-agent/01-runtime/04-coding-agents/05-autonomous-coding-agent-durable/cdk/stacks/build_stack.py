"""Build stack: ECR repos + CodeBuild projects for native ARM64 image builds.

Creates the four ECR repos (coding-agent, sandbox, sandbox-swift, evaluator) and a
CodeBuild project per image (native ARM64, no QEMU). AgentCore microVMs are ARM64-only.

The runtime stack references the resulting ECR image URIs. Building the images is
the ONE step `cdk deploy --all` cannot do inline — start the builds after deploy via
`scripts/build_images.sh` (uploads source to s3://<bucket>/build-artifacts/<name>.zip
then `aws codebuild start-build`), or build/push locally with the shell scripts.

The swift sandbox shares the sandbox/ build context but uses Dockerfile.swift
(set via the DOCKERFILE build env var). The evaluator is a standalone agent with its
own build context (evaluator-agent/) and its own Dockerfile.
"""
import aws_cdk as cdk
from aws_cdk import (
    aws_codebuild as codebuild,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct


class BuildStack(cdk.Stack):
    """ECR repos + CodeBuild projects for ARM64 container image builds."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str,
        bucket: s3.IBucket,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        account = cdk.Stack.of(self).account
        region = cdk.Stack.of(self).region

        # --- ECR repos (coding-agent, sandbox, sandbox-swift, evaluator) ---
        repo_names = {
            "coding-agent": f"{project}-coding-agent",
            "sandbox": f"{project}-sandbox",
            "sandbox-swift": f"{project}-sandbox-swift",
            "evaluator": f"{project}-evaluator",
        }
        self.repos = {
            key: ecr.Repository(
                self,
                f"Ecr-{key}",
                repository_name=name,
                image_scan_on_push=True,
                removal_policy=cdk.RemovalPolicy.RETAIN,
            )
            for key, name in repo_names.items()
        }

        # --- CodeBuild IAM role ---
        self.build_role = iam.Role(
            self,
            "CodeBuildRole",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
            description="Role for CodeBuild ARM64 image builds (ECR push + logs)",
        )
        self.build_role.add_to_policy(iam.PolicyStatement(
            sid="ECRAuth", actions=["ecr:GetAuthorizationToken"], resources=["*"],
        ))
        self.build_role.add_to_policy(iam.PolicyStatement(
            sid="ECRPush",
            actions=[
                "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
                "ecr:BatchCheckLayerAvailability", "ecr:PutImage",
                "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
            ],
            resources=[f"arn:aws:ecr:{region}:{account}:repository/{project}-*"],
        ))
        self.build_role.add_to_policy(iam.PolicyStatement(
            sid="Logs",
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[
                f"arn:aws:logs:{region}:{account}:log-group:/aws/codebuild/{project}-*",
                f"arn:aws:logs:{region}:{account}:log-group:/aws/codebuild/{project}-*:*",
            ],
        ))
        self.build_role.add_to_policy(iam.PolicyStatement(
            sid="S3Source",
            actions=[
                "s3:GetObject", "s3:GetObjectVersion",
                "s3:GetBucketVersioning", "s3:ListBucket",
            ],
            resources=[bucket.bucket_arn, f"{bucket.bucket_arn}/build-artifacts/*"],
        ))

        # --- Buildspec (DOCKERFILE defaults to "Dockerfile"; swift overrides it) ---
        build_spec = codebuild.BuildSpec.from_object({
            "version": "0.2",
            "env": {"variables": {"DOCKERFILE": "Dockerfile"}},
            "phases": {
                "pre_build": {"commands": [
                    "echo Logging in to Amazon ECR...",
                    "aws ecr get-login-password --region $AWS_DEFAULT_REGION"
                    " | docker login --username AWS --password-stdin"
                    " $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com",
                ]},
                "build": {"commands": [
                    "echo Building $IMAGE_REPO_NAME:$IMAGE_TAG from $DOCKERFILE",
                    "docker build -f $DOCKERFILE -t $IMAGE_REPO_NAME:$IMAGE_TAG .",
                    "docker tag $IMAGE_REPO_NAME:$IMAGE_TAG"
                    " $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME:$IMAGE_TAG",
                ]},
                "post_build": {"commands": [
                    "docker push"
                    " $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME:$IMAGE_TAG",
                ]},
            },
        })

        def _build_project(logical_id, project_name, repo_name, source_zip, dockerfile):
            return codebuild.Project(
                self,
                logical_id,
                project_name=project_name,
                environment=codebuild.BuildEnvironment(
                    build_image=codebuild.LinuxArmBuildImage.AMAZON_LINUX_2_STANDARD_3_0,
                    compute_type=codebuild.ComputeType.LARGE,
                    privileged=True,  # docker-in-docker
                ),
                source=codebuild.Source.s3(bucket=bucket, path=source_zip),
                build_spec=build_spec,
                environment_variables={
                    "AWS_ACCOUNT_ID": codebuild.BuildEnvironmentVariable(value=account),
                    "IMAGE_REPO_NAME": codebuild.BuildEnvironmentVariable(value=repo_name),
                    "IMAGE_TAG": codebuild.BuildEnvironmentVariable(value="latest"),
                    "DOCKERFILE": codebuild.BuildEnvironmentVariable(value=dockerfile),
                },
                role=self.build_role,
                timeout=cdk.Duration.minutes(40),
            )

        self.coding_agent_build = _build_project(
            "CodingAgentBuild", f"{project}-build-coding-agent",
            f"{project}-coding-agent", "build-artifacts/coding-agent.zip", "Dockerfile")
        self.sandbox_build = _build_project(
            "SandboxBuild", f"{project}-build-sandbox",
            f"{project}-sandbox", "build-artifacts/sandbox.zip", "Dockerfile")
        # Swift reuses the sandbox build context (zip) but builds Dockerfile.swift.
        self.sandbox_swift_build = _build_project(
            "SandboxSwiftBuild", f"{project}-build-sandbox-swift",
            f"{project}-sandbox-swift", "build-artifacts/sandbox.zip", "Dockerfile.swift")
        # Evaluator is a standalone agent: its own build context (evaluator-agent/) + Dockerfile.
        self.evaluator_build = _build_project(
            "EvaluatorBuild", f"{project}-build-evaluator",
            f"{project}-evaluator", "build-artifacts/evaluator.zip", "Dockerfile")

        # --- Outputs ---
        cdk.CfnOutput(self, "CodingAgentBuildProject", value=self.coding_agent_build.project_name,
                      export_name=f"{project}-build-coding-agent-name")
        cdk.CfnOutput(self, "SandboxBuildProject", value=self.sandbox_build.project_name,
                      export_name=f"{project}-build-sandbox-name")
        cdk.CfnOutput(self, "SandboxSwiftBuildProject", value=self.sandbox_swift_build.project_name,
                      export_name=f"{project}-build-sandbox-swift-name")
        cdk.CfnOutput(self, "EvaluatorBuildProject", value=self.evaluator_build.project_name,
                      export_name=f"{project}-build-evaluator-name")
        cdk.CfnOutput(self, "BuildRoleArn", value=self.build_role.role_arn,
                      export_name=f"{project}-build-role-arn")
