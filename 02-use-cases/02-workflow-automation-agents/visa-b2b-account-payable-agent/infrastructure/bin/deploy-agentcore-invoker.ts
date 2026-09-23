#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { AgentCoreInvokerStack } from '../lib/agentcore-invoker-stack';

const app = new cdk.App();

// No VPC needed - Lambda only calls AWS APIs
new AgentCoreInvokerStack(app, 'AgentCoreInvokerStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
});
