import { defineBackend } from '@aws-amplify/backend';
import { auth } from './auth/resource';
import { Policy, PolicyStatement } from 'aws-cdk-lib/aws-iam';
import { Stack } from 'aws-cdk-lib';

export const backend = defineBackend({ auth });
const role = backend.auth.resources.authenticatedUserIamRole;
const stack = Stack.of(role);
const runtimeArn = process.env.AGENT_RUNTIME_ARN;
// First deploy auth alone, then bind the exact backend output. Never a wildcard fallback.
if (runtimeArn) {
  if (!/^arn:aws:bedrock-agentcore:[a-z0-9-]+:\d{12}:runtime\/[A-Za-z0-9_-]+$/.test(runtimeArn)) {
    throw new Error('AGENT_RUNTIME_ARN must be an exact Runtime ARN');
  }
  role.attachInlinePolicy(new Policy(stack, 'BedrockAgentCorePolicy', {
    statements: [new PolicyStatement({
      actions: ['bedrock-agentcore:InvokeAgentRuntime'],
      resources: [runtimeArn, `${runtimeArn}/runtime-endpoint/*`],
    })],
  }));
}
// No browser DynamoDB or Memory grants. Authenticated Runtime operations bind
// result keys and Memory namespaces to a verified Cognito subject.
const model = process.env.MODEL_ID_FOR_CHART || 'us.anthropic.claude-haiku-4-5-20251001-v1:0';
if (model !== 'us.anthropic.claude-haiku-4-5-20251001-v1:0') {
  throw new Error('Review chart model permissions before changing the model');
}
role.attachInlinePolicy(new Policy(stack, 'BedrockInvokeModelPolicy', {
  statements: [new PolicyStatement({
    actions: ['bedrock:InvokeModel'],
    resources: [
      `arn:aws:bedrock:${stack.region}:${stack.account}:inference-profile/${model}`,
      ...['us-east-1', 'us-east-2', 'us-west-2'].map(region =>
        `arn:aws:bedrock:${region}::foundation-model/${model.replace(/^us\./, '')}`),
    ],
  })],
}));
