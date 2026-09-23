# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Storage construct combining and S3."""

from aws_cdk import Aws
from aws_cdk import aws_kms as kms
from constructs import Construct

from cdk.constructs.storage.s3 import S3Bucket


class Storage(Construct):
    """Storage construct and S3 buckets."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        suffix: str,
        kms_key: kms.Key,
        session_expiration_days: int = 90,
    ):
        """Initialize storage construct."""
        super().__init__(scope, id)

        account_id = Aws.ACCOUNT_ID

        s3_construct = S3Bucket(
            self,
            "S3",
            suffix=suffix,
            account_id=account_id,
            session_expiration_in_days=session_expiration_days,
            kms_key=kms_key,
        )
        self.access_logs_bucket = s3_construct.access_logs_bucket
        self.sessions_bucket = s3_construct.sessions_bucket
