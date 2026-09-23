"""API Gateway construct"""

from constructs import Construct
from aws_cdk import aws_apigateway as apigateway, aws_lambda as lambda_

from .helpers import add_cors_options


class ApiConstruct(Construct):
    """Construct for API Gateway and all endpoints."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        async_deploy_lambda: lambda_.Function,
        token_usage_lambda: lambda_.Function,
        invoke_agent_lambda: lambda_.Function,
        get_agent_lambda: lambda_.Function,
        list_agents_lambda: lambda_.Function,
        delete_agent_lambda: lambda_.Function,
        update_config_lambda: lambda_.Function,
        set_tenant_limit_lambda: lambda_.Function,
        infrastructure_costs_lambda: lambda_.Function,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create API Gateway
        self.api = apigateway.RestApi(
            self,
            "AgentDeploymentAPI",
            rest_api_name="Agent Deployment API",
            description="API to deploy Bedrock agents with tenant isolation",
            deploy_options=apigateway.StageOptions(
                stage_name="prod",
                throttling_rate_limit=100,
                throttling_burst_limit=200,
            ),
        )

        # Create API Key
        self.api_key = self.api.add_api_key(
            "AgentDeploymentApiKey",
            api_key_name="agent-deployment-key",  # pragma: allowlist secret
        )

        # Create Usage Plan
        usage_plan = self.api.add_usage_plan(
            "AgentDeploymentUsagePlan",
            name="Agent Deployment Usage Plan",
            throttle=apigateway.ThrottleSettings(rate_limit=100, burst_limit=200),
            quota=apigateway.QuotaSettings(limit=10000, period=apigateway.Period.DAY),
        )
        usage_plan.add_api_key(self.api_key)
        usage_plan.add_api_stage(stage=self.api.deployment_stage)

        # Setup all endpoints
        self._setup_deploy_endpoint(async_deploy_lambda)
        self._setup_usage_endpoint(token_usage_lambda)
        self._setup_invoke_endpoint(invoke_agent_lambda)
        self._setup_agent_endpoint(get_agent_lambda, delete_agent_lambda)
        self._setup_agents_endpoint(list_agents_lambda)
        self._setup_config_endpoint(update_config_lambda)
        self._setup_tenant_limit_endpoint(set_tenant_limit_lambda)
        self._setup_infrastructure_costs_endpoint(infrastructure_costs_lambda)

    def _setup_deploy_endpoint(self, lambda_fn: lambda_.Function) -> None:
        """Setup /deploy endpoint."""
        resource = self.api.root.add_resource("deploy")

        resource.add_method(
            "POST",
            apigateway.LambdaIntegration(lambda_fn, proxy=True),
            api_key_required=True,
            request_parameters={"method.request.querystring.tenantId": True},
            method_responses=[
                apigateway.MethodResponse(
                    status_code="200",
                    response_parameters={"method.response.header.Access-Control-Allow-Origin": True},
                    response_models={"application/json": apigateway.Model.EMPTY_MODEL},
                )
            ],
        )
        add_cors_options(resource, ["POST", "OPTIONS"])

    def _setup_usage_endpoint(self, lambda_fn: lambda_.Function) -> None:
        """Setup /usage endpoint."""
        resource = self.api.root.add_resource("usage")
        resource.add_method(
            "GET",
            apigateway.LambdaIntegration(lambda_fn, proxy=True),
            api_key_required=True,
        )
        add_cors_options(resource, ["GET", "OPTIONS"])

    def _setup_invoke_endpoint(self, lambda_fn: lambda_.Function) -> None:
        """Setup /invoke endpoint."""
        resource = self.api.root.add_resource("invoke")
        resource.add_method(
            "POST",
            apigateway.LambdaIntegration(lambda_fn, proxy=True),
            api_key_required=True,
        )
        add_cors_options(resource, ["POST", "OPTIONS"])

    def _setup_agent_endpoint(self, get_lambda: lambda_.Function, delete_lambda: lambda_.Function) -> None:
        """Setup /agent endpoint (GET and DELETE)."""
        resource = self.api.root.add_resource("agent")
        resource.add_method(
            "GET",
            apigateway.LambdaIntegration(get_lambda, proxy=True),
            api_key_required=True,
        )
        resource.add_method(
            "DELETE",
            apigateway.LambdaIntegration(delete_lambda, proxy=True),
            api_key_required=True,
        )
        add_cors_options(resource, ["GET", "DELETE", "OPTIONS"])

    def _setup_agents_endpoint(self, lambda_fn: lambda_.Function) -> None:
        """Setup /agents endpoint."""
        resource = self.api.root.add_resource("agents")
        resource.add_method(
            "GET",
            apigateway.LambdaIntegration(lambda_fn, proxy=True),
            api_key_required=True,
        )
        add_cors_options(resource, ["GET", "OPTIONS"])

    def _setup_config_endpoint(self, lambda_fn: lambda_.Function) -> None:
        """Setup /config endpoint."""
        resource = self.api.root.add_resource("config")
        integration = apigateway.LambdaIntegration(lambda_fn, proxy=True)
        resource.add_method("GET", integration, api_key_required=True)
        resource.add_method("PUT", integration, api_key_required=True)
        add_cors_options(resource, ["GET", "PUT", "OPTIONS"])

    def _setup_tenant_limit_endpoint(self, lambda_fn: lambda_.Function) -> None:
        """Setup /tenant-limit endpoint."""
        resource = self.api.root.add_resource("tenant-limit")
        resource.add_method(
            "POST",
            apigateway.LambdaIntegration(lambda_fn, proxy=True),
            api_key_required=True,
        )
        add_cors_options(resource, ["POST", "OPTIONS"])

    def _setup_infrastructure_costs_endpoint(self, lambda_fn: lambda_.Function) -> None:
        """Setup /infrastructure-costs endpoint."""
        resource = self.api.root.add_resource("infrastructure-costs")
        resource.add_method(
            "GET",
            apigateway.LambdaIntegration(lambda_fn, proxy=True),
            api_key_required=True,
        )
        add_cors_options(resource, ["GET", "OPTIONS"])
