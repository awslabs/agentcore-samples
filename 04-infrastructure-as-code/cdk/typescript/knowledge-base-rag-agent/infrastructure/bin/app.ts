#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import * as dotenv from 'dotenv';
import { AwsSolutionsChecks } from 'cdk-nag';

// Import all the modular stacks
import { SharedResourcesStack } from '../lib/stacks/shared-resources-stack';
import { NetworkStack } from '../lib/stacks/network-stack';
import { StorageStack } from '../lib/stacks/storage-stack';
import { CognitoStack } from '../lib/stacks/cognito-stack';
import { DatabaseStack } from '../lib/stacks/database-stack';
import { OpenSearchStack } from '../lib/stacks/opensearch-stack';
import { AgentCoreMemoryStack } from '../lib/stacks/agentcore-memory-stack';
import { AgentCoreRuntimeStack } from '../lib/stacks/agentcore-runtime-stack';
import { ApiStack } from '../lib/stacks/api-stack';
import { MonitoringStack } from '../lib/stacks/monitoring-stack';
import { WebConsoleStack } from '../lib/stacks/web-console-stack';

// Import nag suppressions
import {
  applyNagSuppressions,
  suppressS3Warnings,
  suppressCognitoWarnings,
  suppressApiGatewayWarnings,
  suppressCloudFrontWarnings,
  suppressVpcWarnings,
  suppressSnsWarnings,
} from '../lib/utils/nag-suppressions';

// Load environment variables
dotenv.config();

const app = new cdk.App();

// Add cdk-nag security checks (only when CDK_NAG_ENABLED=true)
if (process.env.CDK_NAG_ENABLED === 'true') {
  cdk.Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
}

// Environment configuration
const region = process.env.AWS_REGION || 'us-east-1';
const account = process.env.CDK_DEFAULT_ACCOUNT;
const env = { account, region };

// Application configuration from environment variables
const config = {
  // AgentCore settings
  agentCoreModelId: process.env.AGENTCORE_MODEL_ID || 'us.anthropic.claude-sonnet-4-5-20250929-v1:0',
  
  // Lambda settings
  lambdaTimeoutSeconds: parseInt(process.env.LAMBDA_TIMEOUT_SECONDS || '300'),
  lambdaMemoryMB: parseInt(process.env.LAMBDA_MEMORY_MB || '1024'),
  
  // Network settings
  vpcCidr: process.env.VPC_CIDR || '10.0.0.0/18',
  vpcMaxAzs: parseInt(process.env.VPC_MAX_AZS || '2'),
  
  // Monitoring settings
  alertEmail: process.env.ALERT_EMAIL || 'admin@example.com',
  enableMonitoring: process.env.ENABLE_MONITORING === 'true',
  
  // OpenSearch settings
  openSearchCollectionName: process.env.OPENSEARCH_COLLECTION_NAME || 'bedrock-agentcore-vectors',
  openSearchIndexName: process.env.OPENSEARCH_INDEX_NAME || 'bedrock-knowledge-base-index',
  
  // API settings
  corsEnabled: process.env.CORS_ENABLED === 'true',
  corsOrigins: process.env.CORS_ORIGINS?.split(',') || ['*'],
};

// ============================================================================
// Stack Deployment Order:
// 1. Foundation Layer (no dependencies)
// 2. Core Infrastructure Layer
// 3. AgentCore Layer (Memory, then Runtime)
// 4. API Layer
// 5. Integration Layer (Monitoring, Web Console)
// ============================================================================

// 0. Shared Resources Layer - No dependencies (deployed first)
const sharedResourcesStack = new SharedResourcesStack(app, 'AgentCoreSharedResourcesStack', {
  env,
  description: 'Shared resources for AgentCore Template',
});

// 1. Foundation Layer - No dependencies
const networkStack = new NetworkStack(app, 'AgentCoreNetworkStack', {
  env,
  description: 'Network infrastructure for AgentCore Template',
  cidr: config.vpcCidr,
  maxAzs: config.vpcMaxAzs,
});

const storageStack = new StorageStack(app, 'AgentCoreStorageStack', {
  env,
  description: 'Storage infrastructure for AgentCore Template',
});

// 2. Core Infrastructure Layer
const cognitoStack = new CognitoStack(app, 'AgentCoreCognitoStack', {
  env,
  description: 'User authentication for AgentCore Template',
});

const databaseStack = new DatabaseStack(app, 'AgentCoreDatabaseStack', {
  env,
  vpc: networkStack.vpc,
  description: 'Database infrastructure for AgentCore Template',
});

const openSearchStack = new OpenSearchStack(app, 'AgentCoreOpenSearchStack', {
  env,
  collectionName: config.openSearchCollectionName,
  vectorIndexName: config.openSearchIndexName,
  bedrockRoleName: 'AgentCoreKnowledgeBaseRole',
  description: 'Vector database for AgentCore Template',
});

// 3. AgentCore Layer
const agentCoreMemoryStack = new AgentCoreMemoryStack(app, 'AgentCoreMemoryStack', {
  env,
  description: 'AgentCore Memory for conversation context and long-term recall',
});

const agentCoreRuntimeStack = new AgentCoreRuntimeStack(app, 'AgentCoreRuntimeStack', {
  env,
  userPool: cognitoStack.userPool,
  userPoolClient: cognitoStack.userPoolClient,
  modelId: config.agentCoreModelId,
  description: 'AgentCore Runtime for AI agent execution',
});

// 4. API Layer
const apiStack = new ApiStack(app, 'AgentCoreApiStack', {
  env,
  vpc: networkStack.vpc,
  userPool: cognitoStack.userPool,
  corsEnabled: config.corsEnabled,
  corsOrigins: config.corsOrigins,
  description: 'API Gateway for AgentCore Template',
});

// 5. Integration Layer
const monitoringStack = new MonitoringStack(app, 'AgentCoreMonitoringStack', {
  env,
  alertEmail: config.alertEmail,
  description: 'Monitoring and alerting for AgentCore Template',
});

const webConsoleStack = new WebConsoleStack(app, 'AgentCoreWebConsoleStack', {
  env,
  userPoolId: cognitoStack.userPool.userPoolId,
  userPoolClientId: cognitoStack.userPoolClient.userPoolClientId,
  identityPoolId: cognitoStack.identityPool.ref,
  apiGatewayId: apiStack.api.restApiId,
  description: 'Web console for AgentCore Template',
});

// ============================================================================
// Stack Dependencies
// ============================================================================

// Foundation layer dependencies
databaseStack.addDependency(networkStack);

// OpenSearch depends on shared resources for the common layer
openSearchStack.addDependency(sharedResourcesStack);

// AgentCore Memory depends on storage (for KMS key)
agentCoreMemoryStack.addDependency(storageStack);

// AgentCore Runtime depends on:
// - Cognito (for authentication)
// - OpenSearch (for RAG)
// - Memory (for conversation context)
agentCoreRuntimeStack.addDependency(cognitoStack);
agentCoreRuntimeStack.addDependency(openSearchStack);
agentCoreRuntimeStack.addDependency(agentCoreMemoryStack);

// API layer dependencies
apiStack.addDependency(networkStack);
apiStack.addDependency(cognitoStack);
apiStack.addDependency(sharedResourcesStack);
apiStack.addDependency(agentCoreRuntimeStack);

// Integration layer dependencies
monitoringStack.addDependency(apiStack);
webConsoleStack.addDependency(apiStack);
webConsoleStack.addDependency(cognitoStack);

// ============================================================================
// Tags
// ============================================================================
cdk.Tags.of(app).add('Project', 'AgentCoreTemplate');
cdk.Tags.of(app).add('ManagedBy', 'CDK');
cdk.Tags.of(app).add('Architecture', 'AgentCore');

// ============================================================================
// CDK-NAG Suppressions (only when CDK_NAG_ENABLED=true)
// ============================================================================
if (process.env.CDK_NAG_ENABLED === 'true') {
  // Apply common suppressions to all stacks
  [
    sharedResourcesStack,
    networkStack,
    storageStack,
    cognitoStack,
    databaseStack,
    openSearchStack,
    agentCoreMemoryStack,
    agentCoreRuntimeStack,
    apiStack,
    monitoringStack,
    webConsoleStack,
  ].forEach((stack) => {
    applyNagSuppressions(stack);
  });

  // Apply specific suppressions
  suppressS3Warnings(storageStack);
  suppressS3Warnings(webConsoleStack);
  suppressCognitoWarnings(cognitoStack);
  suppressApiGatewayWarnings(apiStack);
  suppressCloudFrontWarnings(webConsoleStack);
  suppressVpcWarnings(networkStack);
  suppressSnsWarnings(monitoringStack);
}
