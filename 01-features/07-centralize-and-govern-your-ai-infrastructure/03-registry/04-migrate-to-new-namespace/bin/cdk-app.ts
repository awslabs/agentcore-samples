#!/usr/bin/env node
import * as path from 'node:path';
import { App, Aspects, Environment, Tags } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import {
  accessResourcesForAccount,
  engineGlueRoleName,
  generatedAccessAccounts,
  generatedAccessRoleName,
  loadMigrationConfig,
  migrationExternalId,
  resolveRegistryMappings,
} from '../lib/config';
import { MigrationEngineStack } from '../lib/migration-engine-stack';
import { RegistryAccessStack } from '../lib/registry-access-stack';

const app = new App();
const configPath =
  app.node.tryGetContext('config') ??
  process.env.MIGRATION_CONFIG ??
  path.join(process.cwd(), 'config', 'migration.json');
const config = loadMigrationConfig(String(configPath));
const registryMappings = resolveRegistryMappings(config);
const engineEnv = environment(config.engine.account, config.engine.region);

const engineStack = new MigrationEngineStack(app, 'AgentRegistryMigrationEngine', {
  stackName: config.engine.stackName,
  env: engineEnv,
  terminationProtection: config.engine.terminationProtection,
  config,
  registryMappings,
  description: 'AWS Agent Registry preview-to-new-version migration engine',
});
Tags.of(engineStack).add('Application', 'AgentRegistryMigration');
Tags.of(engineStack).add('DeploymentId', config.engine.deploymentId);

// Cross-account access roles are only generated for remote endpoints that did not supply their
// own roleArn. When engine.createIamRoles is false, configuration validation requires an explicit
// roleArn on every remote endpoint, so this loop is empty and no IAM role is created anywhere.
for (const accountId of generatedAccessAccounts(config)) {
  if (!config.engine.account || !config.engine.region) {
    throw new Error('Engine account and region are required to provision cross-account access roles');
  }
  const resources = accessResourcesForAccount(config, accountId);
  const stackId = `RegistryAccess-${accountId}`;
  const accessStack = new RegistryAccessStack(app, stackId, {
    stackName: `${config.engine.stackName}-Access-${accountId}`,
    env: {
      account: accountId,
      region: config.engine.accessStackRegion ?? config.engine.region,
    },
    accessRoleName: generatedAccessRoleName(config),
    engineAccountId: config.engine.account,
    engineRoleArn: `arn:${config.engine.partition}:iam::${config.engine.account}:role/${engineGlueRoleName(config)}`,
    externalId: migrationExternalId(config),
    previewReadActions: config.iam.previewReadActions,
    previewRegistryResources: resources.source,
    targetWriteActions: config.iam.targetWriteActions,
    targetRegistryResources: resources.target,
    description: `Cross-account Agent Registry migration access for engine ${config.engine.deploymentId}`,
  });
  accessStack.addDependency(engineStack);
  Tags.of(accessStack).add('Application', 'AgentRegistryMigration');
  Tags.of(accessStack).add('DeploymentId', config.engine.deploymentId);
}

// Run the AWS Solutions security pack against every generated stack during synthesis. This adds
// policy-validation findings only; it does not change CloudFormation resources or deployment flow.
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));

app.synth();

function environment(account?: string, region?: string): Environment | undefined {
  if (!account && !region) {
    return undefined;
  }
  return { account, region };
}
