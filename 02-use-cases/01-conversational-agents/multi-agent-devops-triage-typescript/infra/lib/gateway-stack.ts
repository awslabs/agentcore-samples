import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { CfnOutput, Duration, RemovalPolicy, Stack } from 'aws-cdk-lib';
import type { StackProps } from 'aws-cdk-lib';
import {
  Gateway,
  GatewayAuthorizer,
  SchemaDefinitionType,
  ToolSchema,
} from 'aws-cdk-lib/aws-bedrockagentcore';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import type { Construct } from 'constructs';

const here = path.dirname(fileURLToPath(import.meta.url));

/**
 * AgentCore Gateway + Lambda service-catalog target + supporting IAM/ECR.
 *
 * Inbound auth is IAM (AWS_IAM): callers SigV4-sign MCP requests with
 * service name `bedrock-agentcore` — exactly what the runbook worker's
 * in-process signing proxy produces.
 */
export class GatewayStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    // --- Lambda target: the mock service catalog -------------------------
    const catalogFn = new lambda.Function(this, 'ServiceCatalogFn', {
      runtime: lambda.Runtime.NODEJS_22_X,
      architecture: lambda.Architecture.ARM_64,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(here, '..', 'lambda', 'service-catalog')),
      timeout: Duration.seconds(10),
      description: 'Mock service catalog / runbook lookup (AgentCore Gateway target)',
    });

    // --- Gateway with IAM inbound auth -----------------------------------
    const gateway = new Gateway(this, 'ServiceCatalogGateway', {
      gatewayName: 'sample-service-catalog',
      description: 'Sample gateway exposing the mock service catalog over MCP',
      authorizerConfiguration: GatewayAuthorizer.usingAwsIam(),
    });

    // The target grants the gateway's role invoke on the Lambda and uses the
    // gateway's IAM role for outbound credentials by default.
    gateway.addLambdaTarget('ServiceCatalogTarget', {
      gatewayTargetName: 'catalog',
      description: 'Mock service catalog Lambda',
      lambdaFunction: catalogFn,
      toolSchema: ToolSchema.fromInline([
        {
          name: 'lookup_service',
          description:
            'Look up a service in the service catalog: owning team, escalation contact, tier, and dependencies. Known services: orders-api, payments-svc, inventory-svc.',
          inputSchema: {
            type: SchemaDefinitionType.OBJECT,
            properties: {
              service: {
                type: SchemaDefinitionType.STRING,
                description: 'Service name, e.g. "orders-api"',
              },
            },
            required: ['service'],
          },
        },
        {
          name: 'get_runbook',
          description:
            'Get the runbook steps for a service and symptom (e.g. "latency", "errors").',
          inputSchema: {
            type: SchemaDefinitionType.OBJECT,
            properties: {
              service: {
                type: SchemaDefinitionType.STRING,
                description: 'Service name, e.g. "orders-api"',
              },
              symptom: {
                type: SchemaDefinitionType.STRING,
                description: 'Observed symptom, e.g. "latency spike"',
              },
            },
            required: ['service', 'symptom'],
          },
        },
      ]),
    });

    // --- ECR repo for the shared agent image ------------------------------
    const repo = new ecr.Repository(this, 'AgentImageRepo', {
      repositoryName: 'sample-claude-agentcore-agents',
      removalPolicy: RemovalPolicy.DESTROY,
      emptyOnDelete: true,
    });

    // --- Runtime execution role (shared by the three agent runtimes) ------
    const runtimeRole = new iam.Role(this, 'RuntimeExecutionRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: 'Execution role for AgentCore Runtimes (Bedrock models + ECR pull + logs)',
    });
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockModelAccess',
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        resources: ['*'],
      }),
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'Logs',
        actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
        resources: ['*'],
      }),
    );
    gateway.grantInvoke(runtimeRole);
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'WorkerInvoke',
        // The lead runtime calls the worker runtimes over InvokeAgentRuntime;
        // A2A discovery fetches each worker's card via GetAgentCard (the
        // /invocations/.well-known/agent-card.json path maps to that action).
        actions: ['bedrock-agentcore:InvokeAgentRuntime', 'bedrock-agentcore:GetAgentCard'],
        resources: [`arn:aws:bedrock-agentcore:${this.region}:${this.account}:runtime/*`],
      }),
    );
    repo.grantPull(runtimeRole);
    // ECR GetAuthorizationToken is account-scoped
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'EcrAuth',
        actions: ['ecr:GetAuthorizationToken'],
        resources: ['*'],
      }),
    );

    new CfnOutput(this, 'GatewayUrl', { value: gateway.gatewayUrl ?? '' });
    new CfnOutput(this, 'GatewayArn', { value: gateway.gatewayArn });
    new CfnOutput(this, 'AgentImageRepoUri', { value: repo.repositoryUri });
    new CfnOutput(this, 'RuntimeExecutionRoleArn', { value: runtimeRole.roleArn });
  }
}
