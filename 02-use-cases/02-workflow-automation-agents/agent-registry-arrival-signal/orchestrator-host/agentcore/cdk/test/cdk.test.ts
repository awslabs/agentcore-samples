// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { AgentCoreStack } from '../lib/cdk-stack';

// The orchestrator execution-role grants for the Agent Registry and the
// DynamoDB idempotency table are not added by this TypeScript stack. They are
// owned by the Python infrastructure stack
// (agent_registry_sample/infrastructure/stack.py), which imports the
// orchestrator role by ARN and attaches them, because the orchestrator deploys
// before the infra table exists (its own stack cannot reference the
// table without a self-referential cycle).
//
// The assertions for those grants live in
// tests/test_infrastructure_stack.py:
//   - test_orchestrator_role_granted_runtime_dataplane_and_dedupe:
//       Search (registry level) + GetDiscoverableRegistryRecord (record level,
//       since there is no agent-registry:BatchGet... IAM action) +
//       PutItem/UpdateItem/GetItem scoped to the table ARN.
//   - test_orchestrator_role_has_no_invoke_or_controlplane_grant:
//       no bedrock-agentcore:InvokeAgentRuntime (A2A invoke is authorized by
//       each peer's resource-based policy, the consumer-group model) and
//       no control-plane action on the orchestrator role.

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
