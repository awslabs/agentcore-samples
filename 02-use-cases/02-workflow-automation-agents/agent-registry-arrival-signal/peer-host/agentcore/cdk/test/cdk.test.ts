// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { AgentCoreStack } from '../lib/cdk-stack';

test('AgentCoreStack synthesizes with empty spec', () => {
  const app = new cdk.App();
  const stack = new AgentCoreStack(app, 'TestStack', {
    spec: {
      name: 'testproject',
      version: 1,
      managedBy: 'CDK' as const,
      runtimes: [],
      memories: [],
      credentials: [],
      evaluators: [],
      onlineEvalConfigs: [],
      configBundles: [],
      policyEngines: [],
      payments: [],
      agentCoreGateways: [],
      mcpRuntimeTools: [],
      unassignedTargets: [],
      datasets: [],
      knowledgeBases: [],
    },
  });
  const template = Template.fromStack(stack);
  template.hasOutput('StackNameOutput', {
    Description: 'Name of the CloudFormation Stack',
  });
});

// U3 (peer-agent) no-op role pattern. The peer's execution role is the
// AgentCore CDK's auto-created baseline with nothing added, and no
// addToPrincipalPolicy for any registry action. This is the inverse of the
// orchestrator, whose registry and DynamoDB grants are
// attached by the infrastructure stack.
//
// These assertions confirm zero agent-registry actions exist anywhere on the
// peer stack's IAM policies.

function synthPeerStack(): Template {
  const app = new cdk.App();
  const stack = new AgentCoreStack(app, 'TestPeerStack', {
    spec: {
      name: 'testpeer',
      version: 1,
      managedBy: 'CDK' as const,
      runtimes: [
        {
          name: 'peer',
          build: 'CodeZip' as const,
          entrypoint: 'main.py',
          codeLocation: 'app/peer/',
          runtimeVersion: 'PYTHON_3_12' as const,
          networkMode: 'PUBLIC' as const,
          protocol: 'A2A' as const,
        },
      ],
      memories: [],
      credentials: [],
      evaluators: [],
      onlineEvalConfigs: [],
      configBundles: [],
      policyEngines: [],
      payments: [],
      agentCoreGateways: [],
      mcpRuntimeTools: [],
      unassignedTargets: [],
      datasets: [],
      knowledgeBases: [],
    } as any,
  });
  return Template.fromStack(stack);
}

function allActionsOnStack(template: Template): string[] {
  const policies = template.findResources('AWS::IAM::Policy');
  const actions: string[] = [];
  for (const resource of Object.values(policies)) {
    const statements = (resource as any).Properties.PolicyDocument.Statement as Array<{
      Action: string | string[];
    }>;
    for (const s of statements) {
      const a = Array.isArray(s.Action) ? s.Action : [s.Action];
      actions.push(...a);
    }
  }
  return actions;
}

test('peer synthesizes (the stock scaffold stack, used as-is, no edits)', () => {
  const template = synthPeerStack();
  template.hasOutput('StackNameOutput', {});
});

test('peer execution role grants ZERO agent-registry control-plane actions', () => {
  const template = synthPeerStack();
  const actions = allActionsOnStack(template);
  const controlPlane = actions.filter(a => typeof a === 'string' && a.startsWith('agent-registry:'));
  expect(controlPlane).toEqual([]);
});

test('peer execution role grants ZERO agent-registry data-plane actions', () => {
  const template = synthPeerStack();
  const actions = allActionsOnStack(template);
  const dataPlane = actions.filter(a => typeof a === 'string' && a.startsWith('agent-registry:'));
  expect(dataPlane).toEqual([]);
});

test('peer execution role has NO CreateRegistryRecord grant', () => {
  const template = synthPeerStack();
  const actions = allActionsOnStack(template);
  // The peer never registers itself.
  expect(actions).not.toContain('agent-registry:CreateRegistryRecord');
  expect(actions).not.toContain('agent-registry:BatchGetDiscoverableRegistryRecord');
  expect(actions).not.toContain('agent-registry:SearchDiscoverableRegistryRecords');
});
