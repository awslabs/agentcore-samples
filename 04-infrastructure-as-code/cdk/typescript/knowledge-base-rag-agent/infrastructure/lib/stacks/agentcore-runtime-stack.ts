import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as agentcore from '@aws-cdk/aws-bedrock-agentcore-alpha';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as path from 'path';

export interface AgentCoreRuntimeStackProps extends cdk.StackProps {
  userPool: cognito.IUserPool;
  userPoolClient: cognito.IUserPoolClient;
  modelId?: string;
}

export class AgentCoreRuntimeStack extends cdk.Stack {
  public readonly runtime: agentcore.Runtime;
  public readonly runtimeIdParameter: ssm.StringParameter;

  constructor(scope: Construct, id: string, props: AgentCoreRuntimeStackProps) {
    super(scope, id, props);

    const paramPrefix = '/AgentCoreTemplate';

    // Use cross-region inference profile for Claude Sonnet 4 (latest model)
    const modelId = props.modelId || 'us.anthropic.claude-sonnet-4-5-20250929-v1:0';

    // Get OpenSearch collection endpoint for RAG
    const collectionEndpoint = ssm.StringParameter.valueForStringParameter(
      this,
      `${paramPrefix}/OpenSearch/CollectionEndpoint`
    );

    const vectorIndexName = ssm.StringParameter.valueForStringParameter(
      this,
      `${paramPrefix}/OpenSearch/VectorIndexName`
    );

    // Get Memory ID for agent memory integration
    const memoryId = ssm.StringParameter.valueForStringParameter(
      this,
      `${paramPrefix}/AgentCore/MemoryId`
    );

    // Get Knowledge Base ID for RAG
    const knowledgeBaseId = ssm.StringParameter.valueForStringParameter(
      this,
      `${paramPrefix}/KnowledgeBaseId`
    );

    // Create AgentCore Runtime using local asset (Dockerfile)
    // The agent code is in infrastructure/agent directory
    const agentRuntimeArtifact = agentcore.AgentRuntimeArtifact.fromAsset(
      path.join(__dirname, '../../agent')
    );

    // Create the AgentCore Runtime
    this.runtime = new agentcore.Runtime(this, 'AgentRuntime', {
      runtimeName: 'knowledge_base_rag_agent',
      description: 'Knowledge Base RAG Agent - Conversational AI with RAG',
      agentRuntimeArtifact: agentRuntimeArtifact,
      authorizerConfiguration: agentcore.RuntimeAuthorizerConfiguration.usingCognito(
        props.userPool,
        [props.userPoolClient]
      ),
      // Environment variables for the agent
      environmentVariables: {
        MODEL_ID: modelId,
        OPENSEARCH_ENDPOINT: collectionEndpoint,
        OPENSEARCH_INDEX: vectorIndexName,
        KNOWLEDGE_BASE_ID: knowledgeBaseId,
        MEMORY_ID: memoryId,
        AWS_REGION: this.region,
      },
      // Lifecycle configuration
      lifecycleConfiguration: {
        idleRuntimeSessionTimeout: cdk.Duration.minutes(15),
        maxLifetime: cdk.Duration.hours(8),
      },
    });

    // Grant permissions to invoke Bedrock models
    // Use wildcard region for foundation models because cross-region inference
    // profiles can route requests to any region where the model is available
    this.runtime.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
        ],
        resources: [
          // Allow all regions for foundation models (cross-region inference)
          'arn:aws:bedrock:*::foundation-model/*',
          // Allow inference profiles in this account
          `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
        ],
      })
    );

    // Grant permissions to access OpenSearch Serverless
    this.runtime.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['aoss:APIAccessAll'],
        resources: [`arn:aws:aoss:${this.region}:${this.account}:collection/*`],
      })
    );

    // Grant permissions to use Bedrock Knowledge Base Retrieve API
    this.runtime.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:Retrieve',
          'bedrock:RetrieveAndGenerate',
        ],
        resources: [`arn:aws:bedrock:${this.region}:${this.account}:knowledge-base/*`],
      })
    );

    // Grant permissions to use AgentCore Memory
    this.runtime.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock-agentcore:CreateEvent',
          'bedrock-agentcore:GetEvent',
          'bedrock-agentcore:DeleteEvent',
          'bedrock-agentcore:ListEvents',
        ],
        resources: [`arn:aws:bedrock-agentcore:${this.region}:${this.account}:memory/*`],
      })
    );

    // Grant KMS permissions for Memory encryption
    this.runtime.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'kms:Encrypt',
          'kms:Decrypt',
          'kms:GenerateDataKey',
        ],
        resources: ['*'],
        conditions: {
          StringEquals: {
            'kms:ViaService': `bedrock-agentcore.${this.region}.amazonaws.com`,
          },
        },
      })
    );

    // Create the default endpoint
    this.runtime.addEndpoint('default', {
      version: '1',
      description: 'Default endpoint for Knowledge Base RAG Agent',
    });

    // Store runtime ID in SSM Parameter Store
    this.runtimeIdParameter = new ssm.StringParameter(this, 'RuntimeIdParameter', {
      parameterName: `${paramPrefix}/AgentCore/RuntimeId`,
      description: 'AgentCore Runtime ID for Knowledge Base RAG Agent',
      stringValue: this.runtime.agentRuntimeId,
    });

    // Store runtime ARN for invocation
    new ssm.StringParameter(this, 'RuntimeArnParameter', {
      parameterName: `${paramPrefix}/AgentCore/RuntimeArn`,
      description: 'AgentCore Runtime ARN for Knowledge Base RAG Agent',
      stringValue: this.runtime.agentRuntimeArn,
    });

    // Store endpoint name
    new ssm.StringParameter(this, 'RuntimeEndpointParameter', {
      parameterName: `${paramPrefix}/AgentCore/RuntimeEndpoint`,
      description: 'AgentCore Runtime Endpoint for Knowledge Base RAG Agent',
      stringValue: 'default',
    });

    // Store model ID
    new ssm.StringParameter(this, 'ModelIdParameter', {
      parameterName: `${paramPrefix}/ModelId`,
      description: 'Model ID for Knowledge Base RAG Agent',
      stringValue: modelId,
    });

    // Outputs
    new cdk.CfnOutput(this, 'RuntimeId', {
      value: this.runtime.agentRuntimeId,
      description: 'AgentCore Runtime ID',
      exportName: `${this.stackName}-RuntimeId`,
    });

    new cdk.CfnOutput(this, 'RuntimeArn', {
      value: this.runtime.agentRuntimeArn,
      description: 'AgentCore Runtime ARN',
      exportName: `${this.stackName}-RuntimeArn`,
    });

    new cdk.CfnOutput(this, 'ModelId', {
      value: modelId,
      description: 'Bedrock Model ID',
      exportName: `${this.stackName}-ModelId`,
    });
  }
}
