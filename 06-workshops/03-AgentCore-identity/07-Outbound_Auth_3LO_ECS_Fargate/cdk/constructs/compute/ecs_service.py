# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""ECS service construct for agent and session binding services."""

from aws_cdk import Aws, Duration, RemovalPolicy
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from config import OidcConfig


class EcsService(Construct):
    """ECS Cluster with Agent and Session Binding services."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        vpc: ec2.IVpc,
        listener: elbv2.ApplicationListener,
        alb_security_group: ec2.ISecurityGroup,
        oidc_config: OidcConfig,
        environment_vars: dict,
        suffix: str,
        inference_profile_id: str,
        model_id: str,
        kms_key: kms.IKey,
        identity_aws_region: str,
        workload_identity_name: str,
        github_provider_name: str,
    ):
        """Initialize ECS service construct."""
        super().__init__(scope, id)

        account_id = Aws.ACCOUNT_ID
        region = Aws.REGION

        self.security_group = ec2.SecurityGroup(
            self,
            "EcsSg",
            vpc=vpc,
            description="ECS service security group",
            allow_all_outbound=False,
        )
        self.security_group.add_ingress_rule(
            alb_security_group,
            ec2.Port.tcp(8080),
            "Allow from ALB to services",
        )

        self.security_group.add_egress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(443),
            description="Allow HTTPS for AWS services and external APIs",
        )

        self.security_group.add_egress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.udp(53),
            description="Allow DNS resolution",
        )

        _ = self.security_group.node.default_child

        self.cluster = ecs.Cluster(
            self,
            "Cluster",
            vpc=vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        self.agent_task_role = iam.Role(
            self,
            "AgentTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        self.agent_task_role.attach_inline_policy(
            iam.Policy(
                self,
                "AgentBedrockPolicy",
                statements=[
                    iam.PolicyStatement(
                        sid="AllowBedrockInvokeViaInferenceProfile",
                        effect=iam.Effect.ALLOW,
                        actions=[
                            "bedrock:InvokeModel",
                            "bedrock:InvokeModelWithResponseStream",
                        ],
                        resources=[
                            f"arn:aws:bedrock:{region}:{account_id}:inference-profile/{inference_profile_id}",
                        ],
                    ),
                    iam.PolicyStatement(
                        sid="AllowFoundationModelViaInferenceProfile",
                        effect=iam.Effect.ALLOW,
                        actions=[
                            "bedrock:InvokeModel",
                            "bedrock:InvokeModelWithResponseStream",
                        ],
                        resources=[
                            f"arn:aws:bedrock:*::foundation-model/{model_id}",
                        ],
                        conditions={
                            "StringLike": {
                                "bedrock:InferenceProfileArn": f"arn:aws:bedrock:*:{account_id}:inference-profile/*"
                            }
                        },
                    ),
                ],
            )
        )

        self.agent_task_role.attach_inline_policy(
            iam.Policy(
                self,
                "AgentCoreWorkloadPolicy",
                statements=[
                    iam.PolicyStatement(
                        sid="AllowAgentCoreWorkloadAccess",
                        effect=iam.Effect.ALLOW,
                        actions=[
                            "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                            "bedrock-agentcore:GetResourceOAuth2Token",
                        ],
                        resources=[
                            f"arn:aws:bedrock-agentcore:{identity_aws_region}:{account_id}"
                            ":workload-identity-directory/default",
                            f"arn:aws:bedrock-agentcore:{identity_aws_region}:{account_id}"
                            f":workload-identity-directory/default/workload-identity/{workload_identity_name}",
                            f"arn:aws:bedrock-agentcore:{identity_aws_region}:{account_id}"
                            f":token-vault/default/oauth2credentialprovider/{github_provider_name}",
                            f"arn:aws:bedrock-agentcore:{identity_aws_region}:{account_id}:token-vault/default",
                        ],
                    ),
                ],
            )
        )

        self.agent_task_role.add_to_policy(
            iam.PolicyStatement(
                sid="AllowSecretsManagerAgentCoreOAuth",
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:{identity_aws_region}:{account_id}"
                    f":secret:bedrock-agentcore-identity!default/oauth2/{github_provider_name}*"
                ],
            )
        )

        self.session_binding_task_role = iam.Role(
            self,
            "SessionBindingTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        self.session_binding_task_role.attach_inline_policy(
            iam.Policy(
                self,
                "SessionBindingAgentCorePolicy",
                statements=[
                    iam.PolicyStatement(
                        sid="AllowCompleteResourceTokenAuth",
                        effect=iam.Effect.ALLOW,
                        actions=["bedrock-agentcore:CompleteResourceTokenAuth"],
                        resources=[
                            f"arn:aws:bedrock-agentcore:{identity_aws_region}:{account_id}"
                            ":workload-identity-directory/default",
                            f"arn:aws:bedrock-agentcore:{identity_aws_region}:{account_id}"
                            f":workload-identity-directory/default/workload-identity/{workload_identity_name}",
                            f"arn:aws:bedrock-agentcore:{identity_aws_region}:{account_id}:token-vault/default",
                            f"arn:aws:bedrock-agentcore:{identity_aws_region}:{account_id}"
                            f":token-vault/default/oauth2credentialprovider/{github_provider_name}",
                        ],
                    ),
                ],
            )
        )

        self.session_binding_task_role.add_to_policy(
            iam.PolicyStatement(
                sid="AllowSecretsManagerAccess",
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:{identity_aws_region}:{account_id}"
                    f":secret:bedrock-agentcore-identity!default/oauth2/{github_provider_name}*"
                ],
            )
        )

        agent_log_group = logs.LogGroup(
            self,
            "AgentLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
            encryption_key=kms_key,
        )

        self.agent_task_definition = ecs.FargateTaskDefinition(
            self,
            "AgentTaskDef",
            task_role=self.agent_task_role,
            cpu=512,
            memory_limit_mib=1024,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        self.agent_container = self.agent_task_definition.add_container(
            "Agent",
            image=ecs.ContainerImage.from_asset(
                "backend",
                asset_name=f"agent-{suffix}",
                platform=ecr_assets.Platform.LINUX_ARM64,
                file="runtime/Dockerfile",
            ),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="agent", log_group=agent_log_group),
            environment=environment_vars,
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8080/ping || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )
        self.agent_container.add_port_mappings(ecs.PortMapping(container_port=8080))

        session_binding_log_group = logs.LogGroup(
            self,
            "SessionBindingLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
            encryption_key=kms_key,
        )

        self.session_binding_task_definition = ecs.FargateTaskDefinition(
            self,
            "SessionBindingTaskDef",
            task_role=self.session_binding_task_role,
            cpu=256,
            memory_limit_mib=512,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        self.session_binding_container = self.session_binding_task_definition.add_container(
            "SessionBinding",
            image=ecs.ContainerImage.from_asset(
                "backend",
                asset_name=f"session-binding-{suffix}",
                platform=ecr_assets.Platform.LINUX_ARM64,
                file="session_binding/Dockerfile",
            ),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="session-binding", log_group=session_binding_log_group),
            environment=environment_vars,
            health_check=ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    "curl -f http://localhost:8080/ping || exit 1",
                ],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )
        self.session_binding_container.add_port_mappings(ecs.PortMapping(container_port=8080))

        self.agent_service = ecs.FargateService(
            self,
            "AgentService",
            cluster=self.cluster,
            task_definition=self.agent_task_definition,
            desired_count=1,
            security_groups=[self.security_group],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
        )

        self.session_binding_service = ecs.FargateService(
            self,
            "SessionBindingService",
            cluster=self.cluster,
            task_definition=self.session_binding_task_definition,
            desired_count=1,
            security_groups=[self.security_group],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
        )

        agent_target_group = elbv2.ApplicationTargetGroup(
            self,
            "AgentTargetGroup",
            port=8080,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[self.agent_service],
            health_check=elbv2.HealthCheck(path="/ping"),
            vpc=vpc,
        )
        # Session Binding Target Group
        session_binding_target_group = elbv2.ApplicationTargetGroup(
            self,
            "SessionBindingTargetGroup",
            port=8080,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[self.session_binding_service],
            health_check=elbv2.HealthCheck(path="/ping"),
            vpc=vpc,
        )

        oidc_credentials = secretsmanager.Secret.from_secret_name_v2(self, "OIDCCredentials", oidc_config.secret_name)

        listener.add_action(
            "EntraIdAction",
            priority=11,
            conditions=[elbv2.ListenerCondition.path_patterns(["/invocations", "/docs", "/openapi.json"])],
            action=elbv2.ListenerAction.authenticate_oidc(
                issuer=oidc_config.issuer,
                authorization_endpoint=oidc_config.authorization_endpoint,
                token_endpoint=oidc_config.token_endpoint,
                user_info_endpoint=oidc_config.user_info_endpoint,
                client_id=oidc_credentials.secret_value_from_json("client_id").to_string(),
                client_secret=oidc_credentials.secret_value_from_json("client_secret"),
                scope=oidc_config.scope,
                session_timeout=Duration.minutes(5),
                next=elbv2.ListenerAction.forward([agent_target_group]),
            ),
        )

        listener.add_action(
            "SessionBindingAction",
            priority=22,
            conditions=[elbv2.ListenerCondition.path_patterns(["/oauth2/session-binding"])],
            action=elbv2.ListenerAction.authenticate_oidc(
                issuer=oidc_config.issuer,
                authorization_endpoint=oidc_config.authorization_endpoint,
                token_endpoint=oidc_config.token_endpoint,
                user_info_endpoint=oidc_config.user_info_endpoint,
                client_id=oidc_credentials.secret_value_from_json("client_id").to_string(),
                client_secret=oidc_credentials.secret_value_from_json("client_secret"),
                scope=oidc_config.scope,
                session_timeout=Duration.minutes(5),
                next=elbv2.ListenerAction.forward([session_binding_target_group]),
            ),
        )

        listener.add_action(
            "DefaultAction",
            action=elbv2.ListenerAction.fixed_response(
                status_code=404,
                content_type="text/plain",
                message_body="Not Found",
            ),
        )
