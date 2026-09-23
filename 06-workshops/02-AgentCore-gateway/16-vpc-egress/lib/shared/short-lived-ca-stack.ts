import * as cdk from "aws-cdk-lib/core";
import * as acmpca from "aws-cdk-lib/aws-acmpca";
import { Construct } from "constructs";

/**
 * AWS Private CA in short-lived certificate mode ($50/month instead of $400/month).
 * Certificates issued by this CA are valid for up to 7 days.
 * Suitable for labs and short-lived workloads.
 */
export interface ShortLivedCaStackProps extends cdk.StackProps {
  baseDomain: string;
}

export class ShortLivedCaStack extends cdk.Stack {
  public readonly caArn: string;

  constructor(scope: Construct, id: string, props: ShortLivedCaStackProps) {
    super(scope, id, props);

    const ca = new acmpca.CfnCertificateAuthority(this, "ShortLivedCA", {
      type: "ROOT",
      keyAlgorithm: "RSA_2048",
      signingAlgorithm: "SHA256WITHRSA",
      usageMode: "SHORT_LIVED_CERTIFICATE",
      subject: {
        commonName: `Short-Lived CA - ${props.baseDomain}`,
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
      exportName: "ShortLivedCaArn",
    });
  }
}
