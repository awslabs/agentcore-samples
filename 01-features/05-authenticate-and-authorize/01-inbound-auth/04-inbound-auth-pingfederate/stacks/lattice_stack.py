# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""VPC Lattice stack — resource gateway and resource configuration for private IdP connectivity."""

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_vpclattice as vpclattice
from constructs import Construct


class LatticeStack(Stack):
    """Create VPC Lattice resources that expose the internal PingFederate ALB to AgentCore Identity.

    This stack creates:
    1. A Resource Gateway — ENIs in the VPC that serve as the ingress point for Lattice traffic.
    2. A Resource Configuration — describes the PingFederate ALB (DNS + port) so that
       AgentCore Identity can reach it privately via the ``selfManagedLatticeResource``
       attribute on the OAuth2 credential provider.
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        vpc: ec2.IVpc,
        alb: elbv2.IApplicationLoadBalancer,
        alb_listener: elbv2.IApplicationListener,
        suffix: str,
        **kwargs,
    ):
        """Initialize Lattice stack."""
        super().__init__(scope, id, **kwargs)

        # Security group for the resource gateway — allows HTTPS traffic from Lattice to the ALB
        gw_sg = ec2.SecurityGroup(
            self,
            "ResourceGatewaySg",
            vpc=vpc,
            description="VPC Lattice resource gateway security group",
            allow_all_outbound=True,
        )
        gw_sg.add_ingress_rule(ec2.Peer.ipv4(vpc.vpc_cidr_block), ec2.Port.tcp(443), "HTTPS from VPC")

        # Resource Gateway — place ENIs in the private subnets of the VPC where PingFederate runs.
        private_subnet_ids = [s.subnet_id for s in vpc.private_subnets]

        resource_gateway = vpclattice.CfnResourceGateway(
            self,
            "ResourceGateway",
            name=f"ping-idp-gw-{suffix}",
            vpc_identifier=vpc.vpc_id,
            subnet_ids=private_subnet_ids,
            security_group_ids=[gw_sg.security_group_id],
            ip_address_type="IPV4",
        )

        # Resource Configuration — a SINGLE resource pointing to the internal ALB by DNS name.
        # AgentCore Identity uses the resourceConfigurationIdentifier (rcfg-xxx) to reach
        # PingFederate privately through VPC Lattice.
        resource_config = vpclattice.CfnResourceConfiguration(
            self,
            "ResourceConfiguration",
            name=f"ping-idp-rcfg-{suffix}",
            resource_configuration_type="SINGLE",
            protocol_type="TCP",
            port_ranges=["443"],
            resource_gateway_id=resource_gateway.attr_id,
            resource_configuration_definition=vpclattice.CfnResourceConfiguration.ResourceConfigurationDefinitionProperty(
                dns_resource=vpclattice.CfnResourceConfiguration.DnsResourceProperty(
                    domain_name=alb.load_balancer_dns_name,
                    ip_address_type="IPV4",
                ),
            ),
            allow_association_to_sharable_service_network=True,
        )
        resource_config.add_dependency(resource_gateway)

        # The resource configuration ID (rcfg-xxx) is what AgentCore Identity needs
        self.resource_configuration_id = resource_config.attr_id

        CfnOutput(
            self,
            "ResourceGatewayId",
            value=resource_gateway.attr_id,
            description="VPC Lattice Resource Gateway ID",
        )
        CfnOutput(
            self,
            "ResourceConfigurationId",
            value=resource_config.attr_id,
            description="VPC Lattice Resource Configuration ID — use this in the AgentCore Identity OAuth2 provider",
        )
