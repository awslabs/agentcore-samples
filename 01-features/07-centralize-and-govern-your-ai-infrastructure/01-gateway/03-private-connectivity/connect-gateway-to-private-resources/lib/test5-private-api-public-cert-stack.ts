import * as cdk from "aws-cdk-lib/core";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import * as acm from "aws-cdk-lib/aws-certificatemanager";
import * as route53 from "aws-cdk-lib/aws-route53";
import * as route53targets from "aws-cdk-lib/aws-route53-targets";
import * as logs from "aws-cdk-lib/aws-logs";
import * as iam from "aws-cdk-lib/aws-iam";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";

export interface PrivateApiPublicCertStackProps extends cdk.StackProps {
  vpc: ec2.IVpc;
  baseDomain: string;
  publicCertArn: string;
}

export class PrivateApiPublicCertStack extends cdk.Stack {
  constructor(
    scope: Construct,
    id: string,
    props: PrivateApiPublicCertStackProps,
  ) {
    super(scope, id, props);

    const domainName = `test5.internal.${props.baseDomain}`;

    const vpceSecurityGroup = new ec2.SecurityGroup(this, "VpceSecurityGroup", {
      vpc: props.vpc,
      description: "Security group for execute-api VPC endpoint",
      allowAllOutbound: true,
    });
    vpceSecurityGroup.addIngressRule(
      ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
      ec2.Port.tcp(443),
      "Allow HTTPS from VPC",
    );

    const vpcEndpoint = new ec2.InterfaceVpcEndpoint(
      this,
      "ExecuteApiVpcEndpoint",
      {
        vpc: props.vpc,
        service: ec2.InterfaceVpcEndpointAwsService.APIGATEWAY,
        subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
        securityGroups: [vpceSecurityGroup],
        privateDnsEnabled: true,
      },
    );

    const certificate = acm.Certificate.fromCertificateArn(
      this,
      "PublicCert",
      props.publicCertArn,
    );

    const accessLogGroup = new logs.LogGroup(this, "ApiAccessLogGroup", {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const api = new apigw.RestApi(this, "PrivateApiPublicCert", {
      restApiName: "PrivateApiPublicCert",
      endpointConfiguration: {
        types: [apigw.EndpointType.PRIVATE],
        vpcEndpoints: [vpcEndpoint],
      },
      policy: new iam.PolicyDocument({
        statements: [
          new iam.PolicyStatement({
            effect: iam.Effect.DENY,
            principals: [new iam.AnyPrincipal()],
            actions: ["execute-api:Invoke"],
            resources: ["execute-api:/*"],
            conditions: {
              StringNotEquals: {
                "aws:sourceVpce": vpcEndpoint.vpcEndpointId,
              },
            },
          }),
          new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            principals: [new iam.AnyPrincipal()],
            actions: ["execute-api:Invoke"],
            resources: ["execute-api:/*"],
          }),
        ],
      }),
      domainName: {
        domainName,
        certificate,
        endpointType: apigw.EndpointType.REGIONAL,
      },
      deployOptions: {
        accessLogDestination: new apigw.LogGroupLogDestination(accessLogGroup),
        accessLogFormat: apigw.AccessLogFormat.jsonWithStandardFields(),
        loggingLevel: apigw.MethodLoggingLevel.INFO,
      },
    });

    const requestValidator = new apigw.RequestValidator(
      this,
      "RequestValidator",
      {
        restApi: api,
        validateRequestBody: true,
        validateRequestParameters: true,
      },
    );

    const healthResource = api.root.addResource("health");
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
        requestValidator,
        authorizationType: apigw.AuthorizationType.IAM,
      },
    );

    NagSuppressions.addStackSuppressions(this, [
      {
        id: "AwsSolutions-APIG3",
        reason: "WAF cannot be attached to private API Gateway",
      },
      {
        id: "AwsSolutions-COG4",
        reason:
          "Using IAM auth + VPC endpoint resource policy for access control",
      },
      {
        id: "CdkNagValidationFailure",
        reason: "Security group uses VPC CIDR intrinsic reference",
      },
    ]);

    const privateZone = new route53.PrivateHostedZone(this, "PrivateZone", {
      zoneName: `internal.${props.baseDomain}`,
      vpc: props.vpc,
    });

    new route53.ARecord(this, "ApiAliasRecord", {
      zone: privateZone,
      recordName: `test5`,
      target: route53.RecordTarget.fromAlias(
        new route53targets.ApiGateway(api),
      ),
    });
  }
}
