#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { CdkDataAnalystAssistantAgentcoreStrandsStack } from '../cdklib/cdk-data-analyst-assistant-agentcore-strands-stack';

const app = new cdk.App();
new CdkDataAnalystAssistantAgentcoreStrandsStack(app, 'CdkDataAnalystAssistantAgentcoreStrandsStack', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION },
});
