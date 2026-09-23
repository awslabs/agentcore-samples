# CDK app — Secure IDE Gateway Tool (Figma)

CDK TypeScript app that deploys the serverless OAuth proxy and AgentCore Gateway for this sample.
See [DEPLOYMENT.md](DEPLOYMENT.md) for the full deployment guide and [../README.md](../README.md)
for the architecture.

The `cdk.json` file tells the CDK Toolkit how to execute the app (`ts-node bin/cdk.ts`).

## Useful commands

* `pnpm install`      install dependencies
* `pnpm run build`    compile TypeScript to JS
* `pnpm run watch`    watch for changes and compile
* `pnpm cdk deploy`   deploy this stack to your default AWS account/region
* `pnpm cdk diff`     compare deployed stack with current state
* `pnpm cdk synth`    emit the synthesized CloudFormation template
