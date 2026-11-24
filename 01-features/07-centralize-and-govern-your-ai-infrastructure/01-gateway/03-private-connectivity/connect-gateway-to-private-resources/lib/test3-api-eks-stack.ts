import * as cdk from "aws-cdk-lib/core";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as eks from "aws-cdk-lib/aws-eks";
import * as route53 from "aws-cdk-lib/aws-route53";
import { KubectlV31Layer } from "@aws-cdk/lambda-layer-kubectl-v31";
import { NagSuppressions } from "cdk-nag";
import { Construct } from "constructs";

export interface ApiEksStackProps extends cdk.StackProps {
	clusterName: string;
	kubectlRoleArn: string;
	kubectlSecurityGroupId: string;
	kubectlPrivateSubnetIds: string[];
	vpc: ec2.IVpc;
	certificateArn: string;
	/** FQDN covered by the public certificate, e.g. "internal.example.com" */
	privateDomain: string;
}

export class ApiEksStack extends cdk.Stack {
	constructor(scope: Construct, id: string, props: ApiEksStackProps) {
		super(scope, id, props);

		const cluster = eks.Cluster.fromClusterAttributes(this, "ImportedCluster", {
			clusterName: props.clusterName,
			kubectlRoleArn: props.kubectlRoleArn,
			kubectlSecurityGroupId: props.kubectlSecurityGroupId,
			kubectlPrivateSubnetIds: props.kubectlPrivateSubnetIds,
			vpc: props.vpc,
			kubectlLayer: new KubectlV31Layer(this, "KubectlLayer"),
		});

		// --- Kubernetes resources ---
		const namespace = cluster.addManifest("ApiNamespace", {
			apiVersion: "v1",
			kind: "Namespace",
			metadata: { name: "rest-api" },
		});

		const deployment = cluster.addManifest("ApiDeployment", {
			apiVersion: "apps/v1",
			kind: "Deployment",
			metadata: {
				name: "rest-api",
				namespace: "rest-api",
			},
			spec: {
				replicas: 1,
				selector: { matchLabels: { app: "rest-api" } },
				template: {
					metadata: { labels: { app: "rest-api" } },
					spec: {
						containers: [
							{
								name: "rest-api",
								image: "python:3.12-slim",
								command: [
									"sh",
									"-c",
									'pip install fastapi uvicorn && python -c "\n' +
										"from fastapi import FastAPI\n" +
										"app = FastAPI()\n" +
										"items = []\n" +
										"@app.get('/health')\n" +
										"def health():\n" +
										"    return {'status': 'ok'}\n" +
										"@app.get('/items')\n" +
										"def list_items():\n" +
										"    return items\n" +
										"@app.post('/items')\n" +
										"def create_item(item: dict):\n" +
										"    items.append(item)\n" +
										"    return item\n" +
										"import uvicorn\n" +
										"uvicorn.run(app, host='0.0.0.0', port=8080)\n" +
										'"',
								],
								ports: [{ containerPort: 8080 }],
							},
						],
					},
				},
			},
		});
		deployment.node.addDependency(namespace);

		// NLB created via Kubernetes Service type LoadBalancer with AWS annotations
		const privateSubnetIds = props.kubectlPrivateSubnetIds.join(",");
		const nlbService = cluster.addManifest("ApiNlbService", {
			apiVersion: "v1",
			kind: "Service",
			metadata: {
				name: "rest-api-nlb",
				namespace: "rest-api",
				annotations: {
					"service.beta.kubernetes.io/aws-load-balancer-type": "nlb",
					"service.beta.kubernetes.io/aws-load-balancer-scheme": "internal",
					"service.beta.kubernetes.io/aws-load-balancer-nlb-target-type": "ip",
					"service.beta.kubernetes.io/aws-load-balancer-ssl-cert":
						props.certificateArn,
					"service.beta.kubernetes.io/aws-load-balancer-ssl-ports": "443",
					"service.beta.kubernetes.io/aws-load-balancer-subnets":
						privateSubnetIds,
				},
			},
			spec: {
				type: "LoadBalancer",
				selector: { app: "rest-api" },
				ports: [
					{
						name: "https",
						port: 443,
						targetPort: 8080,
						protocol: "TCP",
					},
				],
			},
		});
		nlbService.node.addDependency(deployment);

		// Retain K8s manifests on stack deletion to avoid kubectl Lambda timeout.
		// The NLB deprovisioning can exceed Lambda's 15-min limit, causing cdk destroy to hang.
		// These resources are cleaned up when the EKS cluster is destroyed.
		for (const manifest of [namespace, deployment, nlbService]) {
			manifest.node.findAll().forEach((child) => {
				if (child instanceof cdk.CfnResource) {
					child.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);
				}
			});
		}

		// --- Route 53 Private Hosted Zone ---
		// Empty zone associated with the VPC. The notebook adds an Alias A
		// record pointing at the K8s-managed NLB once it's been provisioned
		// (NLB DNS isn't known at deploy time). AgentCore's Resource Gateway
		// uses Private DNS to resolve this domain — no routingDomain needed.
		const privateZone = new route53.PrivateHostedZone(this, "PrivateZone", {
			zoneName: props.privateDomain,
			vpc: props.vpc,
		});

		new cdk.CfnOutput(this, "PrivateDomain", {
			value: props.privateDomain,
			description:
				"Private domain — notebook adds an Alias A record to the NLB",
		});

		new cdk.CfnOutput(this, "PrivateZoneId", {
			value: privateZone.hostedZoneId,
			description:
				"Route 53 private hosted zone ID (used by the notebook to UPSERT the NLB alias record)",
		});

		NagSuppressions.addStackSuppressions(
			this,
			[
				{
					id: "AwsSolutions-IAM4",
					reason: "EKS kubectl provider uses CDK-managed policies",
				},
				{
					id: "AwsSolutions-IAM5",
					reason: "EKS kubectl provider uses CDK-managed wildcard permissions",
				},
				{
					id: "AwsSolutions-L1",
					reason: "Lambda runtime is managed by CDK EKS construct",
				},
			],
			true,
		);
	}
}
