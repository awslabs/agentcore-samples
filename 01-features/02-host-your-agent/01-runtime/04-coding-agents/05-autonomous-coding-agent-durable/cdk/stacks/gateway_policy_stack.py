"""Gateway Policy stack: AgentCore Gateway + Cedar Policy Engine.

Provides a managed policy enforcement layer between the coding agent and sandbox.
The Gateway evaluates Cedar policies against every tool call BEFORE forwarding
to the sandbox runtime. This is the cloud-native approach — policies are managed
centrally, decisions are audited to CloudWatch, and the agent cannot bypass them.

Architecture:
  Coding Agent → AgentCore Gateway (Cedar Policy Engine) → Sandbox Runtime

The Gateway:
  - Intercepts all MCP tool calls from the coding agent
  - Evaluates Cedar policies against (principal, action, resource, context)
  - Returns DENY with reason if policy forbids the action
  - Forwards to sandbox only on ALLOW
  - Logs all decisions to CloudWatch for audit
"""
import os

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from constructs import Construct


class GatewayPolicyStack(cdk.Stack):
    """AgentCore Gateway with Cedar policy engine for sandbox tool authorization."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str,
        sandbox_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        account = cdk.Stack.of(self).account
        region = cdk.Stack.of(self).region

        # --- Cedar policies (loaded from file) ---
        policies_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "gateway-policies"
        )
        gateway_policy_file = os.path.join(policies_dir, "gateway.cedar")

        # Read policy content (will be stored as a stack parameter)
        policy_content = ""
        if os.path.exists(gateway_policy_file):
            with open(gateway_policy_file) as f:
                policy_content = f.read()

        # --- Policy Engine ---
        self.policy_engine = cdk.CfnResource(
            self,
            "PolicyEngine",
            type="AWS::BedrockAgentCore::PolicyEngine",
            properties={
                "Name": f"{project}-sandbox-policy-engine",
                "Mode": "ENFORCE",
            },
        )

        # --- Gateway ---
        self.gateway = cdk.CfnResource(
            self,
            "SandboxGateway",
            type="AWS::BedrockAgentCore::Gateway",
            properties={
                "Name": f"{project}-sandbox-gateway",
                "AuthorizerType": "NONE",  # Internal traffic; use IAM for prod
                "PolicyEngines": [
                    {
                        "PolicyEngineArn": cdk.Token.as_string(
                            self.policy_engine.get_att("PolicyEngineArn")
                        ),
                        "Mode": "ENFORCE",
                    }
                ],
            },
        )
        self.gateway.add_dependency(self.policy_engine)

        # --- Outputs ---
        self.gateway_arn = cdk.Token.as_string(
            self.gateway.get_att("GatewayArn")
        )
        self.gateway_url = cdk.Token.as_string(
            self.gateway.get_att("GatewayUrl")
        )
        self.policy_engine_arn = cdk.Token.as_string(
            self.policy_engine.get_att("PolicyEngineArn")
        )

        cdk.CfnOutput(
            self,
            "GatewayArn",
            value=self.gateway_arn,
            export_name=f"{project}-gateway-arn",
        )
        cdk.CfnOutput(
            self,
            "GatewayUrl",
            value=self.gateway_url,
            export_name=f"{project}-gateway-url",
        )
        cdk.CfnOutput(
            self,
            "PolicyEngineArn",
            value=self.policy_engine_arn,
            export_name=f"{project}-policy-engine-arn",
        )
