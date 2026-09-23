"""Runtime stack: execution roles + the FOUR AgentCore runtimes + SSM ARN params.

Mirrors deploy/00_bootstrap.sh (exec roles) + deploy/30_create_base_runtimes.sh +
deploy/31_create_poc_runtimes.sh (the four runtimes), all VPC-mode with the ONE
broad S3 Files access point mounted at /mnt/shared.

Runtimes (CFN type AWS::BedrockAgentCore::Runtime, native — not a custom resource):
  - <project>_coding_agent : Claude Agent SDK (Opus). S3 mount only.
  - <project>_sandbox      : python executor. S3 mount + sessionStorage /mnt/workspace.
  - <project>_sandbox_swift: swift executor.  S3 mount + sessionStorage /mnt/workspace.
  - <project>_evaluator    : standalone review/evaluator agent — its OWN image
                             (<project>-evaluator) + its OWN least-privilege, READ-ONLY
                             IAM role (separate logs/cost/IAM from the coder). Opus 4.8,
                             S3 mount read-only at /mnt/shared (no sessionStorage). There
                             is no REVIEW_MODE flag — it is a first-class separate agent,
                             not the coding-agent image repurposed.

ARM64 container images are pre-built (CodeBuild / shell scripts) and referenced by
ECR URI via context (-c coding_agent_image=... / evaluator_image=... etc). There are
FOUR images now (coding-agent, sandbox, sandbox-swift, evaluator) — the evaluator no
longer reuses the coding-agent image. Each runtime's ARN is published to SSM at
/<project>/runtime/<key> (key = coding_agent | sandbox | sandbox_swift | evaluator) —
the orchestrator reads ARNs from there at invocation time, so recreating a runtime
needs no orchestrator redeploy.
"""
from typing import List

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2, aws_iam as iam, aws_ssm as ssm
from constructs import Construct


class RuntimeStack(cdk.Stack):
    """Execution role + four AgentCore runtimes + their SSM ARN parameters."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str,
        vpc: ec2.IVpc,
        security_group: ec2.ISecurityGroup,
        private_subnets: List[ec2.ISubnet],
        access_point_arn: str,
        bedrock_model: str = "global.anthropic.claude-opus-4-8",
        evaluator_model: str = "global.anthropic.claude-opus-4-8",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        account = cdk.Stack.of(self).account
        region = cdk.Stack.of(self).region

        # --- Container image URIs (pre-built; referenced by URI) ---
        coding_agent_image = self.node.try_get_context("coding_agent_image") or \
            f"{account}.dkr.ecr.{region}.amazonaws.com/{project}-coding-agent:latest"
        sandbox_image = self.node.try_get_context("sandbox_image") or \
            f"{account}.dkr.ecr.{region}.amazonaws.com/{project}-sandbox:latest"
        sandbox_swift_image = self.node.try_get_context("sandbox_swift_image") or \
            f"{account}.dkr.ecr.{region}.amazonaws.com/{project}-sandbox-swift:latest"
        # The evaluator is a standalone agent with its OWN image (not the coder's).
        evaluator_image = self.node.try_get_context("evaluator_image") or \
            f"{account}.dkr.ecr.{region}.amazonaws.com/{project}-evaluator:latest"
        bucket_name = f"{project}-shared-{account}-{region}"

        # --- Execution role (trusted by bedrock-agentcore.amazonaws.com) ---
        # Mirrors the inline policy in deploy/00_bootstrap.sh.
        self.exec_role = iam.Role(
            self,
            "ExecRole",
            role_name=f"{project}-runtime-exec",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:*"
                    },
                },
            ),
        )

        # ECR pull (auth token requires "*"; image actions also "*" per bootstrap script)
        self.exec_role.add_to_policy(iam.PolicyStatement(
            sid="ECRPull",
            actions=[
                "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
                "ecr:GetAuthorizationToken",
            ],
            resources=["*"],  # ECR authorization tokens are account-wide and cannot be scoped to individual repositories
        ))
        # CloudWatch Logs (runtime log groups)
        self.exec_role.add_to_policy(iam.PolicyStatement(
            sid="Logs",
            actions=[
                "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                "logs:DescribeLogStreams", "logs:DescribeLogGroups",
            ],
            resources=[
                f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*"
            ],
        ))
        # Bedrock model invocation
        self.exec_role.add_to_policy(iam.PolicyStatement(
            sid="Bedrock",
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=["*"],  # Model ARN is specified at invocation time by the agent; wildcard allows model flexibility
        ))
        # X-Ray
        self.exec_role.add_to_policy(iam.PolicyStatement(
            sid="XRay",
            actions=[
                "xray:PutTraceSegments", "xray:PutTelemetryRecords",
                "xray:GetSamplingRules", "xray:GetSamplingTargets",
            ],
            resources=["*"],  # X-Ray does not support resource-level permissions
        ))
        # Cross-runtime invoke (coder -> sandbox; in-session test command)
        self.exec_role.add_to_policy(iam.PolicyStatement(
            sid="InvokeSandboxRuntime",
            actions=[
                "bedrock-agentcore:InvokeAgentRuntime",
                "bedrock-agentcore:InvokeAgentRuntimeCommand",
            ],
            resources=[
                f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/{project}_*",
                f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/{project}_*/*",
            ],
        ))
        # AgentCore Memory (write/recall lessons)
        self.exec_role.add_to_policy(iam.PolicyStatement(
            sid="AgentCoreMemory",
            actions=[
                "bedrock-agentcore:CreateEvent",
                "bedrock-agentcore:BatchCreateMemoryRecords",
                "bedrock-agentcore:RetrieveMemoryRecords",
                "bedrock-agentcore:ListMemoryRecords",
                "bedrock-agentcore:GetMemoryRecord",
            ],
            resources=[f"arn:aws:bedrock-agentcore:{region}:{account}:memory/*"],
        ))
        # Durable callback (coding agent resumes the durable orchestrator itself)
        self.exec_role.add_to_policy(iam.PolicyStatement(
            sid="DurableCallback",
            actions=[
                "lambda:SendDurableExecutionCallbackSuccess",
                "lambda:SendDurableExecutionCallbackFailure",
            ],
            resources=["*"],  # Callback targets are determined at runtime by AgentCore
        ))
        # S3 Files NFS mount
        self.exec_role.add_to_policy(iam.PolicyStatement(
            sid="S3FilesMount",
            actions=[
                "s3files:ClientMount", "s3files:ClientWrite", "s3files:ClientRootAccess",
                "s3files:GetAccessPoint", "s3files:GetFileSystem", "s3files:GetMountTarget",
                "s3files:ListAccessPoints", "s3files:ListMountTargets",
                "s3files:DescribeMountTargets",
            ],
            resources=["*"],  # S3 Files access is scoped by the access point policy, not the IAM resource ARN
        ))
        # Seed-repo read (hydrate copies s3://<bucket>/repos/<repo>/ into the ticket dir)
        self.exec_role.add_to_policy(iam.PolicyStatement(
            sid="S3RepoSeedRead",
            actions=["s3:GetObject", "s3:ListBucket"],
            resources=[
                f"arn:aws:s3:::{bucket_name}",
                f"arn:aws:s3:::{bucket_name}/repos/*",
            ],
        ))
        # CloudWatch custom metrics
        self.exec_role.add_to_policy(iam.PolicyStatement(
            sid="CWMetrics",
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],  # PutMetricData does not support resource-level permissions
        ))

        # --- Evaluator execution role (least-privilege, READ-ONLY) ---
        # Mirrors the <project>-evaluator-exec role in deploy/00_bootstrap.sh. The
        # evaluator is a separate agent with separate IAM: it only reads the shared
        # mount and invokes Bedrock. It deliberately CANNOT InvokeAgentRuntime (no
        # sandbox / other agents), write Memory, run commands, write to S3, or send
        # durable callbacks — much narrower than the coder/sandbox shared role.
        self.evaluator_role = iam.Role(
            self,
            "EvaluatorRole",
            role_name=f"{project}-evaluator-exec",
            description=(
                f"Least-privilege read-only execution role for the {project} "
                f"evaluator agent"
            ),
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:*"
                    },
                },
            ),
        )
        # ECR pull
        self.evaluator_role.add_to_policy(iam.PolicyStatement(
            sid="ECRPull",
            actions=[
                "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
                "ecr:GetAuthorizationToken",
            ],
            resources=["*"],  # ECR authorization tokens are account-wide and cannot be scoped to individual repositories
        ))
        # CloudWatch Logs (runtime log groups)
        self.evaluator_role.add_to_policy(iam.PolicyStatement(
            sid="Logs",
            actions=[
                "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                "logs:DescribeLogStreams", "logs:DescribeLogGroups",
            ],
            resources=[
                f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*"
            ],
        ))
        # Bedrock model invocation
        self.evaluator_role.add_to_policy(iam.PolicyStatement(
            sid="BedrockInvoke",
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=["*"],  # Model ARN is specified at invocation time by the agent; wildcard allows model flexibility
        ))
        # S3 Files NFS mount — READ-ONLY (deliberately NO ClientWrite / ClientRootAccess)
        self.evaluator_role.add_to_policy(iam.PolicyStatement(
            sid="S3FilesMountReadOnly",
            actions=[
                "s3files:ClientMount",
                "s3files:GetAccessPoint", "s3files:GetFileSystem", "s3files:GetMountTarget",
                "s3files:ListAccessPoints", "s3files:ListMountTargets",
                "s3files:DescribeMountTargets",
            ],
            resources=["*"],  # S3 Files access is scoped by the access point policy, not the IAM resource ARN
        ))
        # X-Ray
        self.evaluator_role.add_to_policy(iam.PolicyStatement(
            sid="XRay",
            actions=[
                "xray:PutTraceSegments", "xray:PutTelemetryRecords",
                "xray:GetSamplingRules", "xray:GetSamplingTargets",
            ],
            resources=["*"],  # X-Ray does not support resource-level permissions
        ))
        # CloudWatch custom metrics
        self.evaluator_role.add_to_policy(iam.PolicyStatement(
            sid="CWMetrics",
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],  # PutMetricData does not support resource-level permissions
        ))

        # --- Shared network + filesystem fragments ---
        network_config = {
            "NetworkMode": "VPC",
            "NetworkModeConfig": {
                "Subnets": [s.subnet_id for s in private_subnets],
                "SecurityGroups": [security_group.security_group_id],
            },
        }
        shared_mount = {
            "S3FilesAccessPoint": {
                "AccessPointArn": access_point_arn,
                "MountPath": "/mnt/shared",
            }
        }
        session_storage = {"SessionStorage": {"MountPath": "/mnt/workspace"}}

        # IAM policy must exist before runtime creation (avoids a race on first deploy).
        policy_node = self.exec_role.node.try_find_child("DefaultPolicy")
        eval_policy_node = self.evaluator_role.node.try_find_child("DefaultPolicy")

        def _runtime(logical_id, name, image, filesystem, env, role=None):
            # Each runtime defaults to the shared coder/sandbox exec role; the evaluator
            # passes its own least-privilege role (mirrors RUNTIME_ROLE_ARN override in
            # deploy/30_create_runtime.sh).
            role = role or self.exec_role
            r = cdk.CfnResource(
                self,
                logical_id,
                type="AWS::BedrockAgentCore::Runtime",
                properties={
                    "AgentRuntimeName": name,
                    "AgentRuntimeArtifact": {
                        "ContainerConfiguration": {"ContainerUri": image},
                    },
                    "RoleArn": role.role_arn,
                    "NetworkConfiguration": network_config,
                    "FilesystemConfigurations": filesystem,
                    "EnvironmentVariables": env,
                },
            )
            r.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
            # Depend on whichever role's inline policy this runtime uses.
            dep = eval_policy_node if role is self.evaluator_role else policy_node
            if dep is not None:
                r.node.add_dependency(dep)
            return r

        # --- Python sandbox (S3 mount + sessionStorage) ---
        self.sandbox_runtime = _runtime(
            "SandboxRuntime", f"{project}_sandbox", sandbox_image,
            [shared_mount, session_storage],
            {
                "MOUNT_PATH": "/mnt/shared",
                "WORKSPACE_PATH": "/mnt/workspace",
                "SANDBOX_LANG": "python",
                "BUCKET": bucket_name,
                "COMPONENT_NAME": "sandbox",
            },
        )

        # --- Swift sandbox (S3 mount + sessionStorage; SwiftPM .build persists) ---
        self.sandbox_swift_runtime = _runtime(
            "SandboxSwiftRuntime", f"{project}_sandbox_swift", sandbox_swift_image,
            [shared_mount, session_storage],
            {
                "MOUNT_PATH": "/mnt/shared",
                "WORKSPACE_PATH": "/mnt/workspace",
                "SANDBOX_LANG": "swift",
                "BUCKET": bucket_name,
                "COMPONENT_NAME": "sandbox-swift",
            },
        )

        # --- Coding agent (S3 mount; delegates execution to a sandbox) ---
        # SANDBOX_ARN points at the python sandbox by default; for swift tickets the
        # orchestrator passes the swift sandbox ARN in the invoke payload.
        self.coding_agent_runtime = _runtime(
            "CodingAgentRuntime", f"{project}_coding_agent", coding_agent_image,
            [shared_mount],
            {
                "MOUNT_PATH": "/mnt/shared",
                "SANDBOX_ARN": cdk.Token.as_string(
                    self.sandbox_runtime.get_att("AgentRuntimeArn")
                ),
                "BEDROCK_MODEL": bedrock_model,
                "COMPONENT_NAME": "coding-agent",
            },
        )
        self.coding_agent_runtime.add_dependency(self.sandbox_runtime)

        # --- Evaluator agent (standalone: own image + own least-privilege read-only role) ---
        # Separate ECR image (evaluator-agent), its own read-only IAM role (no
        # InvokeAgentRuntime/Memory/command/S3-write), and its own runtime/logs. Mounts
        # /mnt/shared read-only to read the implementation. Opus 4.8 — no REVIEW_MODE flag.
        self.evaluator_runtime = _runtime(
            "EvaluatorRuntime", f"{project}_evaluator", evaluator_image,
            [shared_mount],
            {
                "MOUNT_PATH": "/mnt/shared",
                "BEDROCK_MODEL": evaluator_model,
                "COMPONENT_NAME": "evaluator",
            },
            role=self.evaluator_role,
        )

        # --- Exports ---
        self.coding_agent_arn = cdk.Token.as_string(
            self.coding_agent_runtime.get_att("AgentRuntimeArn"))
        self.sandbox_arn = cdk.Token.as_string(
            self.sandbox_runtime.get_att("AgentRuntimeArn"))
        self.sandbox_swift_arn = cdk.Token.as_string(
            self.sandbox_swift_runtime.get_att("AgentRuntimeArn"))
        self.evaluator_arn = cdk.Token.as_string(
            self.evaluator_runtime.get_att("AgentRuntimeArn"))

        # --- SSM parameters: /<project>/runtime/<key> = runtime ARN ---
        # The orchestrator reads these at invocation time (see orchestrator/handler.py).
        for key, arn in {
            "coding_agent": self.coding_agent_arn,
            "sandbox": self.sandbox_arn,
            "sandbox_swift": self.sandbox_swift_arn,
            "evaluator": self.evaluator_arn,
        }.items():
            ssm.StringParameter(
                self,
                f"RuntimeArnParam-{key}",
                parameter_name=f"/{project}/runtime/{key}",
                string_value=arn,
            )

        for label, value in {
            "CodingAgentArn": self.coding_agent_arn,
            "SandboxArn": self.sandbox_arn,
            "SandboxSwiftArn": self.sandbox_swift_arn,
            "EvaluatorArn": self.evaluator_arn,
        }.items():
            cdk.CfnOutput(
                self,
                label,
                value=value,
                export_name=f"{project}-{label.lower()}",
            )
