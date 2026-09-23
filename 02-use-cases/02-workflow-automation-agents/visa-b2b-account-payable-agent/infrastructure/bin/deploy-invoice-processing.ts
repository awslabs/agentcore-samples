#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { InvoiceProcessingStack } from '../lib/invoice-processing-stack';

const app = new cdk.App();

// Get RTP API URL from context or environment variable
const rtpApiUrl = app.node.tryGetContext('rtpApiUrl') || process.env.RTP_API_URL || 'https://api.rtp-overlay.com';

// Deploy invoice processing stack
new InvoiceProcessingStack(app, 'InvoiceProcessingStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-west-2',
  },
  description: 'RTP Overlay Invoice Processing - Lambda, S3, and Bedrock integration',
  rtpApiUrl,
});
