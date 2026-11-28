#!/usr/bin/env node
import { AgentCoreStack } from '../lib/cdk-stack';
import { AgentCoreProjectSpecSchema, readAgentCoreProject, type AwsDeploymentTarget } from '@aws/agentcore-cdk';
import { App, Aspects, CfnRule, Fn, type Environment } from 'aws-cdk-lib';
import { AwsSolutionsChecks, NagSuppressions } from 'cdk-nag';
import * as fs from 'fs';
import * as path from 'path';


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
  const { agentCoreJson, targets, deployedState, projectRoot } = readAgentCoreProject();
  const spec = AgentCoreProjectSpecSchema.parse(agentCoreJson);

  if (targets.length !== 1) {
    throw new Error('This fresh-only example requires exactly one explicit deployment target');
  }
  const preview = process.env.IT_SAMPLE_SYNTH_ONLY === 'true';
  const ledgerPath = path.join(projectRoot, '.sample-deployment.json');
  const ledger = fs.existsSync(ledgerPath) ? JSON.parse(fs.readFileSync(ledgerPath, 'utf8')) : undefined;
  if (!preview && (!ledger?.stack_id || ledger.binding.project !== spec.name ||
      ledger.binding.target !== targets[0]!.name || ledger.binding.account !== targets[0]!.account ||
      ledger.binding.region !== targets[0]!.region || ledger.nonce !== process.env.IT_SAMPLE_OWNER ||
      process.env.IT_SAMPLE_TARGET !== targets[0]!.name)) {
    throw new Error('Use scripts/deploy.sh for a verified fresh reservation; IT_SAMPLE_SYNTH_ONLY=true is for offline template preview only');
  }

  const app = new App();

  for (const target of targets) {
    const env = toEnvironment(target);
    const stackName = toStackName(spec.name, target.name);

    // Extract credentials from deployed state for this target
    const credentials = deployedState?.targets[target.name]?.resources?.credentials;

    const stack = new AgentCoreStack(app, stackName, {
      spec,
      projectRoot,
      targetName: target.name,
      credentials,
      kbId: process.env.KB_ID || app.node.tryGetContext('kbId') || undefined,
      destroyOnDelete: (process.env.DESTROY_ON_DELETE ?? 'true').toLowerCase() === 'true',
      env,
      description: `AgentCore stack for ${spec.name} deployed to ${target.name} (${target.region})`,
      tags: {
        'agentcore:project-name': spec.name,
        'agentcore:target-name': target.name,
        'it-sample:owner': ledger?.nonce ?? 'offline-preview-not-deployable',
      },
    });

    if (preview) {
      new CfnRule(stack, 'OfflinePreviewOnly', { assertions: [{
        assert: Fn.conditionEquals('offline-preview', 'authorized-deployment'),
        assertDescription: 'This is a non-deployable offline preview. Use the fresh reservation entrypoint.',
      }] });
    }

    // cdk-nag runs with retained scaffold waivers below, not security acceptance.
    // Review generated policies and service requirements before cloud deployment.
    Aspects.of(stack).add(new AwsSolutionsChecks({ verbose: false }));
    NagSuppressions.addStackSuppressions(
      stack,
      [
        { id: 'AwsSolutions-IAM4', reason: 'Generated managed policies require separate effective-IAM review; not least-privilege acceptance' },
        { id: 'AwsSolutions-IAM5', reason: 'Wildcard resources used for sample simplicity; restrict in production' },
        { id: 'AwsSolutions-L1', reason: 'Review the explicitly selected Lambda runtime separately from this scaffold waiver' },
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
