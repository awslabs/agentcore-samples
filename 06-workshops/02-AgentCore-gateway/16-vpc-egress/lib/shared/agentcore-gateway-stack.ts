import * as cdk from "aws-cdk-lib/core";
import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha";
import * as iam from "aws-cdk-lib/aws-iam";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";

export interface AgentCoreGatewayStackProps extends cdk.StackProps {
  gatewayName?: string;
}

export class AgentCoreGatewayStack extends cdk.Stack {
  public readonly gateway: agentcore.Gateway;

  constructor(
    scope: Construct,
    id: string,
    props?: AgentCoreGatewayStackProps,
  ) {
    super(scope, id, props);

    const accountId = cdk.Stack.of(this).account;
    const gatewayName = props?.gatewayName ?? `ac-gw-vpc-egress-${accountId}`;

    this.gateway = new agentcore.Gateway(this, "VpcEgressGateway", {
      gatewayName,
      description: "AgentCore Gateway with MCP Server target",
      protocolConfiguration: new agentcore.McpProtocolConfiguration({
        searchType: agentcore.McpGatewaySearchType.SEMANTIC,
        supportedVersions: [agentcore.MCPProtocolVersion.MCP_2025_03_26],
      }),
      exceptionLevel: agentcore.GatewayExceptionLevel.DEBUG,
    });

    const region = cdk.Stack.of(this).region;

    // AgentCore permissions (workload identity, token vault, gateway, secrets)
    this.gateway.role!.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:GetWorkloadAccessToken",
          "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
          "bedrock-agentcore:GetResourceOauth2Token",
          "bedrock-agentcore:GetResourceApiKey",
          "bedrock-agentcore:GetGateway",
        ],
        resources: ["*"],
      }),
    );

    // Secrets Manager access (for credential providers)
    this.gateway.role!.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["secretsmanager:GetSecretValue"],
        resources: ["*"],
      }),
    );

    // EC2 permissions for managed VPC resource (Resource Gateway ENI provisioning)
    this.gateway.role!.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: [
          "ec2:DescribeNetworkInterfaces",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSubnets",
          "ec2:DescribeVpcs",
          "ec2:CreateNetworkInterface",
        ],
        resources: ["*"],
      }),
    );

    NagSuppressions.addStackSuppressions(this, [
      {
        id: "AwsSolutions-COG1",
        reason:
          "Cognito user pool is for M2M client credentials only, no human passwords",
      },
      {
        id: "AwsSolutions-COG2",
        reason: "MFA not applicable for M2M client credentials flow",
      },
      {
        id: "AwsSolutions-COG3",
        reason: "Advanced security not needed for M2M client credentials flow",
      },
      {
        id: "AwsSolutions-IAM5",
        reason: "Gateway execution role wildcards managed by construct",
      },
    ]);

    new cdk.CfnOutput(this, "GatewayId", {
      value: this.gateway.gatewayId,
    });

    new cdk.CfnOutput(this, "GatewayRoleArn", {
      value: this.gateway.role!.roleArn,
    });

    if (this.gateway.gatewayUrl) {
      new cdk.CfnOutput(this, "GatewayUrl", {
        value: this.gateway.gatewayUrl,
      });
    }

    if (this.gateway.userPool) {
      new cdk.CfnOutput(this, "UserPoolId", {
        value: this.gateway.userPool.userPoolId,
      });
    }

    if (this.gateway.userPoolClient) {
      new cdk.CfnOutput(this, "UserPoolClientId", {
        value: this.gateway.userPoolClient.userPoolClientId,
      });
    }

    if (this.gateway.tokenEndpointUrl) {
      new cdk.CfnOutput(this, "TokenEndpointUrl", {
        value: this.gateway.tokenEndpointUrl,
      });
    }

    if (this.gateway.oauthScopes) {
      new cdk.CfnOutput(this, "OAuthScopes", {
        value: this.gateway.oauthScopes.join(","),
      });
    }
  }
}
