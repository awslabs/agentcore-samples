#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { LambdaStack } from '../lib/lambda-stack';

const app = new cdk.App();

// Get context values from CDK context or environment
const vpcId = app.node.tryGetContext('vpcId');
const dbEndpoint = app.node.tryGetContext('dbEndpoint');
const dbSecretArn = app.node.tryGetContext('dbSecretArn');
const s3BucketName = app.node.tryGetContext('s3BucketName');

if (!vpcId || !dbEndpoint || !dbSecretArn || !s3BucketName) {
  throw new Error('Missing required context: vpcId, dbEndpoint, dbSecretArn, s3BucketName');
}

new LambdaStack(app, 'RtpOverlayLambdaStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
  description: 'RTP Overlay Lambda Function and API Gateway',
  vpcId,
  dbEndpoint,
  dbSecretArn,
  s3BucketName,
});
