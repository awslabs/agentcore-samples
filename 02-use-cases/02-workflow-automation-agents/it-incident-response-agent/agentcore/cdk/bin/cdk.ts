#!/usr/bin/env node
import { AgentCoreStack } from '../lib/cdk-stack';
import { ConfigIO, type AwsDeploymentTarget } from '@aws/agentcore-cdk';
import { App, Aspects, type Environment } from 'aws-cdk-lib';
import { AwsSolutionsChecks, NagSuppressions } from 'cdk-nag';
import * as path from 'path';
import * as fs from 'fs';

function toEnvironment(target: AwsDeploymentTarget): Environment {
  return {
    account: target.account,
    region: target.region,
  };
}

function sanitize(name: string): string {
  return name.replace(/_/g, '-');
}

function toStackName(projectName: string, targetName: string): string {
  return `AgentCore-${sanitize(projectName)}-${sanitize(targetName)}`;
}

async function main() {
  // Config root is parent of cdk/ directory. The CLI sets process.cwd() to agentcore/cdk/.
  const configRoot = path.resolve(process.cwd(), '..');
  const configIO = new ConfigIO({ baseDir: configRoot });

  const spec = await configIO.readProjectSpec();
  const targets = await configIO.readAWSDeploymentTargets();

  // Extract MCP configuration from project spec.
  // Gateway fields are stored in agentcore.json but may not yet be on the
  // AgentCoreProjectSpec type from @aws/agentcore-cdk, so we read them
  // dynamically and cast the resulting object.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const specAny = spec as any;
  const mcpSpec = specAny.agentCoreGateways?.length
    ? {
        agentCoreGateways: specAny.agentCoreGateways,
        mcpRuntimeTools: specAny.mcpRuntimeTools,
        unassignedTargets: specAny.unassignedTargets,
      }
    : undefined;

  // Read deployed state for credential ARNs (populated by pre-deploy identity setup)
  let deployedState: Record<string, unknown> | undefined;
  try {
    deployedState = JSON.parse(fs.readFileSync(path.join(configRoot, '.cli', 'deployed-state.json'), 'utf8'));
  } catch {
    // Deployed state may not exist on first deploy
  }

  if (targets.length === 0) {
    throw new Error('No deployment targets configured. Please define targets in agentcore/aws-targets.json');
  }

  // Read harness configs for role creation.
  // Harness fields may not yet be on the AgentCoreProjectSpec type from @aws/agentcore-cdk,
  // so we read them dynamically via specAny (same pattern as gateways above).
  // Harness paths in agentcore.json are relative to the project root (parent of agentcore/).
  const projectRoot = path.resolve(configRoot, '..');
  const harnessConfigs: {
    name: string;
    executionRoleArn?: string;
    memoryName?: string;
    containerUri?: string;
    hasDockerfile?: boolean;
    dockerfileName?: string;
    harnessDir?: string;
    tools?: { type: string; name: string }[];
    apiKeyArn?: string;
  }[] = [];
  for (const entry of specAny.harnesses ?? []) {
    const harnessDir = path.resolve(projectRoot, entry.path);
    const harnessPath = path.resolve(harnessDir, 'harness.json');
    try {
      const harnessSpec = JSON.parse(fs.readFileSync(harnessPath, 'utf-8'));
      harnessConfigs.push({
        name: entry.name,
        executionRoleArn: harnessSpec.executionRoleArn,
        memoryName: harnessSpec.memory?.name,
        containerUri: harnessSpec.containerUri,
        hasDockerfile: !!harnessSpec.dockerfile,
        dockerfileName: harnessSpec.dockerfile,
        harnessDir,
        tools: harnessSpec.tools,
        apiKeyArn: harnessSpec.model?.apiKeyArn,
      });
    } catch (err) {
      throw new Error(
        `Could not read harness.json for "${entry.name}" at ${harnessPath}: ${err instanceof Error ? err.message : err}`
      );
    }
  }

  const app = new App();

  for (const target of targets) {
    const env = toEnvironment(target);
    const stackName = toStackName(spec.name, target.name);

    // Extract credentials from deployed state for this target
    const targetState = (deployedState as Record<string, unknown>)?.targets as
      | Record<string, Record<string, unknown>>
      | undefined;
    const targetResources = targetState?.[target.name]?.resources as Record<string, unknown> | undefined;
    const credentials = targetResources?.credentials as
      | Record<string, { credentialProviderArn: string; clientSecretArn?: string }>
      | undefined;

    const stack = new AgentCoreStack(app, stackName, {
      spec,
      mcpSpec,
      credentials,
      harnesses: harnessConfigs.length > 0 ? harnessConfigs : undefined,
      kbId: process.env.KB_ID || app.node.tryGetContext('kbId') || undefined,
      destroyOnDelete: (process.env.DESTROY_ON_DELETE ?? 'true').toLowerCase() === 'true',
      env,
      description: `AgentCore stack for ${spec.name} deployed to ${target.name} (${target.region})`,
      tags: {
        'agentcore:project-name': spec.name,
        'agentcore:target-name': target.name,
      },
    });

    // cdk-nag: AWS Solutions checks (report as warnings for sample flexibility)
    Aspects.of(stack).add(new AwsSolutionsChecks({ verbose: false }));
    NagSuppressions.addStackSuppressions(
      stack,
      [
        { id: 'AwsSolutions-IAM4', reason: 'AWS managed policies acceptable for sample' },
        { id: 'AwsSolutions-IAM5', reason: 'Wildcard resources used for sample simplicity; restrict in production' },
        { id: 'AwsSolutions-L1', reason: 'Python 3.11 Lambda runtime acceptable for sample' },
        { id: 'AwsSolutions-S1', reason: 'S3 access logging not required for sample' },
        { id: 'AwsSolutions-DDB3', reason: 'PITR enabled; backup not required for sample data' },
        { id: 'AwsSolutions-SNS2', reason: 'SNS encryption-in-transit only for sample' },
        { id: 'AwsSolutions-SNS3', reason: 'SNS topic-level KMS optional for sample' },
        { id: 'AwsSolutions-SQS3', reason: 'DLQ itself does not need a DLQ for sample' },
        { id: 'AwsSolutions-SQS4', reason: 'SQS KMS encryption optional for sample' },
        { id: 'AwsSolutions-CB4', reason: 'CodeBuild KMS optional for sample' },
        { id: 'AwsSolutions-CB5', reason: 'CodeBuild privileged mode needed for Docker builds' },
        { id: 'AwsSolutions-ECR1', reason: 'ECR image scan not blocking for sample' },
      ],
      true
    );
  }

  app.synth();
}

main().catch((error: unknown) => {
  console.error('AgentCore CDK synthesis failed:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
