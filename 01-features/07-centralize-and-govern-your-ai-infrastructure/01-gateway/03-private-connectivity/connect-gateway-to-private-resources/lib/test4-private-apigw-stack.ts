import * as cdk from "aws-cdk-lib/core";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import * as logs from "aws-cdk-lib/aws-logs";
import * as iam from "aws-cdk-lib/aws-iam";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";

export interface PrivateApigwStackProps extends cdk.StackProps {
  vpc: ec2.IVpc;
  /** Optional: CIDR of a peered VPC to allow inbound HTTPS on the VPCE security group */
  peerVpcCidr?: string;
  /** Whether to enable private DNS on the VPCE (default: true). Set to false for peering. */
  privateDnsEnabled?: boolean;
}

export class PrivateApigwStack extends cdk.Stack {
  public readonly api: apigw.RestApi;
  public readonly vpcEndpoint: ec2.InterfaceVpcEndpoint;

  constructor(scope: Construct, id: string, props: PrivateApigwStackProps) {
    super(scope, id, props);

    const vpceSecurityGroup = new ec2.SecurityGroup(this, "VpceSecurityGroup", {
      vpc: props.vpc,
      description: "Security group for execute-api VPC endpoint",
      allowAllOutbound: true,
    });
    vpceSecurityGroup.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);
    vpceSecurityGroup.addIngressRule(
      ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
      ec2.Port.tcp(443),
      "Allow HTTPS from VPC",
    );

    if (props.peerVpcCidr) {
      vpceSecurityGroup.addIngressRule(
        ec2.Peer.ipv4(props.peerVpcCidr),
        ec2.Port.tcp(443),
        "Allow HTTPS from peered VPC",
      );
    }

    this.vpcEndpoint = new ec2.InterfaceVpcEndpoint(
      this,
      "ExecuteApiVpcEndpoint",
      {
        vpc: props.vpc,
        service: ec2.InterfaceVpcEndpointAwsService.APIGATEWAY,
        subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
        securityGroups: [vpceSecurityGroup],
        privateDnsEnabled: props.privateDnsEnabled ?? true,
      },
    );

    const accessLogGroup = new logs.LogGroup(this, "ApiAccessLogGroup", {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.api = new apigw.RestApi(this, "PrivateApi", {
      restApiName: "PrivateApi",
      endpointConfiguration: {
        types: [apigw.EndpointType.PRIVATE],
        vpcEndpoints: [this.vpcEndpoint],
      },
      policy: new iam.PolicyDocument({
        statements: [
          new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            principals: [new iam.AnyPrincipal()],
            actions: ["execute-api:Invoke"],
            resources: ["execute-api:/*"],
            conditions: {
              StringEquals: {
                "aws:sourceVpce": this.vpcEndpoint.vpcEndpointId,
              },
            },
          }),
          new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            principals: [new iam.AnyPrincipal()],
            actions: ["execute-api:Invoke"],
            resources: ["execute-api:/*"],
            conditions: {
              StringEquals: {
                "aws:sourceVpc": props.vpc.vpcId,
              },
            },
          }),
        ],
      }),
      deployOptions: {
        stageName: "prod",
        accessLogDestination: new apigw.LogGroupLogDestination(accessLogGroup),
        accessLogFormat: apigw.AccessLogFormat.jsonWithStandardFields(),
        loggingLevel: apigw.MethodLoggingLevel.INFO,
      },
      apiKeySourceType: apigw.ApiKeySourceType.HEADER,
    });

    // --- API Key + Usage Plan ---
    const apiKey = this.api.addApiKey("ApiKey", {
      apiKeyName: `private-api-key-${this.stackName}`,
    });

    const usagePlan = this.api.addUsagePlan("UsagePlan", {
      name: `private-api-usage-plan-${this.stackName}`,
      throttle: { rateLimit: 10, burstLimit: 20 },
    });
    usagePlan.addApiKey(apiKey);
    usagePlan.addApiStage({ stage: this.api.deploymentStage });

    // GET /health — returns {"status": "ok"}
    const healthResource = this.api.root.addResource("health");
    healthResource.addMethod(
      "GET",
      new apigw.MockIntegration({
        integrationResponses: [
          {
            statusCode: "200",
            responseTemplates: {
              "application/json": '{"status": "ok"}',
            },
          },
        ],
        requestTemplates: {
          "application/json": '{"statusCode": 200}',
        },
      }),
      {
        methodResponses: [{ statusCode: "200" }],
        apiKeyRequired: true,
      },
    );

    // GET /items — returns a static list of items
    const itemsResource = this.api.root.addResource("items");
    itemsResource.addMethod(
      "GET",
      new apigw.MockIntegration({
        integrationResponses: [
          {
            statusCode: "200",
            responseTemplates: {
              "application/json": JSON.stringify([
                { name: "Widget", price: 9.99 },
                { name: "Gadget", price: 19.99 },
              ]),
            },
          },
        ],
        requestTemplates: {
          "application/json": '{"statusCode": 200}',
        },
      }),
      {
        methodResponses: [{ statusCode: "200" }],
        apiKeyRequired: true,
      },
    );

    // POST /items — echoes back the request body
    itemsResource.addMethod(
      "POST",
      new apigw.MockIntegration({
        integrationResponses: [
          {
            statusCode: "200",
            responseTemplates: {
              "application/json": '{"message": "Item created"}',
            },
          },
        ],
        requestTemplates: {
          "application/json": '{"statusCode": 200}',
        },
      }),
      {
        methodResponses: [{ statusCode: "200" }],
        apiKeyRequired: true,
      },
    );

    // --- Outputs ---
    new cdk.CfnOutput(this, "ApiId", {
      value: this.api.restApiId,
    });

    new cdk.CfnOutput(this, "ApiUrl", {
      value: this.api.url,
      description: "Private API Gateway URL (only reachable via VPCE)",
    });

    new cdk.CfnOutput(this, "ApiKeyId", {
      value: apiKey.keyId,
    });

    new cdk.CfnOutput(this, "VpceId", {
      value: this.vpcEndpoint.vpcEndpointId,
    });

    new cdk.CfnOutput(this, "VpceSgId", {
      value: vpceSecurityGroup.securityGroupId,
    });

    NagSuppressions.addStackSuppressions(this, [
      {
        id: "AwsSolutions-APIG3",
        reason: "WAF cannot be attached to private API Gateway",
      },
      {
        id: "AwsSolutions-APIG2",
        reason: "Request validation not needed for mock integrations",
      },
      {
        id: "AwsSolutions-APIG4",
        reason:
          "Using API key auth — access controlled by VPCE resource policy + API key",
      },
      {
        id: "AwsSolutions-COG4",
        reason: "Using API key auth instead of Cognito",
      },
      {
        id: "CdkNagValidationFailure",
        reason: "Security group uses VPC CIDR intrinsic reference",
      },
    ]);
  }
}
