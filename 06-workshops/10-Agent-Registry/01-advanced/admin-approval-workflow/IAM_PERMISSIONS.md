# IAM Permissions for Admin Approval Workflow sample

Create IAM user or role with the following permissions.

> **Before using these policies**, replace every occurrence of `YOUR_ACCOUNT_ID` with your 12-digit AWS account ID.
> Run the following command to find it:
> ```bash
> aws sts get-caller-identity --query Account --output text
> ```
> Then do a find-and-replace of `YOUR_ACCOUNT_ID` in the JSON below before attaching the policy.

## Policy for AWS Agent Registry access (Administrator)

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AllowCreatingAndListingRegistries",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:CreateRegistry",
                "bedrock-agentcore:ListRegistries"
            ],
            "Resource": ["arn:aws:bedrock-agentcore:*:YOUR_ACCOUNT_ID:*"]
        },
        {
            "Sid": "AllowGetUpdateDeleteRegistry",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:GetRegistry",
                "bedrock-agentcore:UpdateRegistry",
                "bedrock-agentcore:DeleteRegistry"
            ],
            "Resource": ["arn:aws:bedrock-agentcore:*:YOUR_ACCOUNT_ID:registry/*"]
        },
        {
            "Sid": "AllowCreatingAndListingRegistryRecords",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:CreateRegistryRecord",
                "bedrock-agentcore:ListRegistryRecords"
            ],
            "Resource": ["arn:aws:bedrock-agentcore:*:YOUR_ACCOUNT_ID:registry/*"]
        },
        {
            "Sid": "AllowRecordLevelOperations",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:GetRegistryRecord",
                "bedrock-agentcore:UpdateRegistryRecord",
                "bedrock-agentcore:DeleteRegistryRecord",
                "bedrock-agentcore:SubmitRegistryRecordForApproval"
            ],
            "Resource": ["arn:aws:bedrock-agentcore:*:YOUR_ACCOUNT_ID:registry/*/record/*"]
        },
        {
            "Sid": "AllowApproveRejectDeprecateRecords",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:UpdateRegistryRecordStatus"],
            "Resource": ["arn:aws:bedrock-agentcore:*:YOUR_ACCOUNT_ID:registry/*/record/*"]
        },
        {
            "Sid": "AdditionalPermissionForRegistryManagedWorkloadIdentity",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:*WorkloadIdentity"],
            "Resource": ["arn:aws:bedrock-agentcore:*:YOUR_ACCOUNT_ID:workload-identity-directory/default/workload-identity/*"]
        }
    ]
}
```

## Policy for AWS Agent Registry access (Publisher)

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AllowListingAllRegistries",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:ListRegistries"],
            "Resource": ["arn:aws:bedrock-agentcore:*:YOUR_ACCOUNT_ID:*"]
        },
        {
            "Sid": "AllowGetRegistry",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:GetRegistry"],
            "Resource": ["arn:aws:bedrock-agentcore:*:YOUR_ACCOUNT_ID:registry/*"]
        },
        {
            "Sid": "AllowCreatingAndListingRegistryRecords",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:CreateRegistryRecord",
                "bedrock-agentcore:ListRegistryRecords"
            ],
            "Resource": ["arn:aws:bedrock-agentcore:*:YOUR_ACCOUNT_ID:registry/*"]
        },
        {
            "Sid": "AllowRecordLevelOperations",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:GetRegistryRecord",
                "bedrock-agentcore:UpdateRegistryRecord",
                "bedrock-agentcore:DeleteRegistryRecord",
                "bedrock-agentcore:SubmitRegistryRecordForApproval"
            ],
            "Resource": ["arn:aws:bedrock-agentcore:*:YOUR_ACCOUNT_ID:registry/*/record/*"]
        }
    ]
}
```

## Permissions Required to deploy the required CI/CD stack such as DynamoDB and AWS Lambda etc.

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "STSCallerIdentity",
            "Effect": "Allow",
            "Action": ["sts:GetCallerIdentity"],
            "Resource": "*"
        },
        {
            "Sid": "CloudFormationValidate",
            "Effect": "Allow",
            "Action": ["cloudformation:ValidateTemplate"],
            "Resource": "*"
        },
        {
            "Sid": "CloudFormationStackManagement",
            "Effect": "Allow",
            "Action": [
                "cloudformation:CreateStack",
                "cloudformation:UpdateStack",
                "cloudformation:DeleteStack",
                "cloudformation:DescribeStacks",
                "cloudformation:DescribeStackEvents",
                "cloudformation:DescribeStackResources",
                "cloudformation:GetTemplate",
                "cloudformation:ListStackResources",
                "cloudformation:CreateChangeSet",
                "cloudformation:DescribeChangeSet",
                "cloudformation:ExecuteChangeSet",
                "cloudformation:DeleteChangeSet"
            ],
            "Resource": "arn:aws:cloudformation:*:YOUR_ACCOUNT_ID:stack/*/*"
        },
        {
            "Sid": "S3StagingBucketManagement",
            "Effect": "Allow",
            "Action": [
                "s3:CreateBucket",
                "s3:DeleteBucket",
                "s3:HeadBucket",
                "s3:PutBucketPublicAccessBlock",
                "s3:GetBucketPublicAccessBlock",
                "s3:ListBucket",
                "s3:DeleteObject",
                "s3:PutObject",
                "s3:GetObject"
            ],
            "Resource": [
                "arn:aws:s3:::*",
                "arn:aws:s3:::*/*"
            ]
        },
        {
            "Sid": "LambdaFunctionManagement",
            "Effect": "Allow",
            "Action": [
                "lambda:CreateFunction",
                "lambda:UpdateFunctionCode",
                "lambda:UpdateFunctionConfiguration",
                "lambda:DeleteFunction",
                "lambda:GetFunction",
                "lambda:GetFunctionConfiguration",
                "lambda:AddPermission",
                "lambda:RemovePermission"
            ],
            "Resource": "arn:aws:lambda:*:YOUR_ACCOUNT_ID:function:*"
        },
        {
            "Sid": "LambdaLayerManagement",
            "Effect": "Allow",
            "Action": [
                "lambda:PublishLayerVersion",
                "lambda:DeleteLayerVersion",
                "lambda:GetLayerVersion",
                "lambda:ListLayerVersions"
            ],
            "Resource": "arn:aws:lambda:*:YOUR_ACCOUNT_ID:layer:*"
        },
        {
            "Sid": "IAMRoleManagement",
            "Effect": "Allow",
            "Action": [
                "iam:CreateRole",
                "iam:DeleteRole",
                "iam:GetRole",
                "iam:PassRole",
                "iam:AttachRolePolicy",
                "iam:DetachRolePolicy",
                "iam:PutRolePolicy",
                "iam:DeleteRolePolicy",
                "iam:GetRolePolicy",
                "iam:ListRolePolicies",
                "iam:ListAttachedRolePolicies"
            ],
            "Resource": "arn:aws:iam::YOUR_ACCOUNT_ID:role/*"
        },
        {
            "Sid": "KMSCreateKey",
            "Effect": "Allow",
            "Action": ["kms:CreateKey"],
            "Resource": "*"
        },
        {
            "Sid": "KMSManageTaggedKeys",
            "Effect": "Allow",
            "Action": [
                "kms:DescribeKey",
                "kms:EnableKeyRotation",
                "kms:GetKeyPolicy",
                "kms:PutKeyPolicy",
                "kms:ScheduleKeyDeletion",
                "kms:CancelKeyDeletion",
                "kms:TagResource",
                "kms:UntagResource"
            ],
            "Resource": "*"
        },
        {
            "Sid": "DynamoDBTableManagement",
            "Effect": "Allow",
            "Action": [
                "dynamodb:CreateTable",
                "dynamodb:DeleteTable",
                "dynamodb:DescribeTable",
                "dynamodb:UpdateTable",
                "dynamodb:DescribeContinuousBackups",
                "dynamodb:DescribeTimeToLive"
            ],
            "Resource": "arn:aws:dynamodb:*:YOUR_ACCOUNT_ID:table/*"
        },
        {
            "Sid": "EventBridgeManagement",
            "Effect": "Allow",
            "Action": [
                "events:PutRule",
                "events:DeleteRule",
                "events:DescribeRule",
                "events:PutTargets",
                "events:RemoveTargets",
                "events:ListTargetsByRule"
            ],
            "Resource": "arn:aws:events:*:YOUR_ACCOUNT_ID:rule/*"
        }
    ]
}
```


