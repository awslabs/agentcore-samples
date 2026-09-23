#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { ContentProviderStack } from "../lib/content-provider-stack";

const app = new cdk.App();

// Required CDK context values (pass via --context or cdk.json)
//   PAY_TO            Merchant wallet address (0x...)
//   PRICE_USDC_UNITS  Price in USDC atomic units, 6 decimals (default: 100000 = $0.10)
//   NETWORK           CAIP-2 network identifier (default: eip155:84532 = Base Sepolia)
//   USDC_ADDRESS      USDC contract address (default: Base Sepolia USDC)

new ContentProviderStack(app, "AgentCoreContentProvider", {
  env: {
    // Lambda@Edge must be deployed in us-east-1
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: "us-east-1",
  },
  description:
    "AgentCore Payments — x402 demo content provider (CloudFront + Lambda@Edge)",
});
