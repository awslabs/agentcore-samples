# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""KMS identity construct."""

from aws_cdk import Aws, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from constructs import Construct


class Identity(Construct):
    """KMS Key for encryption."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        suffix: str,
    ):
        """Initialize identity construct."""
        super().__init__(scope, id)

        account_id = Aws.ACCOUNT_ID
        region = Stack.of(self).region

        self.kms_key = kms.Key(
            self,
            "Key",
            alias=f"alias/agent-cwl-{suffix}",
            enable_key_rotation=True,
            policy=iam.PolicyDocument.from_json(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "EnableRootAccountPermissions",
                            "Effect": "Allow",
                            "Principal": {"AWS": f"arn:aws:iam::{account_id}:root"},
                            "Action": "kms:*",
                            "Resource": "*",
                        },
                        {
                            "Sid": "AllowCloudWatchLogsEncryption",
                            "Effect": "Allow",
                            "Principal": {"Service": f"logs.{region}.amazonaws.com"},
                            "Action": [
                                "kms:Encrypt",
                                "kms:Decrypt",
                                "kms:ReEncrypt*",
                                "kms:GenerateDataKey*",
                                "kms:DescribeKey",
                            ],
                            "Resource": "*",
                            "Condition": {
                                "ArnEquals": {
                                    "kms:EncryptionContext:aws:logs:arn": (
                                        f"arn:aws:logs:{region}:{account_id}:log-group:*"
                                    )
                                },
                            },
                        },
                    ],
                }
            ),
        )
