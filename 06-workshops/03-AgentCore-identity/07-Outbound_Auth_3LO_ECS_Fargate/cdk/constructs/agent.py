# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Main Agent construct for ECS service with session binding."""

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from constructs import Construct

from config import CdkConfig

from .compute import Alb, EcsService
from .networking import Vpc
from .security import Identity, Waf
from .storage import Storage


class Agent(Construct):
    """Main Agent construct that provisions ECS service with session binding."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: CdkConfig,
        workload_identity_name: str,
        session_expiration_days: int = 90,
    ):
        """Initialize Agent construct."""
        super().__init__(scope, id)

        hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
            self,
            "HostedZone",
            zone_name=config.dns_config.domain_name,
            hosted_zone_id=config.dns_config.hosted_zone_id,
        )

        app_domain = f"agent-3lo.{config.dns_config.domain_name}"

        certificate = acm.Certificate(
            self,
            "Certificate",
            domain_name=app_domain,
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )

        vpc = Vpc(self, "Vpc")

        identity = Identity(
            self,
            "Identity",
            suffix=config.suffix,
        )

        storage = Storage(
            self,
            "Storage",
            suffix=config.suffix,
            kms_key=identity.kms_key,
            session_expiration_days=session_expiration_days,
        )

        alb = Alb(
            self,
            "Alb",
            vpc=vpc.vpc,
            certificate=certificate,
            access_logs_bucket=storage.access_logs_bucket,
        )

        Waf(
            self,
            "Waf",
            alb_arn=alb.alb.load_balancer_arn,
        )

        ecs_service = EcsService(
            self,
            "EcsService",
            vpc=vpc.vpc,
            listener=alb.listener,
            alb_security_group=alb.security_group,
            oidc_config=config.oidc_config,
            environment_vars={
                "WORKLOAD_IDENTITY_NAME": workload_identity_name,
                "AWS_REGION": Stack.of(self).region,
                "IDENTITY_AWS_REGION": config.identity_aws_region,
                "ENVIRONMENT": config.suffix,
                "S3_BUCKET_NAME": storage.sessions_bucket.bucket_name,
                "SESSION_BINDING_URL": f"https://{app_domain}/oauth2/session-binding",
                "INFERENCE_PROFILE_ID": config.inference_profile_id,
                "GITHUB_PROVIDER_NAME": config.github_provider_name,
                "GITHUB_API_BASE": config.github_api_base,
            },
            suffix=config.suffix,
            inference_profile_id=config.inference_profile_id,
            model_id=config.model_id,
            kms_key=identity.kms_key,
            identity_aws_region=config.identity_aws_region,
            workload_identity_name=workload_identity_name,
            github_provider_name=config.github_provider_name,
        )

        storage.sessions_bucket.grant_read_write(ecs_service.agent_task_role)

        identity.kms_key.grant_encrypt_decrypt(ecs_service.session_binding_task_role)

        route53.ARecord(
            self,
            "AliasRecord",
            zone=hosted_zone,
            record_name="agent-3lo",
            target=route53.RecordTarget.from_alias(targets.LoadBalancerTarget(alb.alb)),
        )

        CfnOutput(self, "AppUrl", value=f"https://{app_domain}")
        CfnOutput(self, "LoadBalancerDNS", value=alb.alb.load_balancer_dns_name)
        CfnOutput(self, "S3Bucket", value=storage.sessions_bucket.bucket_name)
