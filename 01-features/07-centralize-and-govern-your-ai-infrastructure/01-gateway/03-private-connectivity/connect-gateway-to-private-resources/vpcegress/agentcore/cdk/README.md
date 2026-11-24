# AgentCore CDK app

This CDK app is managed by the AgentCore CLI. It deploys everything declared in `agentcore/agentcore.json` into AWS
through the `@aws/agentcore-cdk` constructs. It is two files:

- `bin/cdk.ts` — the entry point. It reads the project once (`readAgentCoreProject`), creates one stack per deployment
  target (`resolveTargetStacks`), and turns `agentcore.json` into the application's props (`transformAgentCoreJson`).
  Everything about how `agentcore.json` is interpreted lives in the library, so it changes with the library version, not
  with this file.
- `lib/cdk-stack.ts` — `AgentCoreStack`, which instantiates one `AgentCoreApplication`. This is the file you edit.

Harness settings are read from `harness.yaml`. Inline `systemPrompt` text overrides
the conventional `system-prompt.md` file in the harness directory.

## The CLI runs it for you

You normally do not run this app directly:

```bash
agentcore build    # synthesizes the CloudFormation templates into agentcore/cdk/cdk.out
agentcore deploy   # synthesizes, then deploys the stack for the selected target
agentcore status   # reports the resources agentcore.json declares
```

`npm run build` compiles the app, and `npx cdk synth` / `npx cdk diff` work from this directory too.

## Extending the stack

Add your own AWS resources in `lib/cdk-stack.ts` after the application and wire them to a runtime or harness through the
application's accessors. Runtimes and harnesses implement `iam.IGrantable`, so any AWS L2 grant accepts them, and they
expose `grantRead` / `grantWrite` / `grantReadWrite` for DynamoDB tables, S3 buckets and Secrets Manager secrets plus
`addEnvironmentVariable`:

```ts
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';

const orders = new dynamodb.Table(this, 'Orders', {
  partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
});
const checkout = this.application.runtime('checkout'); // or this.application.harness('support')
checkout.grantReadWrite(orders); // updates the runtime's execution role
checkout.addEnvironmentVariable('ORDERS_TABLE', orders.tableName);
orders.grantReadData(this.application.harness('support')); // any AWS L2 grant works too
```

Then run `agentcore deploy` again. An unknown name fails at synth and lists the names that exist.

If a runtime or harness is configured with an `executionRoleArn`, CDK cannot modify that imported role: every grant
emits a synth-time warning listing the permissions that were not attached, and the role must already carry them.

`agentcore status` reports only the resources `agentcore.json` declares; resources you add here are visible
through CloudFormation (`aws cloudformation describe-stack-resources`).
