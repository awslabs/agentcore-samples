import * as cdk from "aws-cdk-lib/core";
import { Platform } from "aws-cdk-lib/aws-ecr-assets";
import * as acm from "aws-cdk-lib/aws-certificatemanager";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as logs from "aws-cdk-lib/aws-logs";
import * as route53 from "aws-cdk-lib/aws-route53";
import * as route53targets from "aws-cdk-lib/aws-route53-targets";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as servicediscovery from "aws-cdk-lib/aws-servicediscovery";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";

export interface McpEcsStackProps extends cdk.StackProps {
	vpc: ec2.IVpc;
	certificateArn: string;
	/** FQDN covered by the public certificate, e.g. "internal.example.com" */
	privateDomain: string;
}

export class McpEcsStack extends cdk.Stack {
	public readonly albDnsName: string;

	constructor(scope: Construct, id: string, props: McpEcsStackProps) {
		super(scope, id, props);

		const certificate = acm.Certificate.fromCertificateArn(
			this,
			"AlbCert",
			props.certificateArn,
		);

		const cluster = new ecs.Cluster(this, "McpCluster", {
			vpc: props.vpc,
			containerInsightsV2: ecs.ContainerInsights.ENHANCED,
		});

		const namespace = new servicediscovery.PrivateDnsNamespace(
			this,
			"McpNamespace",
			{
				name: "mcp.local",
				vpc: props.vpc,
			},
		);

		const mcpImage = ecs.ContainerImage.fromAsset("docker/fastmcp-mock", {
			platform: Platform.LINUX_AMD64,
		});

		const serviceSg = new ec2.SecurityGroup(this, "McpServiceSg", {
			vpc: props.vpc,
			description: "MCP ECS Service - VPC only",
			allowAllOutbound: true,
		});
		serviceSg.addIngressRule(
			ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
			ec2.Port.tcp(8000),
			"Allow MCP traffic from VPC",
		);

		// --- Internal ALB with public cert ---
		const albSg = new ec2.SecurityGroup(this, "McpAlbSg", {
			vpc: props.vpc,
			description: "MCP ALB - HTTPS from VPC",
			allowAllOutbound: true,
		});
		albSg.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);
		albSg.addIngressRule(
			ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
			ec2.Port.tcp(443),
			"Allow HTTPS from VPC",
		);

		// Allow ALB to reach ECS tasks on ports 8000 and 8001
		serviceSg.addIngressRule(
			albSg,
			ec2.Port.tcp(8000),
			"Allow traffic from ALB to MCP server",
		);
		serviceSg.addIngressRule(
			albSg,
			ec2.Port.tcp(8001),
			"Allow traffic from ALB to Stock MCP server",
		);

		const accessLogBucket = new s3.Bucket(this, "McpAlbAccessLogs", {
			removalPolicy: cdk.RemovalPolicy.DESTROY,
			autoDeleteObjects: true,
			enforceSSL: true,
			encryption: s3.BucketEncryption.S3_MANAGED,
			lifecycleRules: [{ expiration: cdk.Duration.days(30) }],
		});

		const alb = new elbv2.ApplicationLoadBalancer(this, "McpAlb", {
			vpc: props.vpc,
			internetFacing: false,
			securityGroup: albSg,
			vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
		});

		alb.logAccessLogs(accessLogBucket, "alb-logs");

		// HTTPS listener with public cert
		const httpsListener = alb.addListener("HttpsListener", {
			port: 443,
			protocol: elbv2.ApplicationProtocol.HTTPS,
			certificates: [certificate],
		});

		// Fargate target group
		const fargateTargetGroup = new elbv2.ApplicationTargetGroup(
			this,
			"FargateTargetGroup",
			{
				vpc: props.vpc,
				port: 8000,
				protocol: elbv2.ApplicationProtocol.HTTP,
				targetType: elbv2.TargetType.IP,
				healthCheck: {
					path: "/health",
					port: "8000",
					healthyHttpCodes: "200,404,405",
				},
			},
		);

		httpsListener.addTargetGroups("DefaultRoute", {
			targetGroups: [fargateTargetGroup],
		});

		// --- Fargate Service ---
		const fargateTaskDef = new ecs.FargateTaskDefinition(
			this,
			"McpFargateTaskDef",
			{
				memoryLimitMiB: 512,
				cpu: 256,
			},
		);

		const fargateLogGroup = new logs.LogGroup(this, "McpFargateLogGroup", {
			retention: logs.RetentionDays.ONE_MONTH,
			removalPolicy: cdk.RemovalPolicy.DESTROY,
		});

		fargateTaskDef.addContainer("McpContainer", {
			image: mcpImage,
			portMappings: [{ containerPort: 8000 }],
			logging: ecs.LogDrivers.awsLogs({
				logGroup: fargateLogGroup,
				streamPrefix: "mcp-fargate",
			}),
		});

		const fargateService = new ecs.FargateService(this, "McpFargateService", {
			cluster,
			taskDefinition: fargateTaskDef,
			desiredCount: 1,
			vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
			securityGroups: [serviceSg],
			assignPublicIp: false,
			circuitBreaker: { enable: true, rollback: true },
			cloudMapOptions: {
				name: "mcp-fargate",
				cloudMapNamespace: namespace,
				dnsRecordType: servicediscovery.DnsRecordType.A,
			},
		});

		fargateService.attachToApplicationTargetGroup(fargateTargetGroup);

		// --- Stock MCP Server (second MCP server, same ALB, path-based routing) ---
		// The stock server mounts at /stock-mcp/ so ALB forwards /stock-mcp/* as-is.
		const stockImage = ecs.ContainerImage.fromAsset("docker/stock-mcp-mock", {
			platform: Platform.LINUX_AMD64,
		});

		const stockTargetGroup = new elbv2.ApplicationTargetGroup(
			this,
			"StockTargetGroup",
			{
				vpc: props.vpc,
				port: 8001,
				protocol: elbv2.ApplicationProtocol.HTTP,
				targetType: elbv2.TargetType.IP,
				healthCheck: {
					path: "/stock-mcp/",
					port: "8001",
					healthyHttpCodes: "200,404,405,406",
				},
			},
		);

		httpsListener.addTargetGroups("StockMcpRoute", {
			targetGroups: [stockTargetGroup],
			priority: 1,
			conditions: [elbv2.ListenerCondition.pathPatterns(["/stock-mcp/*"])],
		});

		const stockTaskDef = new ecs.FargateTaskDefinition(
			this,
			"StockMcpTaskDef",
			{
				memoryLimitMiB: 512,
				cpu: 256,
			},
		);

		const stockLogGroup = new logs.LogGroup(this, "StockMcpLogGroup", {
			retention: logs.RetentionDays.ONE_MONTH,
			removalPolicy: cdk.RemovalPolicy.DESTROY,
		});

		stockTaskDef.addContainer("StockMcpContainer", {
			image: stockImage,
			portMappings: [{ containerPort: 8001 }],
			logging: ecs.LogDrivers.awsLogs({
				logGroup: stockLogGroup,
				streamPrefix: "stock-mcp",
			}),
		});

		const stockService = new ecs.FargateService(
			this,
			"StockMcpFargateService",
			{
				cluster,
				taskDefinition: stockTaskDef,
				desiredCount: 1,
				vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
				securityGroups: [serviceSg],
				assignPublicIp: false,
				circuitBreaker: { enable: true, rollback: true },
				cloudMapOptions: {
					name: "stock-mcp",
					cloudMapNamespace: namespace,
					dnsRecordType: servicediscovery.DnsRecordType.A,
				},
			},
		);

		stockService.attachToApplicationTargetGroup(stockTargetGroup);

		// --- Bastion for SSM testing ---
		const bastionSg = new ec2.SecurityGroup(this, "BastionSg", {
			vpc: props.vpc,
			description: "Bastion - outbound only for SSM",
			allowAllOutbound: true,
		});

		const bastion = new ec2.Instance(this, "Bastion", {
			vpc: props.vpc,
			instanceType: ec2.InstanceType.of(
				ec2.InstanceClass.T3,
				ec2.InstanceSize.MICRO,
			),
			machineImage: ec2.MachineImage.latestAmazonLinux2023(),
			vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
			securityGroup: bastionSg,
			ssmSessionPermissions: true,
		});

		// Allow bastion to test ALB and MCP tasks
		albSg.addIngressRule(
			bastionSg,
			ec2.Port.tcp(443),
			"Allow bastion to test ALB HTTPS",
		);
		serviceSg.addIngressRule(
			bastionSg,
			ec2.Port.tcp(8000),
			"Allow bastion to test MCP directly",
		);
		serviceSg.addIngressRule(
			bastionSg,
			ec2.Port.tcp(8001),
			"Allow bastion to test Stock MCP directly",
		);

		// --- Route 53 Private Hosted Zone ---
		// Zone name matches the target FQDN. An apex Alias record points at
		// the ALB so `https://<privateDomain>` resolves to ALB private IPs
		// inside the VPC. AgentCore's Resource Gateway uses Private DNS to
		// resolve this domain — no routingDomain workaround needed.
		const privateZone = new route53.PrivateHostedZone(this, "PrivateZone", {
			zoneName: props.privateDomain,
			vpc: props.vpc,
		});

		new route53.ARecord(this, "AlbAliasRecord", {
			zone: privateZone,
			target: route53.RecordTarget.fromAlias(
				new route53targets.LoadBalancerTarget(alb),
			),
		});

		// --- Outputs ---
		new cdk.CfnOutput(this, "AlbDnsName", {
			value: alb.loadBalancerDnsName,
			description: "Internal ALB DNS (private IPs behind this DNS)",
		});

		new cdk.CfnOutput(this, "PrivateDomain", {
			value: props.privateDomain,
			description:
				"Private domain (Alias → ALB, resolvable via Private DNS inside the VPC)",
		});

		new cdk.CfnOutput(this, "AlbSgId", {
			value: albSg.securityGroupId,
		});

		new cdk.CfnOutput(this, "BastionInstanceId", {
			value: bastion.instanceId,
			description: "SSM Session Manager: aws ssm start-session --target <id>",
		});

		NagSuppressions.addStackSuppressions(this, [
			{
				id: "AwsSolutions-IAM5",
				reason:
					"ECS task execution role wildcards required for ECR image pulls and log writes",
			},
			{
				id: "AwsSolutions-ECS2",
				reason: "No secrets in environment variables",
			},
			{
				id: "AwsSolutions-IAM4",
				reason:
					"SSM managed policy required for Session Manager access on bastion",
			},
			{
				id: "AwsSolutions-EC26",
				reason: "EBS encryption not needed for bastion test instance",
			},
			{
				id: "AwsSolutions-EC28",
				reason: "Detailed monitoring not needed for bastion test instance",
			},
			{
				id: "AwsSolutions-EC29",
				reason:
					"Bastion is ephemeral test instance, no termination protection needed",
			},
			{
				id: "AwsSolutions-S1",
				reason: "Access log bucket does not need its own access logs",
			},
			{
				id: "AwsSolutions-EC23",
				reason: "ALB is internal, SG allows VPC CIDR only",
			},
		]);
	}
}
