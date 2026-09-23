#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { PolicyStack } from "../lib/policy-stack";

const app = new cdk.App();

const account =
	(app.node.tryGetContext("account") as string | undefined) ??
	process.env.CDK_DEFAULT_ACCOUNT;
const region =
	(app.node.tryGetContext("region") as string | undefined) ??
	process.env.CDK_DEFAULT_REGION ??
	"us-east-1";

new PolicyStack(app, "LakehousePolicyStack", {
	env: {
		account,
		region,
	},
});
