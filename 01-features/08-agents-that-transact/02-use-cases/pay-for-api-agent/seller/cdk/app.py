#!/usr/bin/env python3
"""CDK app entry point for the Pay for API — Fun Facts seller stack."""

import os

import aws_cdk as cdk

from seller_stack import AgentCorePaymentsFunFactsSellerStack

app = cdk.App()

# Region comes from the usual CDK resolution order:
#   CDK_DEFAULT_REGION → AWS_REGION → AWS CLI profile region.
# We default to us-west-2 to match the default AgentCore Payments region.
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", os.environ.get("AWS_REGION", "us-west-2")),
)

AgentCorePaymentsFunFactsSellerStack(
    app,
    "AgentCorePaymentsFunFactsSellerStack",
    env=env,
    description="AgentCore Payments sample — Fun Facts x402 seller (pay per API call)",
)

app.synth()
