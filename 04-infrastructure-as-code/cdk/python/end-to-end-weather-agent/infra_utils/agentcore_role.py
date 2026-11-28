from aws_cdk import Stack
from aws_cdk import aws_iam as iam
from constructs import Construct


class AgentCoreRole(iam.Role):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        s3_bucket_arn: str,
        browser_arn: str,
        interpreter_arn: str,
        memory_arn: str,
        repository_arn: str,
        **kwargs,
    ):
        region = Stack.of(scope).region
        account_id = Stack.of(scope).account

        statements = [
            iam.PolicyStatement(
                sid="ECRImageAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchCheckLayerAvailability",
                ],
                resources=[repository_arn],
            ),
            iam.PolicyStatement(
                sid="ECRTokenAccess",
                effect=iam.Effect.ALLOW,
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            ),
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:DescribeLogStreams",
                    "logs:CreateLogGroup",
                    "logs:DescribeLogGroups",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"],
            ),
            iam.PolicyStatement(
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
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/*",
                ],
            ),
            iam.PolicyStatement(
                sid="BedrockModelInvocation",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    f"arn:aws:bedrock:{region}:{account_id}:inference-profile/global.anthropic.claude-sonnet-4-6",
                    f"arn:aws:bedrock:{region}:{account_id}:inference-profile/global.anthropic.claude-haiku-4-5-20251001-v1:0",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                ],
            ),
            # Browser Tool permissions
            iam.PolicyStatement(
                sid="BrowserToolAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:StartBrowserSession",
                    "bedrock-agentcore:StopBrowserSession",
                ],
                resources=[browser_arn],
            ),
            iam.PolicyStatement(
                sid="BrowserWebSocket",
                actions=["bedrock-agentcore:ConnectBrowserAutomationStream"],
                resources=["*"],  # The WebSocket handshake action has no IAM resource type.
                conditions={"StringEquals": {"aws:RequestedRegion": region}},
            ),
            # Code Interpreter permissions
            iam.PolicyStatement(
                sid="CodeInterpreterAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:StartCodeInterpreterSession",
                    "bedrock-agentcore:StopCodeInterpreterSession",
                    "bedrock-agentcore:InvokeCodeInterpreter",
                ],
                resources=[interpreter_arn],
            ),
            # Memory permissions
            iam.PolicyStatement(
                sid="MemoryAccess",
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:ListEvents"],
                resources=[memory_arn],
                conditions={
                    "StringEquals": {
                        "bedrock-agentcore:actorId": "user123",
                        "bedrock-agentcore:sessionId": "session456",
                    }
                },
            ),
            iam.PolicyStatement(
                sid="S3BucketAccess",
                actions=["s3:PutObject"],
                resources=[f"{s3_bucket_arn}/results.md"],
            ),
        ]

        super().__init__(
            scope,
            construct_id,
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            inline_policies={"AgentCorePolicy": iam.PolicyDocument(statements=statements)},
            **kwargs,
        )
