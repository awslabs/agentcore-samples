#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { InfrastructureStack } from '../lib/infrastructure-stack';

const app = new cdk.App();

// Deploy infrastructure stack
new InfrastructureStack(app, 'RtpOverlayStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
  description: 'RTP Overlay System - VPC, RDS PostgreSQL, and S3 infrastructure',
});

// Note: Lambda stack will be deployed separately after infrastructure is ready
// Run: ./deploy-lambda.sh