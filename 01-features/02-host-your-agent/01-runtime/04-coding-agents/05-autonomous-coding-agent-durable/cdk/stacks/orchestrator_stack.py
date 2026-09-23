"""Orchestrator stack: Lambda DURABLE FUNCTION + EventBridge rule + SNS topic.

Mirrors deploy/41_durable_orchestrator.sh.

The orchestrator is an AWS Lambda Durable Function (python3.13) that suspends at
$0 compute while the coding agent works (async callback) and is not bound by the
15-min ceiling. Durable execution is enabled via the native DurableConfig property
(ExecutionTimeout=86400s, RetentionPeriodInDays=1) — this can only be set at
creation. The function is invoked via a PUBLISHED VERSION (durable functions
require a qualified ARN); EventBridge targets that version.

There is NO dispatcher Lambda — the coding agent calls SendDurableExecutionCallback*
itself (its runtime exec role has the permission, see runtime_stack.py).

Package = orchestrator/handler.py + repo shared/ + vendored
aws-durable-execution-sdk-python + boto3>=1.43. Handler = handler.handler.
Bundled locally (pip) so `cdk synth` works without Docker; falls back to the
PYTHON_3_13 bundling image if local bundling is unavailable.
"""
import os
import shutil
import subprocess

import aws_cdk as cdk
import jsii
from aws_cdk import (
    aws_events as events,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_sns as sns,
    aws_sns_subscriptions as subscriptions,
)
from constructs import Construct

_CDK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_CDK_DIR)
_ORCH_DIR = os.path.join(_PROJECT_ROOT, "orchestrator")
_SHARED_DIR = os.path.join(_PROJECT_ROOT, "shared")
_VENDOR = ["aws-durable-execution-sdk-python", "boto3>=1.43"]


@jsii.implements(cdk.ILocalBundling)
class _LocalDurableBundler:
    """Vendors handler.py + shared/ + durable SDK + boto3 into the asset output dir
    using the local pip — no Docker needed at synth time."""

    def try_bundle(self, output_dir: str, *_args, **_kwargs) -> bool:
        pip = shutil.which("pip3") or shutil.which("pip")
        if not pip:
            return False  # fall back to Docker image bundling
        try:
            subprocess.run(
                [pip, "install", "--quiet", "--target", output_dir, *_VENDOR],
                check=True,
            )
            shutil.copy2(os.path.join(_ORCH_DIR, "handler.py"),
                         os.path.join(output_dir, "handler.py"))
            dst_shared = os.path.join(output_dir, "shared")
            if os.path.isdir(dst_shared):
                shutil.rmtree(dst_shared)
            shutil.copytree(
                _SHARED_DIR, dst_shared,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        except (subprocess.CalledProcessError, OSError):
            return False
        return True


class OrchestratorStack(cdk.Stack):
    """Durable orchestrator Lambda (published version) + EventBridge + SNS."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str,
        bucket: s3.IBucket,
        memory_id: str,
        coding_agent_arn: str,
        sandbox_arn: str,
        sandbox_swift_arn: str,
        evaluator_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        account = cdk.Stack.of(self).account
        region = cdk.Stack.of(self).region
        fn_name = f"{project}-orchestrator-durable"

        # --- SNS topic ---
        self.sns_topic = sns.Topic(self, "ResultsTopic", display_name=f"{project} Ticket Results")
        notification_email = self.node.try_get_context("notification_email")
        if notification_email:
            self.sns_topic.add_subscription(subscriptions.EmailSubscription(notification_email))

        # --- Lambda role: durable managed policy + app inline policy ---
        lambda_role = iam.Role(
            self,
            "DurableOrchestratorRole",
            role_name=f"{project}-orchestrator-durable-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        # Checkpoint permissions for durable execution (required).
        lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicDurableExecutionRolePolicy"
            )
        )
        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="Logs",
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{region}:{account}:*"],
        ))
        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="S3ReadTicket",
            actions=["s3:GetObject"],
            resources=[f"{bucket.bucket_arn}/tickets-source/*"],
        ))
        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="S3DemoProgress",
            actions=["s3:GetObject", "s3:PutObject"],
            resources=[f"{bucket.bucket_arn}/demo-progress/*"],
        ))
        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="AgentCoreInvoke",
            actions=[
                "bedrock-agentcore:InvokeAgentRuntime",
                "bedrock-agentcore:InvokeAgentRuntimeCommand",
            ],
            resources=[
                f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/{project}_*",
                f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/{project}_*/*",
            ],
        ))
        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="Memory",
            actions=[
                "bedrock-agentcore:BatchCreateMemoryRecords",
                "bedrock-agentcore:RetrieveMemoryRecords",
                "bedrock-agentcore:ListMemoryRecords",
            ],
            resources=[f"arn:aws:bedrock-agentcore:{region}:{account}:memory/*"],
        ))
        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="SSMRuntimeArns",
            actions=["ssm:GetParameter", "ssm:GetParameters"],
            resources=[f"arn:aws:ssm:{region}:{account}:parameter/{project}/runtime/*"],
        ))
        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="SNSPublish",
            actions=["sns:Publish"],
            resources=[self.sns_topic.topic_arn],
        ))

        # --- Durable function ---
        code = lambda_.Code.from_asset(
            _ORCH_DIR,
            bundling=cdk.BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_13.bundling_image,
                local=_LocalDurableBundler(),
                command=[
                    "bash", "-c",
                    "pip install --target /asset-output "
                    + " ".join(f"'{d}'" for d in _VENDOR)
                    + " && cp /asset-input/handler.py /asset-output/handler.py "
                    + "&& cp -a /asset-input/../shared /asset-output/shared",
                ],
            ),
        )

        sns_topic_arn = self.sns_topic.topic_arn
        self.lambda_fn = lambda_.Function(
            self,
            "DurableOrchestratorFn",
            function_name=fn_name,
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="handler.handler",
            code=code,
            role=lambda_role,
            timeout=cdk.Duration.seconds(900),
            memory_size=512,
            # Durable execution — set ONLY at creation.
            durable_config=lambda_.DurableConfig(
                execution_timeout=cdk.Duration.seconds(86400),
                retention_period=cdk.Duration.days(1),
            ),
            environment={
                "BUCKET": bucket.bucket_name,
                "PROJECT": project,
                "MEMORY_ID": memory_id,
                "SNS_TOPIC_ARN": sns_topic_arn,
                "MAX_ATTEMPTS": "3",
                # Optional env fallbacks (handler prefers SSM at /<project>/runtime/*).
                # Keys match orchestrator/handler.py _ENV_FALLBACK ("evaluator" -> EVALUATOR_ARN).
                "CODING_AGENT_ARN": coding_agent_arn,
                "SANDBOX_ARN": sandbox_arn,
                "SANDBOX_SWIFT_ARN": sandbox_swift_arn,
                "EVALUATOR_ARN": evaluator_arn,
            },
        )

        # Published version — durable functions require a qualified ARN to invoke.
        version = self.lambda_fn.current_version
        version.apply_removal_policy(cdk.RemovalPolicy.RETAIN)
        self.version_arn = version.function_arn

        # --- EventBridge rule -> the published VERSION ---
        rule = events.CfnRule(
            self,
            "TicketCreatedRule",
            name=f"{project}-ticket-created-durable",
            description=f"Route ticket events to the {project} durable orchestrator",
            event_pattern={
                "source": [f"{project}.tickets"],
                "detail-type": ["TicketCreated"],
            },
            targets=[events.CfnRule.TargetProperty(
                id="orchestrator",
                arn=version.function_arn,
            )],
        )
        # Allow EventBridge to invoke the qualified (versioned) function.
        lambda_.CfnPermission(
            self,
            "AllowEventBridgeInvoke",
            action="lambda:InvokeFunction",
            function_name=version.function_arn,
            principal="events.amazonaws.com",
            source_arn=rule.attr_arn,
        )

        # --- Outputs ---
        cdk.CfnOutput(self, "DurableFunctionName", value=fn_name,
                      export_name=f"{project}-orchestrator-name")
        cdk.CfnOutput(self, "DurableVersionArn", value=self.version_arn,
                      export_name=f"{project}-orchestrator-version-arn")
        cdk.CfnOutput(self, "SnsTopicArn", value=self.sns_topic.topic_arn,
                      export_name=f"{project}-sns-topic-arn")
        cdk.CfnOutput(self, "EventRuleName", value=f"{project}-ticket-created-durable",
                      export_name=f"{project}-event-rule-name")
