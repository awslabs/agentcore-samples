from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput,
    CustomResource,
    aws_cognito as cognito,
    aws_secretsmanager as secretsmanager,
    aws_ssm as ssm,
    aws_lambda as lambda_,
    aws_iam as iam,
    custom_resources as cr,
)
from constructs import Construct
import os
import json


class CognitoStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        environment: str = "dev",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Cognito User Pool for M2M Authentication
        self.user_pool = cognito.UserPool(
            self,
            "M2MUserPool",
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            mfa=cognito.Mfa.OFF,
            self_sign_up_enabled=False,
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # User Pool Domain for OAuth endpoints
        domain_prefix = f"toon-m2m-{self.account}"
        self.user_pool_domain = self.user_pool.add_domain(
            "M2MUserPoolDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
        )

        # Resource Server for M2M scopes
        resource_server_identifier = f"toon-{environment}-resource-server"
        self.resource_server = self.user_pool.add_resource_server(
            "M2MResourceServer",
            identifier=resource_server_identifier,
            scopes=[
                cognito.ResourceServerScope(
                    scope_name="read",
                    scope_description="Read access to resources",
                ),
                cognito.ResourceServerScope(
                    scope_name="write",
                    scope_description="Write access to resources",
                ),
                cognito.ResourceServerScope(
                    scope_name="gateway",
                    scope_description="Gateway-specific access",
                ),
            ],
        )

        # OAuth scopes for the gateway client
        gateway_scopes = [
            cognito.OAuthScope.custom(f"{resource_server_identifier}/read"),
            cognito.OAuthScope.custom(f"{resource_server_identifier}/gateway"),
        ]

        # Gateway Client for M2M Authentication (client credentials flow)
        self.gateway_client = self.user_pool.add_client(
            "GatewayClient",
            user_pool_client_name="gateway-m2m-client",
            generate_secret=True,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(client_credentials=True),
                scopes=gateway_scopes,
            ),
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(1),
            prevent_user_existence_errors=True,
        )

        # Ensure resource server is created before the client
        self.gateway_client.node.add_dependency(self.resource_server)

        # Token endpoint URL
        token_endpoint = (
            f"https://{domain_prefix}.auth.{self.region}.amazoncognito.com/oauth2/token"
        )
        discovery_url = f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool.user_pool_id}/.well-known/openid-configuration"

        # Secrets Manager Secret for Gateway Client configuration
        self.gateway_client_secret = secretsmanager.Secret(
            self,
            "GatewayClientSecret",
            secret_name=f"toon-{environment}/gateway/client-config",
            description="Gateway Client configuration for M2M authentication",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps(
                    {
                        "client_id": self.gateway_client.user_pool_client_id,
                        "user_pool_id": self.user_pool.user_pool_id,
                        "token_endpoint": token_endpoint,
                        "cognito_discovery_url": discovery_url,
                    }
                ),
                generate_string_key="client_secret",
            ),
        )

        # Lambda to update secret with actual client secret
        secret_updater_lambda = lambda_.Function(
            self,
            "CognitoSecretUpdaterFunction",
            runtime=lambda_.Runtime.PYTHON_3_14,
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset(
                os.path.join(
                    os.path.dirname(__file__), "..", "lambda", "cognito_secret_updater"
                )
            ),
            timeout=Duration.minutes(1),
            memory_size=128,
        )

        # Grant permissions
        secret_updater_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["cognito-idp:DescribeUserPoolClient"],
                resources=[self.user_pool.user_pool_arn],
            )
        )

        self.gateway_client_secret.grant_read(secret_updater_lambda)
        self.gateway_client_secret.grant_write(secret_updater_lambda)

        # Custom Resource Provider
        secret_updater_provider = cr.Provider(
            self,
            "SecretUpdaterProvider",
            on_event_handler=secret_updater_lambda,
        )

        # Custom Resource to update the secret
        CustomResource(
            self,
            "GatewayClientSecretUpdater",
            service_token=secret_updater_provider.service_token,
            properties={
                "UserPoolId": self.user_pool.user_pool_id,
                "ClientId": self.gateway_client.user_pool_client_id,
                "SecretArn": self.gateway_client_secret.secret_arn,
            },
        )

        # SSM Parameters for easy access
        ssm.StringParameter(
            self,
            "CognitoUserPoolIdParameter",
            parameter_name=f"/app/toon/{environment}/cognito/user_pool_id",
            string_value=self.user_pool.user_pool_id,
            description="Cognito User Pool ID",
        )

        ssm.StringParameter(
            self,
            "CognitoTokenURLParameter",
            parameter_name=f"/app/toon/{environment}/cognito/token_url",
            string_value=token_endpoint,
            description="OAuth2 Token URL",
        )

        ssm.StringParameter(
            self,
            "CognitoDiscoveryURLParameter",
            parameter_name=f"/app/toon/{environment}/cognito/discovery_url",
            string_value=discovery_url,
            description="OpenID Connect Discovery URL",
        )

        ssm.StringParameter(
            self,
            "CognitoGatewayClientIdParameter",
            parameter_name=f"/app/toon/{environment}/cognito/gateway_client_id",
            string_value=self.gateway_client.user_pool_client_id,
            description="Gateway M2M Client ID",
        )

        # Outputs
        CfnOutput(
            self,
            "UserPoolId",
            value=self.user_pool.user_pool_id,
            description="Cognito User Pool ID",
        )

        CfnOutput(
            self,
            "UserPoolArn",
            value=self.user_pool.user_pool_arn,
            description="Cognito User Pool ARN",
        )

        CfnOutput(
            self,
            "UserPoolDomain",
            value=f"{domain_prefix}.auth.{self.region}.amazoncognito.com",
            description="Cognito User Pool Domain",
        )

        CfnOutput(
            self,
            "GatewayClientId",
            value=self.gateway_client.user_pool_client_id,
            description="Gateway M2M Client ID",
        )

        CfnOutput(
            self,
            "TokenEndpoint",
            value=token_endpoint,
            description="OAuth 2.0 Token Endpoint",
        )

        CfnOutput(
            self,
            "DiscoveryUrl",
            value=discovery_url,
            description="OpenID Connect Discovery URL",
        )

        CfnOutput(
            self,
            "GatewayClientSecretArn",
            value=self.gateway_client_secret.secret_arn,
            description="Gateway Client Secret ARN in Secrets Manager",
        )

        CfnOutput(
            self,
            "GatewayScopes",
            value=f"{resource_server_identifier}/read {resource_server_identifier}/gateway",
            description="Available scopes for Gateway Client",
        )

        CfnOutput(
            self,
            "ClientCredentialsExample",
            value=f"curl -X POST {token_endpoint} -H 'Content-Type: application/x-www-form-urlencoded' -d 'grant_type=client_credentials&client_id=<CLIENT_ID>&client_secret=<CLIENT_SECRET>&scope={resource_server_identifier}/read {resource_server_identifier}/gateway'",
            description="Example curl command for getting access token",
        )
