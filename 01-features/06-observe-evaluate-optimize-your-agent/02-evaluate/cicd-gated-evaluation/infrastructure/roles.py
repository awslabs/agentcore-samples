# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from constructs import Construct


class MCPServerRole(Construct):
    def __init__(self, scope: Construct, construct_id: str, description: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        stack = cdk.Stack.of(self)
        region = stack.region
        account = stack.account

        self.role = iam.Role(
            self,
            "Role",
            description=description,
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            inline_policies={
                "MCPServerPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                            resources=[f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*"],
                        ),
                        iam.PolicyStatement(
                            # DescribeLogGroups is a list operation: it is evaluated against
                            # the log-group collection, not an individual group, so naming
                            # one group would deny the call. Held to this account and region
                            # and paired with write-only access to the runtime's own group.
                            actions=["logs:DescribeLogGroups"],
                            resources=[f"arn:aws:logs:{region}:{account}:log-group:*"],
                        ),
                        iam.PolicyStatement(
                            actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                            resources=[
                                f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
                            ],
                        ),
                        iam.PolicyStatement(
                            sid="ECRTokenAccess",
                            # GetAuthorizationToken returns the registry credential itself
                            # and so has no repository to scope to; AWS accepts only "*".
                            # Pulls are restricted separately by ECRImageAccess above.
                            actions=["ecr:GetAuthorizationToken"],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            sid="ECRImageAccess",
                            actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                            resources=[f"arn:aws:ecr:{region}:{account}:repository/*"],
                        ),
                        iam.PolicyStatement(
                            # X-Ray write APIs are account-level and accept no resource
                            # qualifier: segments are addressed by trace id, which does not
                            # exist until runtime, and the sampling-rule reads are global.
                            # AWS documents "*" as the only valid resource for these four
                            # actions, so this cannot be narrowed further. Scope is instead
                            # bounded by the action list — write-and-sample only, no read
                            # of other traces.
                            actions=[
                                "xray:PutTraceSegments",
                                "xray:PutTelemetryRecords",
                                "xray:GetSamplingRules",
                                "xray:GetSamplingTargets",
                            ],
                            resources=["*"],
                        ),
                    ]
                )
            },
        )


class AgentCoreRuntimeRole(Construct):
    # Cross-region inference profile used by the agent. The "au." prefix routes to the
    # foundation model in the regions listed below (verified via
    # `aws bedrock get-inference-profile`), so InvokeModel must be granted on both the
    # profile ARN and the underlying foundation model in each of those regions.
    DEFAULT_MODEL_ID = "au.anthropic.claude-haiku-4-5-20251001-v1:0"
    INFERENCE_PROFILE_REGIONS = ("ap-southeast-2", "ap-southeast-4")

    # Known cross-region routing prefixes. Matched explicitly rather than inferred: a
    # provider name is also alphabetic and dot-separated, so a heuristic would strip
    # "anthropic." from a plain model id such as "anthropic.claude-3-..." and build an
    # invalid foundation-model ARN, silently breaking InvokeModel authorization.
    # Extend this tuple if AWS introduces further prefixes.
    CROSS_REGION_PREFIXES = ("au", "us", "eu", "apac", "global")

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        description: str,
        model_id: str = DEFAULT_MODEL_ID,
        a2a_target_runtime_arns: list[str] | None = None,
        guardrail_arns: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        stack = cdk.Stack.of(self)
        region = stack.region
        account = stack.account

        # Strip the cross-region routing prefix ("au.") to get the base foundation model id.
        prefix, _, rest = model_id.partition(".")
        base_model_id = rest if prefix in self.CROSS_REGION_PREFIXES else model_id
        bedrock_model_resources = [
            f"arn:aws:bedrock:{region}:{account}:inference-profile/{model_id}",
            *[f"arn:aws:bedrock:{r}::foundation-model/{base_model_id}" for r in self.INFERENCE_PROFILE_REGIONS],
        ]

        # InvokeAgentRuntime targets. Each runtime ARN also needs its endpoint sub-resource
        # (".../runtime-endpoint/*") because invocations address a qualifier.
        if a2a_target_runtime_arns:
            a2a_resources = [
                arn_part for arn in a2a_target_runtime_arns for arn_part in (arn, f"{arn}/runtime-endpoint/*")
            ]
        else:
            # No known targets: grant nothing rather than the whole account.
            a2a_resources = [f"arn:aws:bedrock-agentcore:{region}:{account}:agent-runtime/__none__"]

        # ApplyGuardrail is granted only on the guardrails this agent is configured with.
        # Without a guardrail the grant resolves to a non-existent ARN rather than "*", so
        # adding a guardrail later is an explicit change rather than a silent widening.
        guardrail_resources = guardrail_arns or [f"arn:aws:bedrock:{region}:{account}:guardrail/__none__"]

        self.role = iam.Role(
            self,
            "Role",
            description=description,
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            inline_policies={
                "AgentCoreRuntimePolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="ECRImageAccess",
                            actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                            resources=[f"arn:aws:ecr:{region}:{account}:repository/*"],
                        ),
                        iam.PolicyStatement(
                            sid="ECRTokenAccess",
                            # GetAuthorizationToken returns the registry credential itself
                            # and so has no repository to scope to; AWS accepts only "*".
                            # Pulls are restricted separately by ECRImageAccess above.
                            actions=["ecr:GetAuthorizationToken"],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            actions=["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                            resources=[f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*"],
                        ),
                        iam.PolicyStatement(
                            # DescribeLogGroups is a list operation: it is evaluated against
                            # the log-group collection, not an individual group, so naming
                            # one group would deny the call. Held to this account and region
                            # and paired with write-only access to the runtime's own group.
                            actions=["logs:DescribeLogGroups"],
                            resources=[f"arn:aws:logs:{region}:{account}:log-group:*"],
                        ),
                        iam.PolicyStatement(
                            actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                            resources=[
                                f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
                            ],
                        ),
                        iam.PolicyStatement(
                            # X-Ray write APIs are account-level and accept no resource
                            # qualifier: segments are addressed by trace id, which does not
                            # exist until runtime, and the sampling-rule reads are global.
                            # AWS documents "*" as the only valid resource for these four
                            # actions, so this cannot be narrowed further. Scope is instead
                            # bounded by the action list — write-and-sample only, no read
                            # of other traces.
                            actions=[
                                "xray:PutTraceSegments",
                                "xray:PutTelemetryRecords",
                                "xray:GetSamplingRules",
                                "xray:GetSamplingTargets",
                            ],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            # PutMetricData takes no resource ARN — CloudWatch authorises it
                            # by namespace instead, which the condition below pins. The
                            # wildcard resource is therefore required, and the effective
                            # scope is the single namespace named in the condition.
                            actions=["cloudwatch:PutMetricData"],
                            resources=["*"],
                            conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
                        ),
                        iam.PolicyStatement(
                            sid="BedrockModelInvocation",
                            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                            resources=bedrock_model_resources,
                        ),
                        iam.PolicyStatement(
                            sid="BedrockGuardrail",
                            actions=["bedrock:ApplyGuardrail"],
                            resources=guardrail_resources,
                        ),
                        iam.PolicyStatement(
                            sid="A2AInvocation",
                            actions=["bedrock-agentcore:InvokeAgentRuntime"],
                            # Scoped to the specific target runtime(s) this agent calls.
                            # The MCP runtime ARN is known at synth time, so the previous
                            # account-wide agent-runtime/* grant was not required.
                            resources=a2a_resources,
                        ),
                    ]
                )
            },
        )

        self.role.assume_role_policy.add_statements(
            iam.PolicyStatement(
                sid="AssumeRolePolicy",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("bedrock-agentcore.amazonaws.com")],
                actions=["sts:AssumeRole"],
                conditions={
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:*"},
                },
            )
        )
