# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CDK entry point — deploys both MCP server and Assistant agent with shared Cognito pool."""

import os

import aws_cdk as cdk

from infrastructure.stack import CombinedStack

app = cdk.App()
CombinedStack(
    app,
    "AgentCoreCICDStack-dev",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region="ap-southeast-2",
    ),
)
app.synth()
