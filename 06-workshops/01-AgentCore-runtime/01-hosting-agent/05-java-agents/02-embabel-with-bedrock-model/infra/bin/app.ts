#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { AgentCoreStack } from '../lib/agentcore-stack';

const app = new cdk.App();

const agentName = app.node.tryGetContext('agentName') || 'embabel-factchecker';
const imageTag = app.node.tryGetContext('imageTag') || 'latest';
const firstRun = app.node.tryGetContext('firstRun') === 'true';

new AgentCoreStack(app, 'AgentCoreStack', {
  agentName,
  imageTag,
  firstRun,
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
});
