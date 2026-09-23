#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { EnterpriseMcpInfraStack } from '../lib/enterprise-mcp-infra-stack';

const app = new cdk.App();
new EnterpriseMcpInfraStack(app, 'EnterpriseMcpInfraStack', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'us-east-1' },
});
