#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { GatewayStack } from '../lib/gateway-stack.mjs';

const app = new App();
new GatewayStack(app, 'SampleClaudeAgentcoreGateway', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? 'us-east-1',
  },
});
