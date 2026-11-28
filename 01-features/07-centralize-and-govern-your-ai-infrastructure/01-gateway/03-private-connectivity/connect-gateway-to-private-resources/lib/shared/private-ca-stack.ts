import * as cdk from "aws-cdk-lib/core";
import * as acmpca from "aws-cdk-lib/aws-acmpca";
import { Construct } from "constructs";

export interface PrivateCaStackProps extends cdk.StackProps {
  baseDomain: string;
}

export class PrivateCaStack extends cdk.Stack {
  public readonly caArn: string;

  constructor(scope: Construct, id: string, props: PrivateCaStackProps) {
    super(scope, id, props);

    const ca = new acmpca.CfnCertificateAuthority(this, "RootCA", {
      type: "ROOT",
      keyAlgorithm: "RSA_2048",
      signingAlgorithm: "SHA256WITHRSA",
      subject: {
        commonName: `Private CA - ${props.baseDomain}`,
        organization: "VPC Egress Testing",
      },
    });

    const caCert = new acmpca.CfnCertificate(this, "RootCACert", {
      certificateAuthorityArn: ca.attrArn,
      certificateSigningRequest: ca.attrCertificateSigningRequest,
      signingAlgorithm: "SHA256WITHRSA",
      templateArn: "arn:aws:acm-pca:::template/RootCACertificate/V1",
      validity: {
        type: "YEARS",
        value: 10,
      },
    });

    new acmpca.CfnCertificateAuthorityActivation(this, "RootCAActivation", {
      certificateAuthorityArn: ca.attrArn,
      certificate: caCert.attrCertificate,
    });

    this.caArn = ca.attrArn;

    new cdk.CfnOutput(this, "CertificateAuthorityArn", {
      value: ca.attrArn,
      exportName: "PrivateCaArn",
    });
  }
}
