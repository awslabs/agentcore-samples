import * as cdk from "aws-cdk-lib/core";
import * as cr from "aws-cdk-lib/custom-resources";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as iam from "aws-cdk-lib/aws-iam";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";

export interface VpcegressStackProps extends cdk.StackProps {
  vpcCidr: string;
}

export class VpcegressStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;

  constructor(scope: Construct, id: string, props: VpcegressStackProps) {
    super(scope, id, props);

    this.vpc = new ec2.Vpc(this, "VpcEgress", {
      ipAddresses: ec2.IpAddresses.cidr(props.vpcCidr),
      maxAzs: 2,
      enableDnsSupport: true,
      enableDnsHostnames: true,
      subnetConfiguration: [
        {
          cidrMask: 24,
          name: "Public",
          subnetType: ec2.SubnetType.PUBLIC,
        },
        {
          cidrMask: 24,
          name: "PrivateWithNat",
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
        },
        {
          cidrMask: 24,
          name: "PrivateIsolated",
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
        },
      ],
    });

    const flowLogGroup = new logs.LogGroup(this, "VpcFlowLogGroup", {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const flowLogRole = new iam.Role(this, "VpcFlowLogRole", {
      assumedBy: new iam.ServicePrincipal("vpc-flow-logs.amazonaws.com"),
    });

    this.vpc.addFlowLog("FlowLog", {
      destination: ec2.FlowLogDestination.toCloudWatchLogs(
        flowLogGroup,
        flowLogRole,
      ),
      trafficType: ec2.FlowLogTrafficType.ALL,
    });

    // Pre-delete guard, not a VPC-wide garbage collector. Other stacks and SDK
    // labs own their SGs. Refuse deletion until their explicit cleanup completes.
    const sgCleanupFn = new lambda.Function(this, "SgCleanupFn", {
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: "index.handler",
      timeout: cdk.Duration.minutes(5),
      code: lambda.Code.fromInline(`
import boto3

def handler(event, context):
    # cr.Provider owns CloudFormation responses; do not use cfnresponse here.
    physical_id = event.get("PhysicalResourceId", event["ResourceProperties"]["VpcId"])
    if event["RequestType"] != "Delete":
        return {"PhysicalResourceId": physical_id}
    ec2 = boto3.client("ec2")
    remaining = []
    for page in ec2.get_paginator("describe_security_groups").paginate(
        Filters=[{"Name": "vpc-id", "Values": [event["ResourceProperties"]["VpcId"]]}]
    ):
        remaining.extend(sg["GroupId"] for sg in page["SecurityGroups"] if sg["GroupName"] != "default")
    if remaining:
        raise RuntimeError(f"Clean up owned lab security groups before deleting VPC: {remaining}")
    return {"PhysicalResourceId": physical_id}
`),
    });

    sgCleanupFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["ec2:DescribeSecurityGroups"],
        resources: ["*"],
      }),
    );

    const sgCleanup = new cdk.CustomResource(this, "SgCleanup", {
      serviceToken: new cr.Provider(this, "SgCleanupProvider", {
        onEventHandler: sgCleanupFn,
      }).serviceToken,
      properties: {
        VpcId: this.vpc.vpcId,
      },
    });

    // Reverse dependency order runs this guard before deleting the VPC.

    new cdk.CfnOutput(this, "VpcId", { value: this.vpc.vpcId });
    new cdk.CfnOutput(this, "PublicSubnetIds", {
      value: this.vpc.publicSubnets.map((s) => s.subnetId).join(","),
    });
    new cdk.CfnOutput(this, "PrivateSubnetIds", {
      value: this.vpc.privateSubnets.map((s) => s.subnetId).join(","),
    });
    new cdk.CfnOutput(this, "IsolatedSubnetIds", {
      value: this.vpc.isolatedSubnets.map((s) => s.subnetId).join(","),
    });

    NagSuppressions.addStackSuppressions(this, [
      { id: "AwsSolutions-IAM4", reason: "Lambda basic execution role managed by CDK" },
      { id: "AwsSolutions-IAM5", reason: "EC2 DescribeSecurityGroups does not support resource-level permissions; guard never mutates SGs" },
      { id: "AwsSolutions-L1", reason: "Lambda runtime is set to latest available Python" },
    ], true);
  }
}
