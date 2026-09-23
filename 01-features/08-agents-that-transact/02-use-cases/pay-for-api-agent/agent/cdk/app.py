#!/usr/bin/env python3
"""CDK app entry point for the Pay for API buyer agent runtime."""

import os

import aws_cdk as cdk

from agent_stack import AgentCorePaymentsBuyerAgentStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get(
        "CDK_DEFAULT_REGION",
        os.environ.get("AWS_REGION", "us-west-2"),
    ),
)

AgentCorePaymentsBuyerAgentStack(
    app,
    "AgentCorePaymentsBuyerAgentStack",
    env=env,
    description=(
        "AgentCore Payments sample — Pay for API buyer agent (Strands Agent + "
        "AgentCorePaymentsPlugin, deployed to AgentCore Runtime)"
    ),
)

app.synth()
