# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""CDK application entry point."""

import aws_cdk as cdk

from config import CdkConfig
from stacks.lattice_stack import LatticeStack
from stacks.gateway_infra_stack import GatewayInfraStack
from stacks.ping_federate_stack import PingFederateStack
from stacks.vpc_stack import VpcStack

app = cdk.App()

config = CdkConfig(
    aws_account=app.node.try_get_context("aws_account") or None,
)

env = cdk.Environment(
    account=config.aws_account,
    region=config.aws_region,
)

# Stack 1: VPC (separate stack for clean deletion — Lattice ENIs can take 8 hours to release)
vpc_stack = VpcStack(
    app,
    "PrivateIdpVpcStack",
    env=env,
)

# Stack 2: PingFederate IdP (ECS Fargate, internal ALB, public ACM cert)
ping_stack = PingFederateStack(
    app,
    "PrivateIdpPingFederateStack",
    vpc=vpc_stack.vpc,
    config=config,
    env=env,
)
ping_stack.add_dependency(vpc_stack)

# Stack 3: Gateway infrastructure (MCP Echo Lambda + IAM role)
gateway_infra_stack = GatewayInfraStack(
    app,
    "PrivateIdpGatewayInfraStack",
    env=env,
)

# Stack 4: VPC Lattice (optional — only with --self-managed-lattice flag)
if config.deploy_lattice:
    lattice_stack = LatticeStack(
        app,
        "PrivateIdpLatticeStack",
        vpc=vpc_stack.vpc,
        alb=ping_stack.alb,
        alb_listener=ping_stack.alb_listener,
        suffix=config.suffix,
        env=env,
    )
    lattice_stack.add_dependency(ping_stack)

app.synth()
