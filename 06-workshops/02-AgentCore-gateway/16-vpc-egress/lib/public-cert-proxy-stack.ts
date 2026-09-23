import * as cdk from 'aws-cdk-lib/core';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as elbv2targets from 'aws-cdk-lib/aws-elasticloadbalancingv2-targets';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';

/**
 * Workaround ALB with a public ACM certificate. This ALB provides
 * a publicly trusted TLS termination point that AgentCore can verify.
 *
 * It targets the same backend EC2 instance as the customer's existing
 * private-cert ALB, providing an alternative entry point for AgentCore.
 */
export interface PublicCertProxyStackProps extends cdk.StackProps {
  vpc: ec2.IVpc;
  publicCertArn: string;
  backendInstance: ec2.Instance;
}

export class PublicCertProxyStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: PublicCertProxyStackProps) {
    super(scope, id, props);

    const publicCert = acm.Certificate.fromCertificateArn(this, 'PublicCert', props.publicCertArn);

    // --- Internal ALB with public certificate ---
    const albSg = new ec2.SecurityGroup(this, 'AlbSg', {
      vpc: props.vpc,
      description: 'Public cert proxy ALB - HTTPS from VPC',
      allowAllOutbound: true,
    });
    albSg.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);
    albSg.addIngressRule(
      ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
      ec2.Port.tcp(443),
      'Allow HTTPS from VPC',
    );

    // The backend EC2 security group already allows port 8000 from the VPC CIDR,
    // which covers this ALB. No additional ingress rule needed here — adding one
    // would create a circular cross-stack dependency.

    const accessLogBucket = new s3.Bucket(this, 'AlbAccessLogs', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      enforceSSL: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      lifecycleRules: [{ expiration: cdk.Duration.days(30) }],
    });

    const alb = new elbv2.ApplicationLoadBalancer(this, 'PublicCertAlb', {
      vpc: props.vpc,
      internetFacing: false,
      securityGroup: albSg,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    alb.logAccessLogs(accessLogBucket, 'alb-logs');

    // HTTPS listener with public cert — terminates TLS and forwards HTTP to EC2
    const httpsListener = alb.addListener('HttpsListener', {
      port: 443,
      protocol: elbv2.ApplicationProtocol.HTTPS,
      certificates: [publicCert],
    });

    httpsListener.addTargets('BackendTarget', {
      port: 8000,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targets: [new elbv2targets.InstanceTarget(props.backendInstance, 8000)],
      healthCheck: {
        path: '/health',
        port: '8000',
        healthyHttpCodes: '200',
      },
    });

    // --- Outputs ---
    new cdk.CfnOutput(this, 'AlbDnsName', {
      value: alb.loadBalancerDnsName,
      description: 'Public cert ALB DNS (publicly resolvable, use as routingDomain)',
    });

    new cdk.CfnOutput(this, 'AlbSgId', {
      value: albSg.securityGroupId,
    });

    NagSuppressions.addStackSuppressions(this, [
      { id: 'AwsSolutions-S1', reason: 'Access log bucket does not need its own access logs' },
      { id: 'AwsSolutions-EC23', reason: 'ALB is internal, SG allows VPC CIDR only' },
      { id: 'CdkNagValidationFailure', reason: 'Security group uses VPC CIDR intrinsic reference' },
    ]);
  }
}
