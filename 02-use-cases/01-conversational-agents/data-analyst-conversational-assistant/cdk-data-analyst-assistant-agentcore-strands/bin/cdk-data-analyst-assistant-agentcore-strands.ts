#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { readAgentCoreProject, resolveTargetStacks, transformAgentCoreJson } from '@aws/agentcore-cdk';
import { resolve } from 'path';
import { assertFreshPortState } from '../cdklib/project-contract';
import { CdkDataAnalystAssistantAgentcoreStrandsStack } from '../cdklib/cdk-data-analyst-assistant-agentcore-strands-stack';

// agentcore/cdk is an alias of this custom app, not a second resource owner.
const root = resolve(__dirname, '../..');
const project = readAgentCoreProject(root);
assertFreshPortState(root, project.projectName, project.targets);
const app = new App();
for (const target of resolveTargetStacks({ projectName: project.projectName, targets: project.targets })) {
  if (target.target && process.env.DATA_ANALYST_FRESH_STACK !== target.stackName) {
    throw new Error(`Fresh stacks only: inventory existing deployments, then set DATA_ANALYST_FRESH_STACK=${target.stackName}. This is not proof the remote name is unused.`);
  }
  const application = transformAgentCoreJson(project.agentCoreJson, {
    projectRoot: project.projectRoot, target: target.target,
  });
  new CdkDataAnalystAssistantAgentcoreStrandsStack(app, target.stackName, {
    env: target.env, tags: target.tags, description: target.description, application,
  });
}
app.synth();
