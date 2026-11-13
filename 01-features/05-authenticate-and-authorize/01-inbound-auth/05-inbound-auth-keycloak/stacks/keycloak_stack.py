# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Keycloak IdP stack: AWS Private CA, private ACM certificate, internal ALB, ECS Fargate, private DNS."""

import json

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_acmpca as acmpca
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from config import CdkConfig

KEYCLOAK_HTTP_PORT = 8080
CLIENT_SECRET_PLACEHOLDER = "__KEYCLOAK_CLIENT_SECRET__"


class KeycloakStack(Stack):
    """Deploy a self-hosted Keycloak IdP whose TLS certificate is issued by a private CA.

    Network design:
    - The ALB is internal. Its security group allows inbound HTTPS from the VPC Lattice endpoint security group.
    - The Keycloak security group allows inbound traffic from the ALB security group.
    - The DNS name is defined in a Route 53 private hosted zone associated with the VPC.
    """

    def __init__(self, scope: Construct, id: str, vpc: ec2.IVpc, config: CdkConfig, **kwargs):
        """Initialize Keycloak stack."""
        super().__init__(scope, id, **kwargs)
        domain = config.keycloak_domain
        record_name, zone_name = domain.split(".", 1)
        issuer = f"https://{domain}/realms/{config.realm_name}"

        # --- AWS Private CA (root CA) ---
        # ACM issues private certificates from CAs in GENERAL_PURPOSE mode, not SHORT_LIVED_CERTIFICATE mode.
        ca = acmpca.CfnCertificateAuthority(
            self,
            "PrivateCa",
            type="ROOT",
            key_algorithm="RSA_2048",
            signing_algorithm="SHA256WITHRSA",
            usage_mode="GENERAL_PURPOSE",
            subject=acmpca.CfnCertificateAuthority.SubjectProperty(
                common_name="AgentCore Keycloak Sample Root CA",
                organization="AgentCore Samples",
            ),
        )
        root_cert = acmpca.CfnCertificate(
            self,
            "PrivateCaRootCertificate",
            certificate_authority_arn=ca.attr_arn,
            certificate_signing_request=ca.attr_certificate_signing_request,
            signing_algorithm="SHA256WITHRSA",
            template_arn="arn:aws:acm-pca:::template/RootCACertificate/V1",
            validity=acmpca.CfnCertificate.ValidityProperty(type="YEARS", value=10),
        )
        activation = acmpca.CfnCertificateAuthorityActivation(
            self,
            "PrivateCaActivation",
            certificate_authority_arn=ca.attr_arn,
            certificate=root_cert.attr_certificate,
            status="ACTIVE",
        )
        # Lets ACM renew the private certificate automatically.
        acm_permission = acmpca.CfnPermission(
            self,
            "PrivateCaAcmPermission",
            certificate_authority_arn=ca.attr_arn,
            principal="acm.amazonaws.com",
            actions=["IssueCertificate", "GetCertificate", "ListPermissions"],
        )
        acm_permission.add_dependency(activation)

        # --- Private ACM certificate for the Keycloak hostname ---
        certificate = acm.PrivateCertificate(
            self,
            "KeycloakCertificate",
            domain_name=domain,
            certificate_authority=acmpca.CertificateAuthority.from_certificate_authority_arn(
                self, "PrivateCaRef", ca.attr_arn
            ),
        )
        certificate.node.add_dependency(activation)

        # --- CA certificate for AgentCore Identity (certificateConfigurations) ---
        # AgentCore Identity needs a plaintext PEM secret containing the CA certificate (not a private key).
        ca_cert_secret = secretsmanager.CfnSecret(
            self,
            "CaCertificateSecret",
            description="PEM root CA certificate of the Keycloak sample private CA (public material, no private key)",
            secret_string=root_cert.attr_certificate,
        )
        ca_cert_secret.add_dependency(activation)

        # --- Generated secrets: Keycloak admin password and M2M client secret ---
        admin_secret = secretsmanager.Secret(
            self,
            "KeycloakAdminSecret",
            description="Keycloak bootstrap admin credentials",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({"username": "admin"}),
                generate_string_key="password",
                exclude_punctuation=True,
                password_length=24,
            ),
        )
        client_secret = secretsmanager.Secret(
            self,
            "KeycloakClientSecret",
            description="Keycloak confidential client credentials for the AgentCore credential provider",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({"clientId": config.client_id}),
                generate_string_key="clientSecret",
                exclude_punctuation=True,
                password_length=32,
            ),
        )

        # --- Keycloak realm imported at startup ---
        # The client secret is injected at container start from Secrets Manager, so the template holds a placeholder.
        realm = {
            "realm": config.realm_name,
            "enabled": True,
            "accessTokenLifespan": 900,
            "clients": [
                {
                    "clientId": config.client_id,
                    "name": "AgentCore M2M client",
                    "enabled": True,
                    "protocol": "openid-connect",
                    "publicClient": False,
                    "clientAuthenticatorType": "client-secret",
                    "secret": CLIENT_SECRET_PLACEHOLDER,
                    "serviceAccountsEnabled": True,
                    "standardFlowEnabled": False,
                    "directAccessGrantsEnabled": False,
                    "implicitFlowEnabled": False,
                    "protocolMappers": [
                        {
                            "name": "agentcore-gateway-audience",
                            "protocol": "openid-connect",
                            "protocolMapper": "oidc-audience-mapper",
                            "config": {
                                "included.custom.audience": config.audience,
                                "access.token.claim": "true",
                                "id.token.claim": "false",
                            },
                        }
                    ],
                }
            ],
        }
        startup_script = (
            "set -euo pipefail; "
            "mkdir -p /opt/keycloak/data/import; "
            f'printf "%s" "${{REALM_JSON//{CLIENT_SECRET_PLACEHOLDER}/$KC_CLIENT_SECRET}}" '
            "> /opt/keycloak/data/import/realm.json; "
            "unset REALM_JSON KC_CLIENT_SECRET; "
            "exec /opt/keycloak/bin/kc.sh start-dev --import-realm"
        )

        # --- ECS Fargate ---
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc, container_insights_v2=ecs.ContainerInsights.ENABLED)
        task_def = ecs.FargateTaskDefinition(self, "TaskDef", cpu=1024, memory_limit_mib=2048)
        log_group = logs.LogGroup(
            self, "LogGroup", retention=logs.RetentionDays.ONE_WEEK, removal_policy=RemovalPolicy.DESTROY
        )
        task_def.add_container(
            "keycloak",
            image=ecs.ContainerImage.from_registry(config.keycloak_image),
            entry_point=["/bin/bash", "-c"],
            command=[startup_script],
            environment={
                "KC_BOOTSTRAP_ADMIN_USERNAME": "admin",
                "KC_HOSTNAME": f"https://{domain}",
                "KC_HTTP_ENABLED": "true",
                "KC_PROXY_HEADERS": "xforwarded",
                "KC_HEALTH_ENABLED": "true",
                "REALM_JSON": json.dumps(realm),
            },
            secrets={
                "KC_BOOTSTRAP_ADMIN_PASSWORD": ecs.Secret.from_secrets_manager(admin_secret, "password"),
                "KC_CLIENT_SECRET": ecs.Secret.from_secrets_manager(client_secret, "clientSecret"),
            },
            port_mappings=[ecs.PortMapping(container_port=KEYCLOAK_HTTP_PORT)],
            logging=ecs.LogDrivers.aws_logs(stream_prefix="keycloak", log_group=log_group),
        )
        service_sg = ec2.SecurityGroup(
            self,
            "KeycloakServiceSg",
            vpc=vpc,
            description="Keycloak tasks, inbound from the ALB",
            allow_all_outbound=True,
        )
        service = ecs.FargateService(
            self,
            "Service",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            security_groups=[service_sg],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            health_check_grace_period=Duration.seconds(180),
        )

        # --- Security groups ---
        # Attach this security group to the AgentCore private endpoint (VPC Lattice resource gateway ENIs).
        self.endpoint_sg = ec2.SecurityGroup(
            self,
            "LatticeEndpointSg",
            vpc=vpc,
            description="AgentCore Identity VPC Lattice endpoint ENIs, egress to the Keycloak ALB",
            allow_all_outbound=False,
        )
        alb_sg = ec2.SecurityGroup(
            self, "AlbSg", vpc=vpc, description="Internal Keycloak ALB", allow_all_outbound=False
        )
        alb_sg.add_ingress_rule(self.endpoint_sg, ec2.Port.tcp(443), "HTTPS from AgentCore Lattice endpoint")
        self.endpoint_sg.add_egress_rule(alb_sg, ec2.Port.tcp(443), "HTTPS to Keycloak ALB")

        # --- Internal ALB with the private certificate ---
        self.alb = elbv2.ApplicationLoadBalancer(
            self,
            "InternalAlb",
            vpc=vpc,
            internet_facing=False,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            drop_invalid_header_fields=True,
        )
        listener = self.alb.add_listener(
            "HttpsListener",
            port=443,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            certificates=[certificate],
            ssl_policy=elbv2.SslPolicy.RECOMMENDED_TLS,
            open=False,
        )
        listener.add_targets(
            "KeycloakTarget",
            port=KEYCLOAK_HTTP_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[service.load_balancer_target(container_name="keycloak", container_port=KEYCLOAK_HTTP_PORT)],
            health_check=elbv2.HealthCheck(
                path=f"/realms/{config.realm_name}/.well-known/openid-configuration",
                healthy_http_codes="200",
                healthy_threshold_count=2,
                interval=Duration.seconds(15),
            ),
            deregistration_delay=Duration.seconds(10),
        )

        # --- Private DNS ---
        zone = route53.PrivateHostedZone(self, "PrivateZone", zone_name=zone_name, vpc=vpc)
        route53.ARecord(
            self,
            "KeycloakRecord",
            zone=zone,
            record_name=record_name,
            target=route53.RecordTarget.from_alias(targets.LoadBalancerTarget(self.alb)),
        )

        # --- Outputs ---
        CfnOutput(self, "KeycloakDomain", value=domain)
        CfnOutput(self, "Issuer", value=issuer)
        CfnOutput(self, "DiscoveryUrl", value=f"{issuer}/.well-known/openid-configuration")
        CfnOutput(self, "ClientId", value=config.client_id)
        CfnOutput(self, "Audience", value=config.audience)
        CfnOutput(self, "PrivateCaArn", value=ca.attr_arn)
        CfnOutput(
            self,
            "CaCertificateSecretArn",
            value=ca_cert_secret.ref,
            description="Secrets Manager secret holding the PEM CA certificate (for certificateConfigurations)",
        )
        CfnOutput(self, "ClientSecretArn", value=client_secret.secret_arn)
        CfnOutput(self, "AdminSecretArn", value=admin_secret.secret_arn)
        CfnOutput(
            self,
            "EndpointSecurityGroupId",
            value=self.endpoint_sg.security_group_id,
            description="Security group for the AgentCore managed VPC Lattice endpoint",
        )
        CfnOutput(self, "AlbDnsName", value=self.alb.load_balancer_dns_name)
