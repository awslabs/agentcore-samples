"""Network stack: VPC, subnets, NAT, SG, VPC endpoints.

Mirrors deploy/10_vpc.sh.

AZ constraint (IMPORTANT): AgentCore Runtime VPC mode rejects subnets in
unsupported Availability Zones. The constraint is by *zone-id*, not zone-name —
and zone-name->zone-id mapping differs per account. On the target account
(123456789012) the supported zone-ids are use1-az1, use1-az2, use1-az4, and the
live shell-script deployment runs in:
    us-east-1a -> use1-az2
    us-east-1b -> use1-az4
(verified from the live subnets in deploy/config.env). So we pin those two AZ
*names* here because on this account they resolve to the proven-working zone-ids.
NOTE: us-east-1b is use1-az4 on THIS account; do not assume 1b is unsupported by
name — verify by zone-id (`aws ec2 describe-subnets ... AvailabilityZoneId`).
"""
from typing import List

import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
)
from constructs import Construct


class NetworkStack(cdk.Stack):
    """VPC with 2 public + 2 private subnets, single NAT, SG, and VPC endpoints."""

    # The two AgentCore-supported AZs on the target account (resolve to use1-az2 +
    # use1-az4). Overridable via context -c agentcore_azs="us-east-1a,us-east-1b".
    DEFAULT_AZS = ["us-east-1a", "us-east-1b"]

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        azs_ctx = self.node.try_get_context("agentcore_azs")
        azs = (
            [a.strip() for a in azs_ctx.split(",")]
            if azs_ctx
            else self.DEFAULT_AZS
        )

        # --- VPC (restricted to 2 AgentCore-supported AZs) ---
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            vpc_name=f"{project}-vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            availability_zones=azs,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=20,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=20,
                ),
            ],
        )

        # --- Security Group (self-referencing for NFS 2049 + HTTPS 443) ---
        # 2049: S3 Files NFS mount targets <-> runtime ENIs.
        # 443:  runtime ENIs -> interface VPC endpoints (they share this SG).
        self.security_group = ec2.SecurityGroup(
            self,
            "RuntimeSg",
            vpc=self.vpc,
            security_group_name=f"{project}-runtime-sg",
            description="AgentCore runtimes + VPC endpoints (self-ref NFS + HTTPS)",
            allow_all_outbound=True,
        )
        self.security_group.add_ingress_rule(
            peer=self.security_group,
            connection=ec2.Port.tcp(2049),
            description="NFS (S3 Files mount targets)",
        )
        self.security_group.add_ingress_rule(
            peer=self.security_group,
            connection=ec2.Port.tcp(443),
            description="HTTPS (interface endpoints)",
        )

        # --- VPC Endpoints (mirror deploy/10_vpc.sh) ---
        region = cdk.Stack.of(self).region
        interface_services = {
            "bedrock-agentcore": f"com.amazonaws.{region}.bedrock-agentcore",
            "bedrock-runtime": f"com.amazonaws.{region}.bedrock-runtime",
            "ecr-api": f"com.amazonaws.{region}.ecr.api",
            "ecr-dkr": f"com.amazonaws.{region}.ecr.dkr",
            "logs": f"com.amazonaws.{region}.logs",
        }

        for name, service_name in interface_services.items():
            ec2.InterfaceVpcEndpoint(
                self,
                f"Vpce-{name}",
                vpc=self.vpc,
                service=ec2.InterfaceVpcEndpointService(service_name, 443),
                subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                ),
                security_groups=[self.security_group],
                private_dns_enabled=True,
            )

        # S3 Gateway endpoint (ECR layers + general S3) on the private route table
        ec2.GatewayVpcEndpoint(
            self,
            "Vpce-s3",
            vpc=self.vpc,
            service=ec2.GatewayVpcEndpointAwsService.S3,
            subnets=[
                ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                )
            ],
        )

        # --- Exports ---
        self.private_subnets: List[ec2.ISubnet] = (
            self.vpc.select_subnets(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ).subnets
        )

        cdk.CfnOutput(self, "VpcId", value=self.vpc.vpc_id, export_name=f"{project}-vpc-id")
        cdk.CfnOutput(
            self,
            "SecurityGroupId",
            value=self.security_group.security_group_id,
            export_name=f"{project}-sg-id",
        )
        cdk.CfnOutput(
            self,
            "PrivateSubnetIds",
            value=cdk.Fn.join(",", [s.subnet_id for s in self.private_subnets]),
            export_name=f"{project}-private-subnet-ids",
        )
