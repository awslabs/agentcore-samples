#!/usr/bin/env node
import { AgentCoreStack } from '../lib/cdk-stack';
import { ConfigIO, type AwsDeploymentTarget } from '@aws/agentcore-cdk';
import { App, type Environment } from 'aws-cdk-lib';
import * as path from 'path';

function toEnvironment(target: AwsDeploymentTarget): Environment {
  return { account: target.account, region: target.region };
}

function sanitize(name: string): string {
  return name.replace(/_/g, '-');
}

function toStackName(projectName: string, targetName: string): string {
  return `AgentCore-${sanitize(projectName)}-${sanitize(targetName)}`;
}

async function main() {
  // The CLI sets process.cwd() to agentcore/cdk/; config root is its parent.
  const configRoot = path.resolve(process.cwd(), '..');
  const configIO = new ConfigIO({ baseDir: configRoot });

  const spec = await configIO.readProjectSpec();
  const targets = await configIO.readAWSDeploymentTargets();

  // Gateway/MCP config lives alongside the spec in agentcore.json; the field may
  // not be on the AgentCoreProjectSpec type yet, so read it dynamically.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const specAny = spec as any;
  const mcpSpec = specAny.agentCoreGateways?.length
    ? { agentCoreGateways: specAny.agentCoreGateways }
    : undefined;

  if (targets.length === 0) {
    throw new Error('No deployment targets configured. Define targets in agentcore/aws-targets.json');
  }

  const app = new App();
  for (const target of targets) {
    new AgentCoreStack(app, toStackName(spec.name, target.name), {
      spec,
      mcpSpec,
      env: toEnvironment(target),
      description: `Receipts IDP AgentCore stack — ${target.name} (${target.region})`,
      tags: {
        'agentcore:project-name': spec.name,
        'agentcore:target-name': target.name,
      },
    });
  }
  app.synth();
}

main().catch((error: unknown) => {
  console.error('AgentCore CDK synthesis failed:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
