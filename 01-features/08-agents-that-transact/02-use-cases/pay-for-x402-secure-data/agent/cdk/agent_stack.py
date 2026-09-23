"""Pay for Secure Data (x402) — agent CDK stack.

Provisions the AgentCore Runtime for the trust-gated x402 agent **without
requiring Docker on the machine running `cdk deploy`**:

1. **Amazon S3 asset** — zips and uploads ``agent/container/`` to the CDK
   bootstrap assets bucket.
2. **Amazon ECR repository** — destination for the built image.
3. **AWS CodeBuild project** — ARM64 Linux environment that pulls the S3
   asset, runs ``docker build``, and pushes to ECR. Runs in AWS, so the
   caller needs only ``cdk deploy`` and AWS credentials.
4. **Build trigger AWS Lambda function** — custom resource that starts the
   CodeBuild run and polls until the image is in ECR before the Runtime
   resource is created.
5. **IAM execution role** with the minimum perms the runtime needs at
   invoke time (Amazon Bedrock model invoke, AgentCore payments data-plane
   ops, Amazon CloudWatch Logs, AWS X-Ray, Amazon CloudWatch Application
   Signals, vended log delivery).
6. **AgentCore Runtime** pointing at the freshly-built image, with
   ``networkMode: PUBLIC`` so the agent can reach the t54 x402-secure and
   registered target x402 endpoints.

The Manager ARN / Connector ID and the x402-secure guardrail configuration
are passed as container environment variables. The per-invocation payment
context (``user_id``, ``payment_session_id``, ``payment_instrument_id``) is
supplied by the caller on each ``/invocations`` request, not baked into the
runtime — so a single deployment serves many users and sessions.

Outputs the Runtime ARN, invoke URL, and execution role ARN so the
notebook can invoke the deployed agent by name.
"""

from __future__ import annotations

import os
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    CustomResource,
    Duration,
    RemovalPolicy,
    Stack,
    aws_lambda,
)
from aws_cdk import (
    aws_bedrockagentcore as bedrockagentcore,
)
from aws_cdk import (
    aws_codebuild as codebuild,
)
from aws_cdk import (
    aws_ecr as ecr,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_s3_assets as s3_assets,
)
from constructs import Construct

# The container source lives in a sibling folder to cdk/ — resolve the
# absolute path once so the S3 asset + docker build share the same context.
CONTAINER_DIR = str(Path(__file__).resolve().parent.parent / "container")

# The CodeBuild trigger custom-resource handler lives in its own folder (loaded
# via Code.from_asset) so it is covered by linting and static analysis.
BUILD_TRIGGER_DIR = str(Path(__file__).resolve().parent / "build_trigger")

# Claude Sonnet 4.5 cross-region inference profile (US). Overridable via the
# BEDROCK_MODEL_ID env var at deploy time to match the notebook.
DEFAULT_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
)


class AgentCorePaymentsX402SecureDataAgentStack(Stack):
    """AgentCore Runtime + IAM for the Pay for Secure Data (x402) agent."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Payment + guardrail configuration passed through to the container.
        # The Manager ARN / Connector ID are created by the notebook (§4);
        # the per-invocation payment context is supplied per request.
        manager_arn = os.environ.get("MANAGER_ARN", "")
        connector_id = os.environ.get("PAYMENT_CONNECTOR_ID", "")

        # ── ECR repository ──
        agent_repo = ecr.Repository(
            self,
            "AgentEcrRepo",
            repository_name="pay-for-x402-secure-data-agent",
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    max_image_count=5,
                    description="Keep the 5 most recent images",
                )
            ],
        )

        # ── S3 asset: zip of agent/container/ ──
        # CDK uploads this to the bootstrap assets bucket on every
        # `cdk deploy`. CodeBuild pulls it from S3 — no GitHub, no
        # CodeCommit, no Docker-on-laptop.
        agent_source = s3_assets.Asset(
            self,
            "AgentSourceAsset",
            path=CONTAINER_DIR,
        )

        # ── CodeBuild project ──
        build_project = codebuild.Project(
            self,
            "AgentBuildProject",
            project_name="pay-for-x402-secure-data-agent-build",
            environment=codebuild.BuildEnvironment(
                # ARM64 matches AgentCore Runtime's Graviton hosts.
                build_image=codebuild.LinuxArmBuildImage.AMAZON_LINUX_2_STANDARD_3_0,
                compute_type=codebuild.ComputeType.SMALL,
                privileged=True,  # docker-in-docker for image build
            ),
            source=codebuild.Source.s3(
                bucket=agent_source.bucket,
                path=agent_source.s3_object_key,
            ),
            environment_variables={
                "AWS_ACCOUNT_ID": codebuild.BuildEnvironmentVariable(value=self.account),
                "AWS_DEFAULT_REGION": codebuild.BuildEnvironmentVariable(value=self.region),
                "ECR_REPO_URI": codebuild.BuildEnvironmentVariable(value=agent_repo.repository_uri),
                "IMAGE_TAG": codebuild.BuildEnvironmentVariable(value=agent_source.asset_hash),
            },
            build_spec=codebuild.BuildSpec.from_object(
                {
                    "version": "0.2",
                    "phases": {
                        "pre_build": {
                            "commands": [
                                "echo Logging in to ECR...",
                                # Parenthesized so the implicit concatenation is
                                # unambiguous: this is ONE shell command, not
                                # three list entries with a missing comma.
                                (
                                    "aws ecr get-login-password --region $AWS_DEFAULT_REGION | "
                                    "docker login --username AWS --password-stdin "
                                    "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com"
                                ),
                            ],
                        },
                        "build": {
                            "commands": [
                                "echo Building agent image...",
                                "docker build -t $ECR_REPO_URI:$IMAGE_TAG .",
                            ],
                        },
                        "post_build": {
                            "commands": [
                                "echo Pushing to ECR...",
                                "docker push $ECR_REPO_URI:$IMAGE_TAG",
                                "docker tag $ECR_REPO_URI:$IMAGE_TAG $ECR_REPO_URI:latest",
                                "docker push $ECR_REPO_URI:latest",
                            ],
                        },
                    },
                }
            ),
        )
        agent_repo.grant_pull_push(build_project)

        # ── Custom resource: kick off the build and wait for it to finish ──
        # The Runtime resource below references the image URI — we need the
        # image in ECR before CloudFormation moves past this step.
        build_trigger_role = iam.Role(
            self,
            "BuildTriggerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
        )
        build_trigger_role.add_to_policy(
            iam.PolicyStatement(
                actions=["codebuild:StartBuild", "codebuild:BatchGetBuilds"],
                resources=[build_project.project_arn],
            )
        )

        build_trigger_fn = aws_lambda.Function(
            self,
            "BuildTriggerFn",
            function_name="pay-for-x402-secure-data-build-trigger",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="index.handler",
            role=build_trigger_role,
            timeout=Duration.minutes(15),
            memory_size=128,
            # Handler lives in build_trigger/index.py so it is covered by
            # linting and static analysis (see build_trigger/index.py).
            code=aws_lambda.Code.from_asset(BUILD_TRIGGER_DIR),
        )

        trigger_build = CustomResource(
            self,
            "TriggerImageBuild",
            service_token=build_trigger_fn.function_arn,
            properties={
                "ProjectName": build_project.project_name,
                # Tie the CR hash to the asset hash — any change in
                # agent/container/ triggers a rebuild automatically.
                "SourceHash": agent_source.asset_hash,
            },
        )

        # ── IAM: runtime execution role ──
        execution_role = iam.Role(
            self,
            "AgentExecutionRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description=(
                "Pay for Secure Data (x402) agent runtime execution role. "
                "Grants Bedrock model invoke + the AgentCore payments DP ops "
                "the AgentCorePaymentsPlugin needs at runtime."
            ),
        )

        # Bedrock model invoke — Claude Sonnet 4.5 via the cross-region US
        # inference profile. Both the foundation model ARN and the
        # inference-profile ARN are granted because Bedrock resolves
        # through the profile.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
                    "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
                    "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
                ],
            )
        )

        # AgentCore payments data-plane operations the plugin calls at runtime.
        # The runtime only ever transacts against the manager passed to it as
        # MANAGER_ARN, so scope the policy to that specific manager (and its
        # instruments/sessions) when it is known at synth time — the notebook
        # creates the manager in §4 and writes MANAGER_ARN into .env before the
        # §8 deploy. Fall back to an account-scoped wildcard only if MANAGER_ARN
        # is not yet set (e.g. deploying before the manager exists).
        if manager_arn and ":payment-manager/" in manager_arn:
            manager_id = manager_arn.split(":payment-manager/", 1)[1].split("/")[0]
            payment_resources = [
                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:payment-manager/{manager_id}",
                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:payment-manager/{manager_id}/instrument/*",
                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:payment-manager/{manager_id}/session/*",
            ]
        else:
            payment_resources = [
                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:payment-manager/*",
                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:payment-manager/*/instrument/*",
                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:payment-manager/*/session/*",
            ]
        execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:ProcessPayment",
                    "bedrock-agentcore:GetPaymentSession",
                    "bedrock-agentcore:GetPaymentInstrument",
                    "bedrock-agentcore:GetPaymentInstrumentBalance",
                    "bedrock-agentcore:GetResourcePaymentToken",
                ],
                resources=payment_resources,
            )
        )

        # CloudWatch Logs — Runtime expects the role to be able to write its
        # own log stream.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                ],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/*",
                ],
            )
        )

        # X-Ray + CloudWatch Application Signals — ADOT emit targets. These
        # actions do not accept resource-level ARNs; the documented IAM
        # policy for ADOT observability uses Resource: "*". The agent's
        # traces are implicitly scoped to its own session via OpenTelemetry
        # context, not via IAM.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="XRayApplicationSignalsCloudTrail",
                actions=[
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                    "xray:PutTelemetryRecords",
                    "xray:PutTraceSegments",
                    "application-signals:StartDiscovery",
                    "cloudtrail:CreateServiceLinkedChannel",
                ],
                resources=["*"],
            )
        )

        # CloudWatch metrics — scoped to the bedrock-agentcore namespace.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="CloudWatchMetrics",
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            )
        )

        # ECR pull — the runtime pulls the image we built above.
        agent_repo.grant_pull(execution_role)

        # Allow this role to be passed to bedrock-agentcore.amazonaws.com.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[execution_role.role_arn],
                conditions={"StringEquals": {"iam:PassedToService": "bedrock-agentcore.amazonaws.com"}},
            )
        )

        # ── AgentCore Runtime ──
        # networkMode=PUBLIC: the runtime container needs outbound internet
        # access to reach the t54 x402-secure API and the registered target
        # x402 endpoint. For production deployments, switch to VPC mode and
        # route the runtime through a NAT Gateway with VPC endpoints for AWS
        # APIs plus an egress allow-list for the external x402 hosts.
        runtime = bedrockagentcore.CfnRuntime(
            self,
            "AgentRuntime",
            agent_runtime_name="pay_for_x402_secure_data_runtime",
            description=(
                "Pay for Secure Data (x402) agent — Strands Agent with Claude "
                "Sonnet 4.5, a t54 x402-secure trust gate, and "
                "AgentCorePaymentsPlugin for autonomous x402 payment."
            ),
            role_arn=execution_role.role_arn,
            network_configuration={"networkMode": "PUBLIC"},
            protocol_configuration="HTTP",
            agent_runtime_artifact={
                "containerConfiguration": {
                    "containerUri": f"{agent_repo.repository_uri}:{agent_source.asset_hash}",
                },
            },
            environment_variables={
                "AWS_REGION": self.region,
                "BEDROCK_MODEL_ID": DEFAULT_MODEL_ID,
                "AGENT_NAME": "pay-for-x402-secure-data",
                # Payment resources created by the notebook (§4). The
                # per-invocation payment context (user/session/instrument)
                # is supplied per request, not here.
                "MANAGER_ARN": manager_arn,
                "PAYMENT_CONNECTOR_ID": connector_id,
                # t54 x402-secure trust guardrail configuration.
                "X402_SECURE_BASE_URL": os.environ.get("X402_SECURE_BASE_URL", "https://x402-secure-api.t54.ai"),
                "X402_SECURE_SCORE_ENDPOINT": os.environ.get(
                    "X402_SECURE_SCORE_ENDPOINT", "/x402/tools/get_overall_score"
                ),
                "X402_TRUST_THRESHOLD": os.environ.get("X402_TRUST_THRESHOLD", "50"),
                "X402_TRUST_CACHE_TTL_SECONDS": os.environ.get("X402_TRUST_CACHE_TTL_SECONDS", "300"),
                "X402_TRUST_FAIL_CLOSED": os.environ.get("X402_TRUST_FAIL_CLOSED", "1"),
                "HEURIST_YAHOO_FINANCE_BASE_URL": os.environ.get(
                    "HEURIST_YAHOO_FINANCE_BASE_URL",
                    "https://mesh.heurist.xyz/x402/agents/YahooFinanceAgent",
                ),
                # ADOT auto-instrumentation.
                "AGENT_OBSERVABILITY_ENABLED": "true",
                "OTEL_PYTHON_DISTRO": "aws_distro",
                "OTEL_PYTHON_CONFIGURATOR": "aws_configurator",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                "OTEL_TRACES_EXPORTER": "otlp",
                "OTEL_LOGS_EXPORTER": "otlp",
                "OTEL_METRICS_EXPORTER": "none",
            },
        )

        # Runtime must wait on the CodeBuild-built image being ready.
        runtime.node.add_dependency(trigger_build)

        # ── Outputs ──
        CfnOutput(
            self,
            "AgentRuntimeArn",
            value=runtime.attr_agent_runtime_arn,
            description="ARN of the deployed AgentCore Runtime",
        )
        CfnOutput(
            self,
            "AgentRuntimeId",
            value=runtime.attr_agent_runtime_id,
            description="ID of the deployed AgentCore Runtime",
        )
        CfnOutput(
            self,
            "AgentRuntimeEndpoint",
            value=(
                f"https://bedrock-agentcore.{self.region}.amazonaws.com/"
                f"runtimes/{runtime.attr_agent_runtime_id}/invocations"
            ),
            description="Invoke URL for the deployed Runtime",
        )
        CfnOutput(
            self,
            "AgentExecutionRoleArn",
            value=execution_role.role_arn,
            description="IAM role the Runtime assumes at invoke time",
        )
        CfnOutput(
            self,
            "AgentEcrRepoUri",
            value=agent_repo.repository_uri,
            description="ECR repository URI the Runtime pulls from",
        )
        CfnOutput(
            self,
            "AgentBuildProjectName",
            value=build_project.project_name,
            description="CodeBuild project that builds the agent image",
        )
