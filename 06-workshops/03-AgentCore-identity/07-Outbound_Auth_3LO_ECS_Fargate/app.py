# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""CDK application entry point."""

import os

import aws_cdk as cdk

from cdk.identity_stack import IdentityStack
from cdk.main_stack import AgentStack
from config import CdkConfig

app = cdk.App()

config = CdkConfig(
    aws_account=app.node.try_get_context("aws_account") or os.environ.get("CDK_DEFAULT_ACCOUNT"),
)

identity_stack = IdentityStack(
    app,
    "AgentIdentityStack",
    suffix=config.suffix,
    env=cdk.Environment(
        account=config.aws_account,
        region=config.identity_aws_region,
    ),
)

agent_stack = AgentStack(
    app,
    "AgentOAuthStack",
    config=config,
    workload_identity_name=identity_stack.workload_identity.name,
    env=cdk.Environment(
        account=config.aws_account,
        region=config.aws_region,
    ),
)

agent_stack.add_dependency(identity_stack)

app.synth()
