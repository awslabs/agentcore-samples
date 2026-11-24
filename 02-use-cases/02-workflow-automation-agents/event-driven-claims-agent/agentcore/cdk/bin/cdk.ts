#!/usr/bin/env node
import { App, Aspects } from 'aws-cdk-lib';
import { readAgentCoreProject, resolveTargetStacks, transformAgentCoreJson } from '@aws/agentcore-cdk';
import { AwsSolutionsChecks } from 'cdk-nag';
import { readFileSync } from 'fs';
import { join } from 'path';
import { AgentCoreStack } from '../lib/cdk-stack';

try {
  const project = readAgentCoreProject();
  const owned = JSON.parse(readFileSync(join(project.projectRoot, '.claims-v1.json'), 'utf8'));
  if (owned.version !== 1 || project.projectName !== owned.project || !/^Claims[a-z0-9]{8,12}$/.test(owned.project)) {
    throw new Error('Fresh v1 ownership configuration required; old stacks cannot be adopted');
  }
  if (project.targets.length !== 1 || project.targets[0].name !== owned.target ||
      project.targets[0].account !== owned.account || project.targets[0].region !== owned.region) {
    throw new Error('Deployment target differs from the ownership ledger');
  }
  if (!owned.discovery_url || !owned.client_id || !owned.provider_arn || typeof owned.notifications !== 'boolean') {
    throw new Error('Run the ownership-checked auth setup before synthesis');
  }
  const app = new App();
  for (const stack of resolveTargetStacks({ projectName: project.projectName, targets: project.targets,
    deployedState: project.deployedState })) {
    if (stack.credentials?.['cognito-gateway-m2m']?.credentialProviderArn !== owned.provider_arn ||
        stack.credentials?.['cognito-gateway-m2m']?.clientSecretArn !== owned.secret_arn) {
      throw new Error('Credential state differs from the ownership ledger');
    }
    new AgentCoreStack(app, stack.stackName, {
      env: stack.env, tags: { ...stack.tags, 'claims:owner': owned.owner }, description: stack.description,
      application: transformAgentCoreJson(project.agentCoreJson, {
        projectRoot: project.projectRoot, credentials: stack.credentials, target: stack.target,
      }),
      discoveryUrl: owned.discovery_url, clientId: owned.client_id, notifications: owned.notifications,
    });
  }
  if (process.env.CDK_NAG === 'true') Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
  app.synth();
} catch (error) {
  console.error('AgentCore CDK synthesis failed:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
}
