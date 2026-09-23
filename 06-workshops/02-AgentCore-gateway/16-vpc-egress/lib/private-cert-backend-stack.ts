import * as cdk from "aws-cdk-lib/core";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as elbv2targets from "aws-cdk-lib/aws-elasticloadbalancingv2-targets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cr from "aws-cdk-lib/custom-resources";
import * as route53 from "aws-cdk-lib/aws-route53";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";

/**
 * Deploys an EC2 instance running a REST API with a non-public TLS
 * certificate. Supports two modes:
 *
 * - **Private CA mode**: Pass `certificateAuthorityArn` to issue a certificate
 *   from AWS Private CA (short-lived, 7-day validity). The cert and key are
 *   stored in SSM Parameter Store and the EC2 serves HTTPS on port 443.
 * - **Self-signed mode**: Omit `certificateAuthorityArn` to generate a
 *   self-signed certificate via openssl. EC2 serves HTTP on port 8000 only
 *   (HTTPS setup is handled separately in the self-signed lab).
 *
 * Both produce a certificate that AgentCore Gateway cannot verify.
 */
export interface PrivateCertBackendStackProps extends cdk.StackProps {
  vpc: ec2.IVpc;
  baseDomain: string;
  /** If provided, issues a cert from this Private CA. Otherwise generates self-signed. */
  certificateAuthorityArn?: string;
}

export class PrivateCertBackendStack extends cdk.Stack {
  public readonly instance: ec2.Instance;
  public readonly ec2Sg: ec2.SecurityGroup;

  constructor(
    scope: Construct,
    id: string,
    props: PrivateCertBackendStackProps,
  ) {
    super(scope, id, props);

    const privateDomain = `api.internal.${props.baseDomain}`;
    const usePrivateCa = !!props.certificateAuthorityArn;

    // --- Certificate via Lambda Custom Resource ---
    const certHandler = new lambda.Function(this, "CertHandler", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "index.handler",
      timeout: cdk.Duration.minutes(2),
      code: lambda.Code.fromInline(`
import subprocess
import os
import time
import boto3

def handler(event, context):
    request_type = event['RequestType']
    ssm_prefix = event['ResourceProperties'].get('SsmPrefix', '')

    if request_type == 'Delete':
        cert_arn = event.get('PhysicalResourceId', '')
        if cert_arn.startswith('arn:'):
            try:
                boto3.client('acm').delete_certificate(CertificateArn=cert_arn)
            except Exception:
                pass
        if ssm_prefix:
            ssm = boto3.client('ssm')
            for suffix in ['/key', '/cert', '/chain']:
                try:
                    ssm.delete_parameter(Name=ssm_prefix + suffix)
                except Exception:
                    pass
        return {'PhysicalResourceId': cert_arn}

    domain = event['ResourceProperties']['DomainName']
    ca_arn = event['ResourceProperties'].get('CertificateAuthorityArn', '')

    # Generate private key
    subprocess.run([
        'openssl', 'genrsa', '-out', '/tmp/key.pem', '2048',
    ], check=True, capture_output=True)

    if ca_arn:
        # Private CA mode: generate CSR, issue cert from CA
        subprocess.run([
            'openssl', 'req', '-new', '-key', '/tmp/key.pem',
            '-out', '/tmp/csr.pem', '-subj', f'/CN={domain}',
        ], check=True, capture_output=True)

        with open('/tmp/csr.pem', 'rb') as f:
            csr_bytes = f.read()

        acmpca = boto3.client('acm-pca')
        issue_resp = acmpca.issue_certificate(
            CertificateAuthorityArn=ca_arn,
            Csr=csr_bytes,
            SigningAlgorithm='SHA256WITHRSA',
            Validity={'Value': 7, 'Type': 'DAYS'},
        )

        waiter = acmpca.get_waiter('certificate_issued')
        waiter.wait(
            CertificateAuthorityArn=ca_arn,
            CertificateArn=issue_resp['CertificateArn'],
        )

        get_resp = acmpca.get_certificate(
            CertificateAuthorityArn=ca_arn,
            CertificateArn=issue_resp['CertificateArn'],
        )

        cert_pem = get_resp['Certificate']
        chain_pem = get_resp.get('CertificateChain', '')
    else:
        # Self-signed mode
        subprocess.run([
            'openssl', 'req', '-x509', '-key', '/tmp/key.pem',
            '-out', '/tmp/cert.pem', '-days', '365',
            '-subj', f'/CN={domain}',
        ], check=True, capture_output=True)

        with open('/tmp/cert.pem') as f:
            cert_pem = f.read()
        chain_pem = ''

    with open('/tmp/key.pem') as f:
        key_pem = f.read()

    for p in ['/tmp/key.pem', '/tmp/cert.pem', '/tmp/csr.pem']:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass

    acm_client = boto3.client('acm')

    if request_type == 'Update':
        old_arn = event.get('PhysicalResourceId', '')
        if old_arn.startswith('arn:'):
            try:
                acm_client.delete_certificate(CertificateArn=old_arn)
            except Exception:
                pass

    import_args = {
        'Certificate': cert_pem.encode() if isinstance(cert_pem, str) else cert_pem,
        'PrivateKey': key_pem.encode(),
    }
    if chain_pem:
        import_args['CertificateChain'] = chain_pem.encode() if isinstance(chain_pem, str) else chain_pem

    resp = acm_client.import_certificate(**import_args)

    # Store cert and key in SSM for EC2 to serve HTTPS directly
    if ssm_prefix:
        ssm = boto3.client('ssm')
        ssm.put_parameter(Name=ssm_prefix + '/key', Value=key_pem, Type='SecureString', Overwrite=True)
        full_cert = cert_pem if isinstance(cert_pem, str) else cert_pem.decode()
        if chain_pem:
            chain_str = chain_pem if isinstance(chain_pem, str) else chain_pem.decode()
            full_cert = full_cert + '\\n' + chain_str
            ssm.put_parameter(Name=ssm_prefix + '/chain', Value=chain_str, Type='SecureString', Overwrite=True)
        ssm.put_parameter(Name=ssm_prefix + '/cert', Value=full_cert, Type='SecureString', Overwrite=True)

    return {
        'PhysicalResourceId': resp['CertificateArn'],
        'Data': {'CertificateArn': resp['CertificateArn']},
    }
`),
    });

    const policyActions = ["acm:ImportCertificate", "acm:DeleteCertificate"];
    if (usePrivateCa) {
      policyActions.push("acm-pca:IssueCertificate", "acm-pca:GetCertificate");
      policyActions.push("ssm:PutParameter", "ssm:DeleteParameter");
    }

    certHandler.addToRolePolicy(
      new iam.PolicyStatement({
        actions: policyActions,
        resources: ["*"],
      }),
    );

    const certProvider = new cr.Provider(this, "CertProvider", {
      onEventHandler: certHandler,
    });

    const ssmPrefix = `/vpc-egress-lab/${this.stackName}`;
    const certProperties: Record<string, string> = {
      DomainName: privateDomain,
    };
    if (props.certificateAuthorityArn) {
      certProperties["CertificateAuthorityArn"] = props.certificateAuthorityArn;
      certProperties["SsmPrefix"] = ssmPrefix;
    }

    const cert = new cdk.CustomResource(this, "Cert", {
      serviceToken: certProvider.serviceToken,
      properties: certProperties,
    });

    const certArn = cert.getAttString("CertificateArn");

    // --- EC2 Instance running REST API ---
    this.ec2Sg = new ec2.SecurityGroup(this, "Ec2Sg", {
      vpc: props.vpc,
      description: "Simple API EC2 instance",
      allowAllOutbound: true,
    });
    this.ec2Sg.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);

    this.instance = new ec2.Instance(this, "SimpleApiInstance", {
      vpc: props.vpc,
      instanceType: ec2.InstanceType.of(
        ec2.InstanceClass.T3,
        ec2.InstanceSize.MICRO,
      ),
      machineImage: ec2.MachineImage.latestAmazonLinux2023(),
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      securityGroup: this.ec2Sg,
      ssmSessionPermissions: true,
    });

    this.instance.addUserData(
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
      // HTTP service on port 8000 (for existing ALB)
      "cat > /etc/systemd/system/simple-api.service << 'SVCEOF'",
      "[Unit]",
      "Description=Simple API Server (HTTP)",
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

    // HTTPS on port 443 with non-public certificate
    if (usePrivateCa) {
      // Private CA mode: pull cert from SSM Parameter Store
      this.instance.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ["ssm:GetParameter"],
          resources: [
            `arn:aws:ssm:${this.region}:${this.account}:parameter/vpc-egress-lab/${this.stackName}/*`,
          ],
        }),
      );

      this.instance.node.addDependency(cert);

      this.instance.addUserData(
        `# Pull TLS cert and key from SSM Parameter Store`,
        `SSM_PREFIX="/vpc-egress-lab/${this.stackName}"`,
        `REGION="${this.region}"`,
        'aws ssm get-parameter --name "${SSM_PREFIX}/cert" --with-decryption --query "Parameter.Value" --output text --region $REGION > /opt/simple-api/cert.pem',
        'aws ssm get-parameter --name "${SSM_PREFIX}/key" --with-decryption --query "Parameter.Value" --output text --region $REGION > /opt/simple-api/key.pem',
      );
    } else {
      // Self-signed mode: generate cert directly on EC2
      this.instance.addUserData(
        `# Generate self-signed certificate`,
        `DOMAIN="${privateDomain}"`,
        'openssl req -x509 -newkey rsa:2048 -keyout /opt/simple-api/key.pem -out /opt/simple-api/cert.pem -days 365 -nodes -subj "/CN=$DOMAIN"',
      );
    }

    // HTTPS service on port 443 (both modes)
    this.instance.addUserData(
      "# HTTPS service on port 443",
      "cat > /etc/systemd/system/simple-api-https.service << 'SVCEOF'",
      "[Unit]",
      `Description=Simple API Server (HTTPS with ${usePrivateCa ? "private CA" : "self-signed"} cert)`,
      "After=network.target",
      "",
      "[Service]",
      "Type=simple",
      "WorkingDirectory=/opt/simple-api",
      "ExecStart=/usr/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port 443 --ssl-keyfile=/opt/simple-api/key.pem --ssl-certfile=/opt/simple-api/cert.pem",
      "Restart=always",
      "",
      "[Install]",
      "WantedBy=multi-user.target",
      "SVCEOF",
      "systemctl daemon-reload",
      "systemctl enable simple-api-https",
      "systemctl start simple-api-https",
    );

    // --- Security group ingress rules ---
    this.ec2Sg.addIngressRule(
      ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
      ec2.Port.tcp(443),
      "Allow HTTPS from VPC (proxy ALB to EC2 with private cert)",
    );

    this.ec2Sg.addIngressRule(
      ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
      ec2.Port.tcp(8000),
      "Allow HTTP from VPC (health checks and testing)",
    );

    // --- Private Hosted Zone ---
    // Resolves the cert domain (e.g., api.internal.example.com) to the EC2's private IP
    // so TLS connections using the domain match the certificate's CN
    const zone = new route53.PrivateHostedZone(this, "PrivateZone", {
      zoneName: `internal.${props.baseDomain}`,
      vpc: props.vpc,
    });

    new route53.ARecord(this, "ApiRecord", {
      zone,
      recordName: "api",
      target: route53.RecordTarget.fromIpAddresses(
        this.instance.instancePrivateIp,
      ),
      ttl: cdk.Duration.seconds(60),
    });

    // --- Internal NLB (TLS passthrough) ---
    // Provides a publicly resolvable DNS name for the EC2's private cert endpoint.
    // Used as routingDomain to demonstrate that AgentCore rejects the private cert.
    const nlbAccessLogBucket = new s3.Bucket(this, "NlbAccessLogs", {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      enforceSSL: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      lifecycleRules: [{ expiration: cdk.Duration.days(30) }],
    });

    const nlbSg = new ec2.SecurityGroup(this, "NlbSg", {
      vpc: props.vpc,
      description: "NLB security group - TLS passthrough to EC2",
      allowAllOutbound: true,
    });
    nlbSg.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);
    nlbSg.addIngressRule(
      ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
      ec2.Port.tcp(443),
      "Allow HTTPS from VPC",
    );

    const nlb = new elbv2.NetworkLoadBalancer(this, "Nlb", {
      vpc: props.vpc,
      internetFacing: false,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      securityGroups: [nlbSg],
      crossZoneEnabled: true,
    });

    nlb.logAccessLogs(nlbAccessLogBucket, "nlb-logs");

    const nlbListener = nlb.addListener("TlsPassthrough", {
      port: 443,
      protocol: elbv2.Protocol.TCP,
    });

    nlbListener.addTargets("Ec2Target", {
      port: 443,
      protocol: elbv2.Protocol.TCP,
      targets: [
        new elbv2targets.IpTarget(this.instance.instancePrivateIp, 443),
      ],
      healthCheck: {
        protocol: elbv2.Protocol.TCP,
        port: "8000",
      },
    });

    // --- Outputs ---
    const certType = usePrivateCa ? "private CA" : "self-signed";

    new cdk.CfnOutput(this, "NlbDnsName", {
      value: nlb.loadBalancerDnsName,
      description:
        "Internal NLB DNS (publicly resolvable — use as routingDomain for fail test)",
    });

    new cdk.CfnOutput(this, "NlbSgId", {
      value: nlbSg.securityGroupId,
      description: "NLB security group (use for Resource Gateway ENIs)",
    });

    new cdk.CfnOutput(this, "Ec2InstanceId", {
      value: this.instance.instanceId,
      description: "SSM Session Manager: aws ssm start-session --target <id>",
    });

    new cdk.CfnOutput(this, "Ec2PrivateIp", {
      value: this.instance.instancePrivateIp,
    });

    new cdk.CfnOutput(this, "CertArn", {
      value: certArn,
      description: `Certificate ARN (${certType} — not trusted by AgentCore)`,
    });

    new cdk.CfnOutput(this, "ApiKey", {
      value: "vpc-egress-lab-api-key",
      description: "API key for the simple REST API (x-api-key header)",
    });

    new cdk.CfnOutput(this, "CertDomain", {
      value: privateDomain,
      description: `Domain on the ${certType} certificate`,
    });

    NagSuppressions.addStackSuppressions(this, [
      {
        id: "AwsSolutions-IAM4",
        reason:
          "SSM managed policy and Lambda basic execution role use AWS managed policies",
      },
      {
        id: "AwsSolutions-IAM5",
        reason: "SSM, ACM, and ACM-PCA operations require wildcards",
      },
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
        id: "AwsSolutions-L1",
        reason: "Lambda runtime is current at time of writing",
      },
      {
        id: "CdkNagValidationFailure",
        reason: "Security group uses VPC CIDR intrinsic reference",
      },
    ]);
  }
}
