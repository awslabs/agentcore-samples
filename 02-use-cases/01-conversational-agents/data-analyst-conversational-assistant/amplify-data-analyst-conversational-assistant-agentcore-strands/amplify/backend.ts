import { defineBackend } from '@aws-amplify/backend';
import { auth } from './auth/resource';
import { Policy, PolicyStatement, Effect } from 'aws-cdk-lib/aws-iam';
import { Stack } from 'aws-cdk-lib';

export const backend = defineBackend({
  auth,
});

// ─── Environment variables ──────────────────────────────────────────────────
// Read from the shell at CDK synth time.
// Local dev:   DYNAMODB_TABLE_NAME=... pnpm amplify sandbox
// Hosting:     Set in Amplify Console → Environment variables
const QUESTION_ANSWERS_TABLE_NAME    = process.env.QUESTION_ANSWERS_TABLE_NAME || '*';
const AGENT_RUNTIME_ARN              = process.env.AGENT_RUNTIME_ARN || '';
const MODEL_ID_FOR_CHART             = process.env.MODEL_ID_FOR_CHART || 'us.anthropic.claude-haiku-4-5-20251001-v1:0';

const authenticatedRole = backend.auth.resources.authenticatedUserIamRole;
const stack = Stack.of(authenticatedRole);
const region = stack.region;
const account = stack.account;

// ─── DynamoDB: read access to the query results table ───────────────────────
const dynamoTableArns: string[] = [];
if (QUESTION_ANSWERS_TABLE_NAME) {
  dynamoTableArns.push(`arn:aws:dynamodb:${region}:${account}:table/${QUESTION_ANSWERS_TABLE_NAME}`);
  dynamoTableArns.push(`arn:aws:dynamodb:${region}:${account}:table/${QUESTION_ANSWERS_TABLE_NAME}/index/*`);
}

authenticatedRole.attachInlinePolicy(
  new Policy(stack, 'DynamoDBReadPolicy', {
    statements: [
      new PolicyStatement({
        effect: Effect.ALLOW,
        actions: [
          'dynamodb:GetItem',
          'dynamodb:Query',
          'dynamodb:Scan',
          'dynamodb:DescribeTable',
          'dynamodb:BatchGetItem',
        ],
        resources: dynamoTableArns,
      }),
    ],
  })
);

// ─── Bedrock AgentCore: invoke the agent runtime ────────────────────────────
if (AGENT_RUNTIME_ARN) {
  authenticatedRole.attachInlinePolicy(
    new Policy(stack, 'BedrockAgentCorePolicy', {
      statements: [
        new PolicyStatement({
          effect: Effect.ALLOW,
          actions: [
            'bedrock-agentcore:InvokeAgentRuntime',
          ],
          resources: [
            AGENT_RUNTIME_ARN,
            `${AGENT_RUNTIME_ARN}/*`,
          ],
        }),
      ],
    })
  );
} else {
  // Fallback: grant access to all AgentCore runtimes in the account
  // Remove or scope this down for production
  authenticatedRole.attachInlinePolicy(
    new Policy(stack, 'BedrockAgentCorePolicy', {
      statements: [
        new PolicyStatement({
          effect: Effect.ALLOW,
          actions: [
            'bedrock-agentcore:InvokeAgentRuntime',
          ],
          resources: [
            `arn:aws:bedrock-agentcore:${region}:${account}:runtime/*`,
          ],
        }),
      ],
    })
  );
}

// ─── Bedrock AgentCore Memory: read long-term memory facts ──────────────────
authenticatedRole.attachInlinePolicy(
  new Policy(stack, 'BedrockAgentCoreMemoryPolicy', {
    statements: [
      new PolicyStatement({
        effect: Effect.ALLOW,
        actions: [
          'bedrock-agentcore:ListMemoryRecords',
          'bedrock-agentcore:RetrieveMemoryRecords',
        ],
        resources: ['*'],
      }),
    ],
  })
);

// ─── Bedrock: invoke model for chart generation ─────────────────────────────
// Cross-region inference profiles (us.anthropic.*) route requests to multiple
// regions. The policy must cover all regions the profile may use.
// Strip the "us." prefix to get the base model ID for foundation-model ARNs.
const baseModelId = MODEL_ID_FOR_CHART.replace(/^us\./, '');
const bedrockRegions = ['us-east-1', 'us-east-2', 'us-west-2'];

const bedrockResources: string[] = [];
for (const r of bedrockRegions) {
  // Inference profile ARN (cross-region)
  bedrockResources.push(`arn:aws:bedrock:${r}:${account}:inference-profile/${MODEL_ID_FOR_CHART}`);
  // Foundation model ARN (base model without us. prefix)
  bedrockResources.push(`arn:aws:bedrock:${r}::foundation-model/${baseModelId}`);
}

authenticatedRole.attachInlinePolicy(
  new Policy(stack, 'BedrockInvokeModelPolicy', {
    statements: [
      new PolicyStatement({
        effect: Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
        ],
        resources: bedrockResources,
      }),
    ],
  })
);
