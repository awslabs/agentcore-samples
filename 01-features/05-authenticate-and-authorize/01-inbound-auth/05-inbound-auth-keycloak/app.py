# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""CDK application entry point."""

import aws_cdk as cdk

from config import CdkConfig
from stacks.gateway_infra_stack import GatewayInfraStack
from stacks.keycloak_stack import KeycloakStack
from stacks.vpc_stack import VpcStack

app = cdk.App()
config = CdkConfig(aws_account=app.node.try_get_context("aws_account") or None)
env = cdk.Environment(account=config.aws_account, region=config.aws_region)

# Stack 1: VPC (separate stack for clean deletion — Lattice ENIs can take up to 8 hours to release)
vpc_stack = VpcStack(app, "PrivateIdpKeycloakVpcStack", env=env)

# Stack 2: Keycloak IdP (private CA, private ACM certificate, internal ALB, ECS Fargate, private DNS)
keycloak_stack = KeycloakStack(app, "PrivateIdpKeycloakStack", vpc=vpc_stack.vpc, config=config, env=env)
keycloak_stack.add_dependency(vpc_stack)

# Stack 3: Gateway infrastructure (MCP Echo Lambda + IAM role)
GatewayInfraStack(app, "PrivateIdpKeycloakGatewayInfraStack", env=env)

app.synth()
