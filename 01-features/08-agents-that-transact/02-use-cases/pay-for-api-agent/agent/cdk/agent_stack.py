"""Pay for API — buyer agent CDK stack.

Provisions the full AgentCore Runtime stack for the buyer agent **without
requiring Docker on the machine running `cdk deploy`**:

1. **Amazon S3 asset** — zips and uploads ``agent/container/`` to the CDK bootstrap
   assets bucket.
2. **Amazon ECR repository** — destination for the built image.
3. **AWS CodeBuild project** — ARM64 Linux environment that pulls the S3
   asset, runs ``docker build``, and pushes to ECR. Runs in AWS, so the
   caller needs only ``cdk deploy`` and AWS credentials.
4. **Build trigger AWS Lambda function** — custom resource that starts
   the CodeBuild run and polls until the image is in ECR before the
   Runtime resource is created.
5. **IAM execution role** with the minimum perms the runtime needs at
   invoke time (Amazon Bedrock, AgentCore Payments data plane, Amazon
   CloudWatch Logs, AWS X-Ray, Amazon CloudWatch Application Signals,
   vended log delivery).
6. **AgentCore Runtime** pointing at the freshly-built image.

Outputs the Runtime ARN, invoke URL, and execution role ARN so the
notebook can invoke the deployed agent by name.
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    CustomResource,
    Duration,
    RemovalPolicy,
    Stack,
    aws_bedrockagentcore as bedrockagentcore,
    aws_codebuild as codebuild,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_lambda as aws_lambda,
    aws_s3_assets as s3_assets,
)
from constructs import Construct

# The container source lives in a sibling folder to cdk/ — resolve the
# absolute path once so S3 asset + docker build share the same context.
CONTAINER_DIR = str(Path(__file__).resolve().parent.parent / "container")


class AgentCorePaymentsBuyerAgentStack(Stack):
    """AgentCore Runtime + IAM for the Pay for API buyer agent."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── ECR repository ──
        agent_repo = ecr.Repository(
            self,
            "AgentEcrRepo",
            repository_name="pay-for-api-agent",
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
            project_name="pay-for-api-agent-build",
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
                                "aws ecr get-login-password --region $AWS_DEFAULT_REGION | "
                                "docker login --username AWS --password-stdin "
                                "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com",
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
            function_name="pay-for-api-agent-build-trigger",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="index.handler",
            role=build_trigger_role,
            timeout=Duration.minutes(15),
            memory_size=128,
            code=aws_lambda.Code.from_inline(
                r"""
import json
import time
import urllib.request

import boto3


def handler(event, context):
    props = event.get("ResourceProperties", {})
    project_name = props.get("ProjectName", "")

    # No rebuild on stack delete — ECR contents are torn down by the
    # repository's lifecycle.
    if event["RequestType"] == "Delete":
        return _respond(event, context, "SUCCESS", {"ImageBuilt": "skipped"})

    cb = boto3.client("codebuild")
    try:
        build = cb.start_build(projectName=project_name)
        build_id = build["build"]["id"]
        print(f"Started CodeBuild: {build_id}")

        # Poll every 30 seconds for up to ~14 minutes.
        for _ in range(28):
            time.sleep(30)
            result = cb.batch_get_builds(ids=[build_id])
            status = result["builds"][0]["buildStatus"]
            print(f"Build status: {status}")
            if status == "SUCCEEDED":
                return _respond(event, context, "SUCCESS", {"BuildId": build_id})
            if status in ("FAILED", "FAULT", "STOPPED", "TIMED_OUT"):
                return _respond(
                    event, context, "FAILED",
                    {"Error": f"CodeBuild {status}"},
                )
        return _respond(event, context, "FAILED", {"Error": "Build timed out"})
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}")
        return _respond(event, context, "FAILED", {"Error": str(exc)})


def _respond(event, context, status, data):
    body = json.dumps({
        "Status": status,
        "Reason": json.dumps(data),
        "PhysicalResourceId": context.log_stream_name,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": data,
    })
    req = urllib.request.Request(
        event["ResponseURL"],
        data=body.encode(),
        method="PUT",
        headers={"Content-Type": ""},
    )
    urllib.request.urlopen(req)
"""
            ),
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
                "Pay for API buyer agent runtime execution role. "
                "Grants Bedrock model invoke + AgentCore Payments DP ops the "
                "AgentCorePaymentsPlugin needs at runtime."
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
                    # Inference profile (cross-region routing)
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                    # Underlying foundation model in each US region the profile
                    # can route to.
                    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
                    "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
                    "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
                ],
            )
        )

        # AgentCore Payments data-plane operations the plugin calls at
        # runtime. The Manager / Instrument / Session IDs are not known
        # at role creation time (the notebook creates them in §4), so
        # the resource list is wildcarded to all PaymentManagers in the
        # caller's account. Production hardening: scope to the specific
        # Manager ARN once it is stable, or add a tag-based condition
        # on `aws:ResourceTag/Project`.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:ProcessPayment",
                    "bedrock-agentcore:GetPaymentSession",
                    "bedrock-agentcore:GetPaymentInstrument",
                    "bedrock-agentcore:GetPaymentInstrumentBalance",
                    "bedrock-agentcore:GetResourcePaymentToken",
                ],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:payment-manager/*",
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:payment-manager/*/instrument/*",
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:payment-manager/*/session/*",
                ],
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

        # ── Observability ──
        # The agent container runs AWS Distro for OpenTelemetry and also
        # wires up CloudWatch Logs vended delivery for the PaymentManager
        # on first invocation (see `_ensure_vended_log_delivery` in
        # agent.py). Both paths need the permissions below.

        # Logs vended-delivery pipeline: Payments → CloudWatch Logs.
        # The delivery source/destination/delivery objects are not
        # resource-scoped (CloudWatch Logs creates them per-region per-
        # account), so the resource list stays wildcarded. The log
        # group writes themselves are scoped to the agentcore-payments
        # log group prefix.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="CloudWatchLogsVendedDelivery",
                actions=[
                    "logs:CreateDelivery",
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:DeleteDelivery",
                    "logs:DeleteDeliveryDestination",
                    "logs:DeleteDeliverySource",
                    "logs:DeleteLogGroup",
                    "logs:DeleteResourcePolicy",
                    "logs:DescribeLogGroups",
                    "logs:DescribeResourcePolicies",
                    "logs:GetDelivery",
                    "logs:GetDeliveryDestination",
                    "logs:GetDeliverySource",
                    "logs:PutDeliveryDestination",
                    "logs:PutDeliverySource",
                    "logs:PutLogEvents",
                    "logs:PutResourcePolicy",
                    "logs:PutRetentionPolicy",
                ],
                # CloudWatch Logs does not permit resource-level scoping
                # on Describe* and Put*Delivery* APIs. The log group
                # actions are implicitly scoped by the delivery target,
                # which we restrict via DeliveryDestination. Production
                # hardening: scope to specific log group prefixes once
                # stable.
                resources=["*"],
            )
        )

        # X-Ray + CloudWatch Application Signals — ADOT emit targets.
        # X-Ray and Application Signals do not accept resource-level
        # ARNs on these actions; the documented IAM policy for ADOT
        # observability uses Resource: "*". The agent's traces are
        # implicitly scoped to its own session via OpenTelemetry
        # context, not via IAM.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="XRayApplicationSignalsCloudTrail",
                actions=[
                    "xray:GetTraceSegmentDestination",
                    "xray:ListResourcePolicies",
                    "xray:PutResourcePolicy",
                    "xray:PutTelemetryRecords",
                    "xray:PutTraceSegments",
                    "xray:UpdateTraceSegmentDestination",
                    "application-signals:StartDiscovery",
                    "cloudtrail:CreateServiceLinkedChannel",
                ],
                resources=["*"],
            )
        )

        # Service-linked role for Application Signals — created once per
        # account, condition-scoped to that specific SLR.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="CreateServiceLinkedRoleForAppSignals",
                actions=["iam:CreateServiceLinkedRole"],
                resources=[
                    "arn:*:iam::*:role/aws-service-role/"
                    "application-signals.cloudwatch.amazonaws.com/"
                    "AWSServiceRoleForCloudWatchApplicationSignals",
                ],
            )
        )

        # PaymentsAllowVendedLogDeliveryForResource +
        # AllowVendedLogDeliveryForResource on the PaymentManager —
        # what lets Payments emit logs through the vended pipeline
        # above. CloudWatch checks both actions implicitly when
        # `logs.put_delivery_source` runs against a Payment Manager
        # ARN: the Payments-prefixed one as the product-level gate, the
        # unprefixed one as the AgentCore-wide gate. Scoped to
        # PaymentManager resources only.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="BedrockAgentCorePaymentsVendedLogDelivery",
                actions=[
                    "bedrock-agentcore:PaymentsAllowVendedLogDeliveryForResource",
                    "bedrock-agentcore:AllowVendedLogDeliveryForResource",
                ],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:payment-manager/*",
                ],
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

        # ── AgentCore Memory ──
        # Persistent conversation memory for the buyer agent. Short
        # event expiry because the demo is stateless between notebook
        # runs; bump to 30+ days for real workloads.
        agent_memory = bedrockagentcore.CfnMemory(
            self,
            "AgentMemory",
            name="pay_for_api_agent_memory",
            description=(
                "Conversation memory for the Pay for API buyer agent. "
                "Each invocation gets its own session under the caller's "
                "paymentUserId actor."
            ),
            event_expiry_duration=7,
        )

        # Grant runtime role the memory CRUD actions it needs at invoke
        # time. Scoped to the Memory resource we just created.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="AgentCoreMemoryCRUD",
                actions=[
                    "bedrock-agentcore:CreateMemory",
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:UpdateMemory",
                    "bedrock-agentcore:DeleteMemory",
                    "bedrock-agentcore:CreateMemoryRecord",
                    "bedrock-agentcore:GetMemoryRecord",
                    "bedrock-agentcore:UpdateMemoryRecord",
                    "bedrock-agentcore:ListMemoryRecords",
                    "bedrock-agentcore:SearchMemoryRecords",
                    "bedrock-agentcore:DeleteMemoryRecord",
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:DeleteEvent",
                    "bedrock-agentcore:ListActors",
                    "bedrock-agentcore:ListSessions",
                ],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:memory/*",
                ],
            )
        )

        # ── AgentCore Runtime ──
        # containerUri points at the image we built in CodeBuild. The
        # asset_hash is used as the tag so a change in agent/container/
        # cycles a new image + triggers Runtime update.
        #
        # networkMode=PUBLIC: the runtime container has outbound
        # internet access, which the agent uses to call the seller's
        # HTTP API. For production deployments that integrate with
        # private services, switch to VPC mode and route the runtime
        # through a NAT Gateway with VPC endpoints for AWS APIs.
        runtime = bedrockagentcore.CfnRuntime(
            self,
            "AgentRuntime",
            agent_runtime_name="pay_for_api_agent_runtime",
            description=(
                "Pay for API buyer agent — Strands Agent with Claude Sonnet "
                "4.5 and AgentCorePaymentsPlugin for autonomous x402 payment."
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
                "MODEL_ID": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "ENABLE_PAYMENTS_PLUGIN": "1",
                # Turn on the vended log delivery wiring in agent.py on
                # first invocation. Set to "0" for debugging.
                "ENABLE_VENDED_LOG_DELIVERY": "1",
                # AgentCore Memory resource the agent attaches to via
                # AgentCoreMemorySessionManager in agent.py.
                "BEDROCK_AGENTCORE_MEMORY_ID": agent_memory.attr_memory_id,
                # ADOT auto-instrumentation (matches the defaults in
                # agent.py so opentelemetry-instrument picks them up too).
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
        # And on the memory resource being created so the env var is resolvable.
        runtime.node.add_dependency(agent_memory)

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
            # Resolved at deploy time: the {region} and {runtime_id}
            # placeholders are substituted into the AgentCore endpoint
            # template by the CDK f-string before CloudFormation sees
            # the value.
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
        CfnOutput(
            self,
            "AgentMemoryId",
            value=agent_memory.attr_memory_id,
            description="AgentCore Memory resource the runtime uses for sessions",
        )
