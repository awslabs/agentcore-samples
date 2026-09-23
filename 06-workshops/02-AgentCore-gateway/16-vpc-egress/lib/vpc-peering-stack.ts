import * as cdk from "aws-cdk-lib/core";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as cr from "aws-cdk-lib/custom-resources";
import * as iam from "aws-cdk-lib/aws-iam";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";

export interface VpcPeeringStackProps extends cdk.StackProps {
  /** Local VPC (requester side) */
  vpc: ec2.IVpc;
  /** Peer VPC ID */
  peerVpcId: string;
  /** Peer region (e.g. 'us-east-1') */
  peerRegion: string;
  /** Peer VPC CIDR (for routes in local VPC) */
  peerVpcCidr: string;
  /** Local VPC CIDR (for routes in peer VPC) */
  localVpcCidr: string;
  /** Route table IDs of peer VPC private subnets (for adding return routes) */
  peerPrivateRouteTableIds: string[];
}

export class VpcPeeringStack extends cdk.Stack {
  public readonly peeringConnectionId: string;

  constructor(scope: Construct, id: string, props: VpcPeeringStackProps) {
    super(scope, id, props);

    // 1. Create VPC peering connection (requester side)
    const peering = new ec2.CfnVPCPeeringConnection(this, "VpcPeering", {
      vpcId: props.vpc.vpcId,
      peerVpcId: props.peerVpcId,
      peerRegion: props.peerRegion,
      tags: [{ key: "Name", value: "agentcore-peering-lab" }],
    });
    this.peeringConnectionId = peering.ref;

    // 2. Accept peering in peer region (cross-region requires explicit acceptance)
    const acceptPeering = new cr.AwsCustomResource(this, "AcceptPeering", {
      onCreate: {
        service: "EC2",
        action: "acceptVpcPeeringConnection",
        parameters: { VpcPeeringConnectionId: peering.ref },
        region: props.peerRegion,
        physicalResourceId: cr.PhysicalResourceId.of("accept-peering"),
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ["ec2:AcceptVpcPeeringConnection"],
          resources: ["*"],
        }),
      ]),
    });
    acceptPeering.node.addDependency(peering);

    // 3. Add routes in local VPC private subnets -> peer VPC CIDR via peering
    props.vpc.privateSubnets.forEach((subnet, i) => {
      const route = new ec2.CfnRoute(this, `RouteLocal${i}`, {
        routeTableId: subnet.routeTable.routeTableId,
        destinationCidrBlock: props.peerVpcCidr,
        vpcPeeringConnectionId: peering.ref,
      });
      route.addDependency(peering);
    });

    // 4. Add routes in peer VPC private subnets -> local VPC CIDR via peering
    //    Uses AwsCustomResource because the route tables are in a different region
    props.peerPrivateRouteTableIds.forEach((rtId, i) => {
      const peerRoute = new cr.AwsCustomResource(this, `RoutePeer${i}`, {
        onCreate: {
          service: "EC2",
          action: "createRoute",
          parameters: {
            RouteTableId: rtId,
            DestinationCidrBlock: props.localVpcCidr,
            VpcPeeringConnectionId: peering.ref,
          },
          region: props.peerRegion,
          physicalResourceId: cr.PhysicalResourceId.of(`route-peer-${i}`),
        },
        onDelete: {
          service: "EC2",
          action: "deleteRoute",
          parameters: {
            RouteTableId: rtId,
            DestinationCidrBlock: props.localVpcCidr,
          },
          region: props.peerRegion,
        },
        policy: cr.AwsCustomResourcePolicy.fromStatements([
          new iam.PolicyStatement({
            actions: ["ec2:CreateRoute", "ec2:DeleteRoute"],
            resources: ["*"],
          }),
        ]),
      });
      peerRoute.node.addDependency(acceptPeering);
    });

    new cdk.CfnOutput(this, "PeeringConnectionId", {
      value: peering.ref,
    });

    NagSuppressions.addStackSuppressions(this, [
      {
        id: "AwsSolutions-IAM4",
        reason:
          "AWSLambdaBasicExecutionRole is required by AwsCustomResource Lambda",
      },
      {
        id: "AwsSolutions-IAM5",
        reason: "Custom resources need wildcard for cross-region EC2 API calls",
      },
      {
        id: "AwsSolutions-L1",
        reason: "Lambda runtime managed by AwsCustomResource construct",
      },
    ]);
  }
}
