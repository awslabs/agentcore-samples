#!/usr/bin/env node
// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
import * as cdk from "aws-cdk-lib/core";
import { CdkEntraIdStack } from "../infra/cdk-stack";

const app = new cdk.App();

// Stack name from context — allows multiple independent deployments
const stackName = app.node.tryGetContext("stackName") || "CdkStackIdeMcpEntraId";

new CdkEntraIdStack(app, stackName, {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
});
