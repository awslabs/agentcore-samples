import * as cdk from "aws-cdk-lib/core";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as eks from "aws-cdk-lib/aws-eks";
import * as iam from "aws-cdk-lib/aws-iam";
import { KubectlV31Layer } from "@aws-cdk/lambda-layer-kubectl-v31";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";

export interface EksClusterStackProps extends cdk.StackProps {
  vpc: ec2.IVpc;
}

export class EksClusterStack extends cdk.Stack {
  public readonly cluster: eks.Cluster;

  constructor(scope: Construct, id: string, props: EksClusterStackProps) {
    super(scope, id, props);

    const clusterAdminRole = new iam.Role(this, "ClusterAdminRole", {
      assumedBy: new iam.AccountRootPrincipal(),
    });

    this.cluster = new eks.Cluster(this, "EksCluster", {
      vpc: props.vpc,
      version: eks.KubernetesVersion.V1_31,
      kubectlLayer: new KubectlV31Layer(this, "KubectlLayer"),
      defaultCapacity: 0,
      mastersRole: clusterAdminRole,
      endpointAccess: eks.EndpointAccess.PRIVATE,
      authenticationMode: eks.AuthenticationMode.API_AND_CONFIG_MAP,
      vpcSubnets: [{ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }],
      clusterLogging: [
        eks.ClusterLoggingTypes.API,
        eks.ClusterLoggingTypes.AUDIT,
        eks.ClusterLoggingTypes.AUTHENTICATOR,
        eks.ClusterLoggingTypes.CONTROLLER_MANAGER,
        eks.ClusterLoggingTypes.SCHEDULER,
      ],
    });

    // Allow cross-stack Lambdas to assume the kubectl role
    // (needed when other stacks use fromClusterAttributes to deploy manifests)
    const kubectlRole = this.cluster.kubectlRole! as iam.Role;
    kubectlRole.assumeRolePolicy!.addStatements(
      new iam.PolicyStatement({
        actions: ["sts:AssumeRole"],
        principals: [new iam.AccountRootPrincipal()],
      }),
    );

    // Allow kubectl Lambdas from child stacks to reach the EKS API
    // (fromClusterAttributes creates new Lambdas whose SGs aren't auto-added)
    this.cluster.clusterSecurityGroup.addIngressRule(
      ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
      ec2.Port.tcp(443),
      "Allow kubectl Lambdas and bastion to reach EKS API",
    );

    this.cluster.addNodegroupCapacity("ManagedNodeGroup", {
      instanceTypes: [new ec2.InstanceType("t3.medium")],
      minSize: 1,
      maxSize: 3,
      desiredSize: 1,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    // --- AWS Load Balancer Controller ---
    // Required for NLB creation via Kubernetes Service annotations
    const lbControllerSa = this.cluster.addServiceAccount("LbControllerSa", {
      name: "aws-load-balancer-controller",
      namespace: "kube-system",
    });

    // IAM policy derived from the official AWS Load Balancer Controller docs:
    // https://kubernetes-sigs.github.io/aws-load-balancer-controller/
    // Wildcard resources are required because the controller manages NLBs/ALBs
    // whose ARNs are not known at deploy time.
    const lbControllerPolicy = new iam.Policy(this, "LbControllerPolicy", {
      statements: [
        new iam.PolicyStatement({
          actions: [
            "ec2:DescribeAccountAttributes",
            "ec2:DescribeAddresses",
            "ec2:DescribeAvailabilityZones",
            "ec2:DescribeInternetGateways",
            "ec2:DescribeVpcs",
            "ec2:DescribeVpcPeeringConnections",
            "ec2:DescribeSubnets",
            "ec2:DescribeSecurityGroups",
            "ec2:DescribeInstances",
            "ec2:DescribeNetworkInterfaces",
            "ec2:DescribeTags",
            "ec2:DescribeCoipPools",
            "ec2:GetCoipPoolUsage",
            "ec2:DescribeTargetGroups",
            "ec2:DescribeTargetHealth",
            "ec2:DescribeListeners",
            "ec2:DescribeRules",
            "elasticloadbalancing:DescribeLoadBalancers",
            "elasticloadbalancing:DescribeLoadBalancerAttributes",
            "elasticloadbalancing:DescribeListeners",
            "elasticloadbalancing:DescribeListenerCertificates",
            "elasticloadbalancing:DescribeSSLPolicies",
            "elasticloadbalancing:DescribeRules",
            "elasticloadbalancing:DescribeTargetGroups",
            "elasticloadbalancing:DescribeTargetGroupAttributes",
            "elasticloadbalancing:DescribeTargetHealth",
            "elasticloadbalancing:DescribeTags",
          ],
          resources: ["*"],
        }),
        new iam.PolicyStatement({
          actions: [
            "ec2:AuthorizeSecurityGroupIngress",
            "ec2:RevokeSecurityGroupIngress",
            "ec2:CreateSecurityGroup",
            "ec2:DeleteSecurityGroup",
            "ec2:CreateTags",
            "ec2:DeleteTags",
          ],
          resources: ["*"],
        }),
        new iam.PolicyStatement({
          actions: [
            "elasticloadbalancing:CreateLoadBalancer",
            "elasticloadbalancing:CreateTargetGroup",
            "elasticloadbalancing:CreateListener",
            "elasticloadbalancing:CreateRule",
            "elasticloadbalancing:DeleteLoadBalancer",
            "elasticloadbalancing:DeleteTargetGroup",
            "elasticloadbalancing:DeleteListener",
            "elasticloadbalancing:DeleteRule",
            "elasticloadbalancing:AddTags",
            "elasticloadbalancing:RemoveTags",
            "elasticloadbalancing:ModifyLoadBalancerAttributes",
            "elasticloadbalancing:ModifyTargetGroup",
            "elasticloadbalancing:ModifyTargetGroupAttributes",
            "elasticloadbalancing:ModifyListener",
            "elasticloadbalancing:ModifyRule",
            "elasticloadbalancing:SetIpAddressType",
            "elasticloadbalancing:SetSecurityGroups",
            "elasticloadbalancing:SetSubnets",
            "elasticloadbalancing:RegisterTargets",
            "elasticloadbalancing:DeregisterTargets",
            "elasticloadbalancing:SetWebAcl",
          ],
          resources: ["*"],
        }),
        new iam.PolicyStatement({
          actions: ["iam:CreateServiceLinkedRole"],
          resources: ["*"],
          conditions: {
            StringEquals: {
              "iam:AWSServiceName": "elasticloadbalancing.amazonaws.com",
            },
          },
        }),
        new iam.PolicyStatement({
          actions: [
            "cognito-idp:DescribeUserPoolClient",
            "acm:ListCertificates",
            "acm:DescribeCertificate",
            "wafv2:GetWebACL",
            "wafv2:GetWebACLForResource",
            "wafv2:AssociateWebACL",
            "wafv2:DisassociateWebACL",
            "shield:GetSubscriptionState",
            "shield:DescribeProtection",
            "shield:CreateProtection",
            "shield:DeleteProtection",
          ],
          resources: ["*"],
        }),
      ],
    });

    lbControllerSa.role.attachInlinePolicy(lbControllerPolicy);

    const lbControllerChart = this.cluster.addHelmChart("LbController", {
      chart: "aws-load-balancer-controller",
      repository: "https://aws.github.io/eks-charts",
      namespace: "kube-system",
      release: "aws-load-balancer-controller",
      wait: true,
      values: {
        clusterName: this.cluster.clusterName,
        serviceAccount: {
          create: false,
          name: "aws-load-balancer-controller",
        },
        region: cdk.Stack.of(this).region,
        vpcId: props.vpc.vpcId,
      },
    });
    lbControllerChart.node.addDependency(lbControllerSa);

    // --- NGINX Ingress Controller (no Service — NLB is created in McpEksStack) ---
    const nginxIngressChart = this.cluster.addHelmChart("NginxIngress", {
      chart: "ingress-nginx",
      repository: "https://kubernetes.github.io/ingress-nginx",
      namespace: "ingress-nginx",
      release: "ingress-nginx",
      createNamespace: true,
      values: {
        controller: {
          // Disable the default LoadBalancer Service — the NLB is created
          // explicitly in McpEksStack with numeric targetPort to avoid
          // named-port resolution issues with the AWS LB Controller.
          service: {
            enabled: false,
          },
          config: {
            "use-forwarded-headers": "true",
          },
        },
      },
    });
    nginxIngressChart.node.addDependency(lbControllerChart);

    const suppressions = [
      {
        id: "AwsSolutions-IAM4",
        reason:
          "EKS managed policies are required by CDK EKS construct internals",
      },
      {
        id: "AwsSolutions-IAM5",
        reason:
          "Wildcard permissions required by CDK EKS construct internals and LB controller",
      },
      {
        id: "AwsSolutions-L1",
        reason:
          "Lambda runtime is managed by CDK EKS construct, cannot override",
      },
      {
        id: "AwsSolutions-SF1",
        reason:
          "Step Function is managed by CDK EKS construct, cannot override",
      },
      {
        id: "AwsSolutions-SF2",
        reason:
          "Step Function is managed by CDK EKS construct, cannot override",
      },
    ];
    NagSuppressions.addStackSuppressions(this, suppressions, true);
  }
}
