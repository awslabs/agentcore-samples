import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr_assets from "aws-cdk-lib/aws-ecr-assets";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as servicediscovery from "aws-cdk-lib/aws-servicediscovery";
import * as path from "path";

export class VpcFargateStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Parameters
    const agentName = new cdk.CfnParameter(this, "AgentName", {
      type: "String",
      default: "VpcFargateAgent",
      description: "Name for the agent runtime",
    });

    // Create VPC with public and private subnets
    const vpc = new ec2.Vpc(this, "AgentVpc", {
      vpcName: `${this.stackName}-vpc`,
      maxAzs: 2,
      ipAddresses: ec2.IpAddresses.cidr("10.0.0.0/16"),
      natGateways: 1,
      subnetConfiguration: [
        {
          name: "Public",
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
        {
          name: "Private",
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
          cidrMask: 24,
        },
      ],
    });

    // Security Group for the Fargate tasks
    const securityGroup = new ec2.SecurityGroup(this, "FargateSecurityGroup", {
      vpc,
      description: "Security group for Fargate containers",
      allowAllOutbound: true,
    });

    // Security Group for the Fargate tasks
    const securityGroupACR = new ec2.SecurityGroup(
      this,
      "AgentCoreSecurityGroup",
      {
        vpc,
        description: "Security group for Fargate containers",
        allowAllOutbound: true,
      }
    );

    // Allow inbound traffic on port 8080 within the VPC
    securityGroup.addIngressRule(
      ec2.Peer.securityGroupId(securityGroupACR.securityGroupId),
      ec2.Port.tcp(8080),
      "Allow inbound on port 8080 from ACR"
    );

    // Build and push Docker image using CDK
    const dockerImage = new ecr_assets.DockerImageAsset(
      this,
      "ResourceDockerImage",
      {
        directory: path.join(__dirname, "../resource-code"),
        platform: ecr_assets.Platform.LINUX_ARM64, // For Graviton/ARM64
      }
    );

    // Create ECS Cluster
    const cluster = new ecs.Cluster(this, "FargateCluster", {
      vpc,
      clusterName: `${this.stackName}-cluster`,
    });

    // Create Cloud Map namespace for service discovery
    const namespace = new servicediscovery.PrivateDnsNamespace(
      this,
      "ServiceDiscoveryNamespace",
      {
        name: "agentcore.local",
        vpc,
        description: "Private DNS namespace for AgentCore services",
      }
    );

    // Create CloudWatch Log Group
    const logGroup = new logs.LogGroup(this, "ServiceLogGroup", {
      logGroupName: `/ecs/${this.stackName}-service`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Create Task Execution Role
    const taskExecutionRole = new iam.Role(this, "TaskExecutionRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AmazonECSTaskExecutionRolePolicy"
        ),
      ],
    });

    // Grant ECR permissions to task execution role
    dockerImage.repository.grantPull(taskExecutionRole);

    // Create Task Role (for the container itself)
    const taskRole = new iam.Role(this, "TaskRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
    });

    // Create Fargate Task Definition
    const taskDefinition = new ecs.FargateTaskDefinition(
      this,
      "TaskDefinition",
      {
        memoryLimitMiB: 512,
        cpu: 256,
        runtimePlatform: {
          cpuArchitecture: ecs.CpuArchitecture.ARM64,
          operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
        },
        executionRole: taskExecutionRole,
        taskRole: taskRole,
      }
    );

    // Add container to task definition
    const container = taskDefinition.addContainer("AppContainer", {
      image: ecs.ContainerImage.fromDockerImageAsset(dockerImage),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: "echo",
        logGroup: logGroup,
      }),
      environment: {
        AWS_REGION: this.region,
      },
      portMappings: [
        {
          containerPort: 8080,
          protocol: ecs.Protocol.TCP,
        },
      ],
      healthCheck: {
        command: ["CMD-SHELL", "curl -f http://localhost:8080/ping || exit 1"],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(20),
      },
    });

    // Create Fargate Service with Cloud Map service discovery
    const fargateService = new ecs.FargateService(this, "FargateService", {
      cluster,
      taskDefinition,
      serviceName: `${this.stackName}-service`,
      desiredCount: 1,
      assignPublicIp: false,
      securityGroups: [securityGroup],
      capacityProviderStrategies: [
        { capacityProvider: "FARGATE_SPOT", weight: 100 },
      ],
      vpcSubnets: {
        subnets: vpc.privateSubnets,
      },
      cloudMapOptions: {
        name: "echo",
        cloudMapNamespace: namespace,
        dnsRecordType: servicediscovery.DnsRecordType.A,
        dnsTtl: cdk.Duration.seconds(30),
        containerPort: 8080,
      },
    });

    // Prepare subnet IDs for network configuration
    const privateSubnetIds = vpc.privateSubnets.map(
      (subnet) => subnet.subnetId
    );

    // Outputs
    new cdk.CfnOutput(this, "VpcId", {
      description: "ID of the created VPC",
      value: vpc.vpcId,
    });

    new cdk.CfnOutput(this, "Subnets", {
      description: "Private subnets",
      value: privateSubnetIds.join(","),
    });

    new cdk.CfnOutput(this, "SecurityGroupId", {
      description: "ID of the security group for AgentCore",
      value: securityGroupACR.securityGroupId,
    });

    new cdk.CfnOutput(this, "ServiceDiscoveryName", {
      description: "Service discovery DNS name",
      value: `echo.${namespace.namespaceName}`,
    });

    new cdk.CfnOutput(this, "CloudMapNamespaceId", {
      description: "Cloud Map namespace ID",
      value: namespace.namespaceId,
    });
  }
}
