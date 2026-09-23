# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""VPC stack — shared network infrastructure for PingFederate and VPC Lattice."""

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class VpcStack(Stack):
    """Create a VPC with public and private subnets.

    This stack is separate from PingFederateStack so that it can be deleted
    independently. VPC Lattice resource gateways create ENIs that can take
    up to 8 hours to release after deletion — separating the VPC allows users
    to delete other stacks first, then retry VPC deletion later.
    """

    def __init__(self, scope: Construct, id: str, **kwargs):
        """Initialize VPC stack."""
        super().__init__(scope, id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                    map_public_ip_on_launch=False,
                ),
            ],
        )

        CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
        CfnOutput(
            self,
            "PrivateSubnetIds",
            value=",".join([s.subnet_id for s in self.vpc.private_subnets]),
            description="Private subnet IDs (for AgentCore Identity managedVpcResource)",
        )
