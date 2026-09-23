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

    // --- Pre-delete SG cleanup ---
    // On stack deletion, this custom resource attempts to delete all non-default
    // security groups in the VPC. If any SG still has ENI dependencies (e.g. from
    // VPC Lattice Resource Gateway), the Lambda fails and prevents VPC deletion.
    const sgCleanupFn = new lambda.Function(this, "SgCleanupFn", {
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: "index.handler",
      timeout: cdk.Duration.minutes(5),
      code: lambda.Code.fromInline(`
import boto3
import cfnresponse

def handler(event, context):
    if event["RequestType"] in ("Create", "Update"):
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
        return

    # Delete event — clean up retained SGs before VPC deletion
    vpc_id = event["ResourceProperties"]["VpcId"]
    ec2 = boto3.client("ec2")

    # Find all non-default SGs in this VPC
    sgs = ec2.describe_security_groups(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )["SecurityGroups"]
    non_default = [sg for sg in sgs if sg["GroupName"] != "default"]

    if not non_default:
        print(f"No non-default SGs in {vpc_id}")
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
        return

    print(f"Found {len(non_default)} non-default SGs in {vpc_id}")
    failed = []

    for sg in non_default:
        sg_id = sg["GroupId"]
        try:
            ec2.delete_security_group(GroupId=sg_id)
            print(f"Deleted SG: {sg_id} ({sg['GroupName']})")
        except Exception as e:
            if "DependencyViolation" in str(e):
                failed.append(sg_id)
                print(f"BLOCKED: {sg_id} ({sg['GroupName']}) still has ENI dependencies")
            else:
                failed.append(sg_id)
                print(f"FAILED: {sg_id} ({sg['GroupName']}): {e}")

    if failed:
        msg = f"Cannot delete VPC — {len(failed)} SGs still have dependencies: {failed}"
        print(msg)
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, reason=msg)
    else:
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
`),
    });

    sgCleanupFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "ec2:DescribeSecurityGroups",
          "ec2:DeleteSecurityGroup",
        ],
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

    // sgCleanup implicitly depends on the VPC (via vpcId ref).
    // On deletion, CloudFormation deletes in reverse dependency order:
    // sgCleanup (runs Lambda to delete SGs) → then VPC.

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
      { id: "AwsSolutions-IAM5", reason: "SG cleanup Lambda needs to describe/delete any SG in the VPC" },
      { id: "AwsSolutions-L1", reason: "Lambda runtime is set to latest available Python" },
    ], true);
  }
}
