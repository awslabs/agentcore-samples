#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { GRProcessingStack } from '../lib/gr-processing-stack';

const app = new cdk.App();

// Get RTP API URL from context or environment variable
const rtpApiUrl = app.node.tryGetContext('rtpApiUrl') || process.env.RTP_API_URL || 'https://api.rtp-overlay.com';

// Deploy GR processing stack
new GRProcessingStack(app, 'GRProcessingStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
  description: 'RTP Overlay GR Processing - Lambda, S3, and Bedrock integration',
  rtpApiUrl,
});
