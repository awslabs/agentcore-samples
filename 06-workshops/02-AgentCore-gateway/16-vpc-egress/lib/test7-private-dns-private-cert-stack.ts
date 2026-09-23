import * as cdk from "aws-cdk-lib/core";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as acm from "aws-cdk-lib/aws-certificatemanager";
import * as acmpca from "aws-cdk-lib/aws-acmpca";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as elbv2targets from "aws-cdk-lib/aws-elasticloadbalancingv2-targets";
import * as route53 from "aws-cdk-lib/aws-route53";
import * as s3 from "aws-cdk-lib/aws-s3";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";

export interface PrivateDnsPrivateCertStackProps extends cdk.StackProps {
  vpc: ec2.IVpc;
  baseDomain: string;
  certificateAuthorityArn: string;
  publicCertArn: string;
}

export class PrivateDnsPrivateCertStack extends cdk.Stack {
  constructor(
    scope: Construct,
    id: string,
    props: PrivateDnsPrivateCertStackProps,
  ) {
    super(scope, id, props);

    const privateDomain = `test7.internal.${props.baseDomain}`;

    // --- Private Certificate (represents customer's existing private cert) ---
    // This cert is issued by AWS Private CA. AgentCore cannot verify it because
    // it only trusts public CAs. The ALB workaround below solves this.
    const privateCert = new acm.PrivateCertificate(this, "PrivateCert", {
      domainName: privateDomain,
      certificateAuthority:
        acmpca.CertificateAuthority.fromCertificateAuthorityArn(
          this,
          "CA",
          props.certificateAuthorityArn,
        ),
    });

    // --- EC2 Instance running simple REST API on HTTP :8000 ---
    const ec2Sg = new ec2.SecurityGroup(this, "Ec2Sg", {
      vpc: props.vpc,
      description: "Simple API EC2 instance",
      allowAllOutbound: true,
    });
    ec2Sg.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);

    const instance = new ec2.Instance(this, "SimpleApiInstance", {
      vpc: props.vpc,
      instanceType: ec2.InstanceType.of(
        ec2.InstanceClass.T3,
        ec2.InstanceSize.MICRO,
      ),
      machineImage: ec2.MachineImage.latestAmazonLinux2023(),
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      securityGroup: ec2Sg,
      ssmSessionPermissions: true,
    });

    instance.addUserData(
      "#!/bin/bash",
      "dnf update -y",
      "dnf install -y python3-pip",
      "pip3 install fastapi uvicorn",
      "mkdir -p /opt/simple-api",
      "cat > /opt/simple-api/app.py << 'PYEOF'",
      "from fastapi import FastAPI, Depends, Header, HTTPException",
      "",
      'API_KEY = "vpc-egress-lab-api-key"',
      "",
      "",
      "def verify_api_key(x_api_key: str = Header(...)):",
      "    if x_api_key != API_KEY:",
      '        raise HTTPException(status_code=403, detail="Invalid API key")',
      "",
      "",
      "app = FastAPI(dependencies=[Depends(verify_api_key)])",
      "items: list[dict] = []",
      "",
      "",
      '@app.get("/health")',
      "def health():",
      '    return {"status": "ok"}',
      "",
      "",
      '@app.get("/items")',
      "def list_items():",
      "    return items",
      "",
      "",
      '@app.post("/items")',
      "def create_item(item: dict):",
      "    items.append(item)",
      "    return item",
      "PYEOF",
      "cat > /etc/systemd/system/simple-api.service << 'SVCEOF'",
      "[Unit]",
      "Description=Simple API Server",
      "After=network.target",
      "",
      "[Service]",
      "Type=simple",
      "WorkingDirectory=/opt/simple-api",
      "ExecStart=/usr/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port 8000",
      "Restart=always",
      "",
      "[Install]",
      "WantedBy=multi-user.target",
      "SVCEOF",
      "systemctl daemon-reload",
      "systemctl enable simple-api",
      "systemctl start simple-api",
    );

    // --- Route 53 Private Hosted Zone ---
    // Maps test7.internal.{baseDomain} to the EC2 private IP.
    // This domain is only resolvable inside the VPC.
    const privateZone = new route53.PrivateHostedZone(this, "PrivateZone", {
      zoneName: `internal.${props.baseDomain}`,
      vpc: props.vpc,
    });

    new route53.ARecord(this, "Ec2Record", {
      zone: privateZone,
      recordName: "test7",
      target: route53.RecordTarget.fromIpAddresses(instance.instancePrivateIp),
    });

    // --- Internal ALB with public cert (the workaround) ---
    // AgentCore requires a publicly trusted TLS certificate. Since the customer's
    // resource uses a private cert, we place an ALB in front with a public cert.
    const albSg = new ec2.SecurityGroup(this, "AlbSg", {
      vpc: props.vpc,
      description: "Internal ALB - HTTPS from VPC",
      allowAllOutbound: true,
    });
    albSg.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);
    albSg.addIngressRule(
      ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
      ec2.Port.tcp(443),
      "Allow HTTPS from VPC",
    );

    // Allow ALB to reach EC2 on port 8000
    ec2Sg.addIngressRule(albSg, ec2.Port.tcp(8000), "Allow traffic from ALB");

    const publicCert = acm.Certificate.fromCertificateArn(
      this,
      "PublicCert",
      props.publicCertArn,
    );

    const accessLogBucket = new s3.Bucket(this, "AlbAccessLogs", {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      enforceSSL: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      lifecycleRules: [{ expiration: cdk.Duration.days(30) }],
    });

    const alb = new elbv2.ApplicationLoadBalancer(this, "InternalAlb", {
      vpc: props.vpc,
      internetFacing: false,
      securityGroup: albSg,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    alb.logAccessLogs(accessLogBucket, "alb-logs");

    // HTTPS listener with public cert — terminates TLS and forwards HTTP to EC2
    const httpsListener = alb.addListener("HttpsListener", {
      port: 443,
      protocol: elbv2.ApplicationProtocol.HTTPS,
      certificates: [publicCert],
    });

    httpsListener.addTargets("Ec2Target", {
      port: 8000,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targets: [new elbv2targets.InstanceTarget(instance, 8000)],
      healthCheck: {
        path: "/health",
        port: "8000",
        healthyHttpCodes: "200",
      },
    });

    // --- Outputs ---
    new cdk.CfnOutput(this, "AlbDnsName", {
      value: alb.loadBalancerDnsName,
      description:
        "Internal ALB DNS name (publicly resolvable, use as routingDomain)",
    });

    new cdk.CfnOutput(this, "AlbSgId", {
      value: albSg.securityGroupId,
    });

    new cdk.CfnOutput(this, "Ec2InstanceId", {
      value: instance.instanceId,
      description: "SSM Session Manager: aws ssm start-session --target <id>",
    });

    new cdk.CfnOutput(this, "Ec2PrivateIp", {
      value: instance.instancePrivateIp,
    });

    new cdk.CfnOutput(this, "ApiKey", {
      value: "vpc-egress-lab-api-key",
      description: "API key for the simple REST API (x-api-key header)",
    });

    new cdk.CfnOutput(this, "PrivateDomainName", {
      value: privateDomain,
      description: "Private domain name (only resolvable inside VPC)",
    });

    new cdk.CfnOutput(this, "PrivateCertArn", {
      value: privateCert.certificateArn,
      description:
        "Private certificate ARN (not usable with AgentCore directly)",
    });

    NagSuppressions.addStackSuppressions(this, [
      {
        id: "AwsSolutions-IAM4",
        reason: "SSM managed policy required for Session Manager access",
      },
      { id: "AwsSolutions-IAM5", reason: "SSM managed policies use wildcards" },
      {
        id: "AwsSolutions-EC26",
        reason: "EBS encryption not needed for lab instance",
      },
      {
        id: "AwsSolutions-EC28",
        reason: "Detailed monitoring not needed for lab instance",
      },
      {
        id: "AwsSolutions-EC29",
        reason: "Lab instance does not need termination protection",
      },
      {
        id: "AwsSolutions-S1",
        reason: "Access log bucket does not need its own access logs",
      },
      {
        id: "AwsSolutions-EC23",
        reason: "ALB is internal, SG allows VPC CIDR only",
      },
      {
        id: "CdkNagValidationFailure",
        reason: "Security group uses VPC CIDR intrinsic reference",
      },
    ]);
  }
}
