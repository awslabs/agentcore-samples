# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Main CDK stack for the agent application."""

from aws_cdk import Stack
from constructs import Construct

from cdk.constructs import Agent
from config import CdkConfig


class AgentStack(Stack):
    """Main stack that deploys the Agent construct."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: CdkConfig,
        workload_identity_name: str,
        **kwargs,
    ):
        """Initialize agent stack."""
        super().__init__(scope, id, **kwargs)

        Agent(
            self,
            "Agent",
            config=config,
            workload_identity_name=workload_identity_name,
        )
