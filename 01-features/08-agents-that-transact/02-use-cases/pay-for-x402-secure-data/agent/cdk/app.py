#!/usr/bin/env python3
"""CDK app entry point for the Pay for Secure Data (x402) agent runtime."""

import os

import aws_cdk as cdk
from agent_stack import AgentCorePaymentsX402SecureDataAgentStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get(
        "CDK_DEFAULT_REGION",
        os.environ.get("AWS_REGION", "us-west-2"),
    ),
)

AgentCorePaymentsX402SecureDataAgentStack(
    app,
    "AgentCorePaymentsX402SecureDataAgentStack",
    env=env,
    description=(
        "AgentCore payments sample — Pay for Secure Data (x402) agent "
        "(Strands Agent + AgentCorePaymentsPlugin + t54 x402-secure trust "
        "gate, deployed to AgentCore Runtime)"
    ),
)

app.synth()
