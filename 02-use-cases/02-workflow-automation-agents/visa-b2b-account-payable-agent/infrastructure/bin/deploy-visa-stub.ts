#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { VisaStubStack } from '../lib/visa-stub-stack';

const app = new cdk.App();

new VisaStubStack(app, 'VisaStubStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
  description: 'Visa B2B Virtual Account Payment stub APIs for development and testing',
});
