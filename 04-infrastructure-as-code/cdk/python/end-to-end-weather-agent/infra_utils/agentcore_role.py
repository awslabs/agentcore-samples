from aws_cdk import aws_iam as iam, Stack
from constructs import Construct


class AgentCoreRole(iam.Role):
    def __init__(self, scope: Construct, construct_id: str, s3_bucket_arn: str = None, **kwargs):
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
                resources=[f"arn:aws:ecr:{region}:{account_id}:repository/*"],
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
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{region}:{account_id}:*",
                ],
            ),
            # Browser Tool permissions
            iam.PolicyStatement(
                sid="BrowserToolAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:StartBrowserSession",
                    "bedrock-agentcore:StopBrowserSession",
                    "bedrock-agentcore:InvokeBrowser",
                    "bedrock-agentcore:ListBrowserSessions",
                    "bedrock-agentcore:TerminateBrowserSession",
                ],
                resources=[f"arn:aws:bedrock-agentcore:{region}:{account_id}:browser/*"],
            ),
            # Code Interpreter permissions
            iam.PolicyStatement(
                sid="CodeInterpreterAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:StartCodeInterpreterSession",
                    "bedrock-agentcore:StopCodeInterpreterSession",
                    "bedrock-agentcore:InvokeCodeInterpreter",
                    "bedrock-agentcore:ListCodeInterpreterSessions",
                ],
                resources=[f"arn:aws:bedrock-agentcore:{region}:{account_id}:code-interpreter/*"],
            ),
            # Memory permissions
            iam.PolicyStatement(
                sid="MemoryAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:PutEvents",
                    "bedrock-agentcore:GetEvents",
                ],
                resources=[f"arn:aws:bedrock-agentcore:{region}:{account_id}:memory/*"],
            ),
            # AWS CLI permissions for use_aws tool
            iam.PolicyStatement(
                sid="AWSCLIAccess",
                effect=iam.Effect.ALLOW,
                actions=["sts:GetCallerIdentity", "sts:AssumeRole"],
                resources=["*"],
            ),
        ]

        # Add S3 permissions if bucket ARN provided
        if s3_bucket_arn:
            statements.append(
                iam.PolicyStatement(
                    sid="S3BucketAccess",
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:ListBucket",
                    ],
                    resources=[s3_bucket_arn, f"{s3_bucket_arn}/*"],
                )
            )

        super().__init__(
            scope,
            construct_id,
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            inline_policies={"AgentCorePolicy": iam.PolicyDocument(statements=statements)},
            **kwargs,
        )
