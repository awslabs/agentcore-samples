#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { VpcFargateStack } from "../lib/vpc-fargate-stack";

const app = new cdk.App();
new VpcFargateStack(app, "VpcFargateStack", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
  description:
    "CDK stack for deploying a Fargate container in a VPC with AgentCore Runtime",
});
