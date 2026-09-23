# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Application Load Balancer construct."""

from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_s3 as s3
from constructs import Construct


class Alb(Construct):
    """Application Load Balancer with security group."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        vpc: ec2.IVpc,
        certificate: acm.ICertificate,
        access_logs_bucket: s3.IBucket,
    ):
        """Initialize ALB with HTTPS listener."""
        super().__init__(scope, id)

        self.security_group = ec2.SecurityGroup(
            self,
            "AlbSg",
            vpc=vpc,
            description="ALB security group",
            allow_all_outbound=True,
        )
        self.security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "Allow HTTPS",
        )

        _ = self.security_group.node.default_child

        self.alb = elbv2.ApplicationLoadBalancer(
            self,
            "ALB",
            vpc=vpc,
            internet_facing=True,
            security_group=self.security_group,
            drop_invalid_header_fields=True,
        )
        self.alb.log_access_logs(access_logs_bucket, prefix="alb")

        self.alb.add_listener(
            "HttpListener",
            port=80,
            open=False,
            default_action=elbv2.ListenerAction.redirect(
                protocol="HTTPS",
                port="443",
                permanent=True,
            ),
        )

        self.listener = self.alb.add_listener(
            "HttpsListener",
            port=443,
            certificates=[certificate],
            ssl_policy=elbv2.SslPolicy.TLS12,
        )
