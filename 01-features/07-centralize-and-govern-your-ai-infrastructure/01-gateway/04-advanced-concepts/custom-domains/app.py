# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
import os

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks
from custom_domains.custom_domains_stack import CustomDomainsStack

app = cdk.App()
cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))

# CloudFront ACM certs, WAF (CLOUDFRONT scope), and Lambda@Edge all require
# us-east-1. Account comes from the CDK/AWS environment; override with
# CDK_DEFAULT_ACCOUNT if needed.
CustomDomainsStack(
    app,
    "CustomDomainsStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region="us-east-1",
    ),
)

app.synth()
