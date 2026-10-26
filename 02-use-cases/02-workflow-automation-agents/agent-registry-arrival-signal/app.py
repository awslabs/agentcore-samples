#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""CDK app entry point for the AWS Agent Registry arrival-signal sample.

This app defines the event infrastructure stack (U1). The two AgentCore
runtimes are deployed separately with the AgentCore CLI (see the walkthrough).

The orchestrator AgentCore Runtime attributes and the registry ARN are
CDK context values a reader supplies at deploy time (via -c on the CLI or
cdk.json's "context" block) since U2 (the orchestrator) and the registry
itself are provisioned outside this stack's own lifecycle.
"""

import aws_cdk as cdk

from agent_registry_sample.infrastructure.stack import InfrastructureStack

app = cdk.App()

InfrastructureStack(
    app,
    "ArrivalSignalInfrastructureStack",
    orchestrator_agent_runtime_arn=app.node.try_get_context("orchestratorAgentRuntimeArn")
    or "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/placeholder-orchestrator",
    orchestrator_agent_runtime_id=app.node.try_get_context("orchestratorAgentRuntimeId")
    or "placeholder-orchestrator",
    orchestrator_agent_runtime_role_arn=app.node.try_get_context("orchestratorAgentRuntimeRoleArn")
    or "arn:aws:iam::000000000000:role/placeholder-orchestrator-role",
    registry_arn=app.node.try_get_context("registryArn")
    or "arn:aws:agent-registry:us-east-1:000000000000:registry/placeholder",
    env=cdk.Environment(region="us-east-1"),
)

app.synth()
