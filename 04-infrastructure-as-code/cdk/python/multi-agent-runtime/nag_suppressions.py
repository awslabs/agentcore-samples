"""
CDK Nag suppressions for amazon-bedrock-agentcore-samples Python CDK stacks.
These are educational/tutorial templates where certain security best practices
are intentionally simplified. Production deployments should address all findings.
"""

from cdk_nag import NagSuppressions  # noqa: E402


SAMPLE_SUPPRESSIONS = [
    # Lambda
    {
        "id": "AwsSolutions-Lambda4",
        "reason": "DLQ not required for tutorial Lambda; configure in production",
    },
    {
        "id": "AwsSolutions-Lambda6",
        "reason": "Reserved concurrency not set for tutorials; configure in production",
    },
    {
        "id": "AwsSolutions-Lambda10",
        "reason": "Code signing not required for tutorial templates",
    },
    {
        "id": "AwsSolutions-L1",
        "reason": "Lambda runtime version acceptable for tutorial samples",
    },
    # IAM
    {
        "id": "AwsSolutions-IAM4",
        "reason": "AWS managed policies used for tutorial simplicity; apply least-privilege in production",
    },
    {
        "id": "AwsSolutions-IAM5",
        "reason": "IAM wildcard resources used for tutorial clarity; restrict in production",
    },
    # S3
    {
        "id": "AwsSolutions-S1",
        "reason": "S3 access logging not required for tutorial buckets; enable in production",
    },
    {
        "id": "AwsSolutions-S10",
        "reason": "S3 deny public access policy simplified for tutorials",
    },
    # DynamoDB
    {
        "id": "AwsSolutions-DDB3",
        "reason": "PITR not required for tutorial data; enable in production",
    },
    # Cognito
    {
        "id": "AwsSolutions-COG2",
        "reason": "MFA optional for tutorial; enable in production",
    },
    {
        "id": "AwsSolutions-COG3",
        "reason": "Advanced Security Mode requires Cognito Plus; optional for tutorials",
    },
    # Secrets Manager
    {
        "id": "AwsSolutions-SMG4",
        "reason": "Secret rotation not required for tutorial; enable in production",
    },
    # ECR
    {
        "id": "AwsSolutions-ECR1",
        "reason": "ECR tag mutability set to IMMUTABLE; KMS encryption optional for tutorials",
    },
    # CloudWatch
    {
        "id": "AwsSolutions-CWL3",
        "reason": "CloudWatch log group retention set; KMS CMK optional for tutorials",
    },
]


def apply_nag_suppressions(stack) -> None:
    """Apply stack-level cdk-nag suppressions for tutorial/sample CDK stacks."""
    NagSuppressions.add_stack_suppressions(stack, SAMPLE_SUPPRESSIONS)
