import { AgentCoreApplication } from '@aws/agentcore-cdk';
import { CfnResource, Stack } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';

/** Intersect the v1 generated role with only the operations this sample uses. */
export function boundRuntime(stack: Stack, application: AgentCoreApplication, providerArn: string, secretArn: string) {
  const runtime = application.runtime('claimsagent');
  const environment = application.runtimeEnvironments.get('claimsagent')!;
  const resource = (type: string) => {
    const matches = environment.node.findAll().filter(c => (c as CfnResource).cfnResourceType === type);
    if (matches.length !== 1) throw new Error(`Expected one ${type} for the Container runtime`);
    return matches[0] as CfnResource;
  };
  const repositoryArn = resource('AWS::ECR::Repository').getAtt('Arn').toString();
  const encryptionKeyArn = resource('AWS::KMS::Key').getAtt('Arn').toString();
  const base = `arn:${stack.partition}:bedrock-agentcore:${stack.region}:${stack.account}`;
  const logs = `arn:${stack.partition}:logs:${stack.region}:${stack.account}:log-group:/aws/bedrock-agentcore/runtimes/${runtime.runtimeName}*`;
  const memoryArn = application.memory('ClaimsAgentMemory').memoryArn;
  const allow = (actions: string[], resources: string[]) => new iam.PolicyStatement({ actions, resources });
  const boundary = new iam.ManagedPolicy(stack, 'ClaimsRuntimeBoundary', {
    description: 'Claims Runtime only: two models, own Memory/Identity credential/image/logs; no config mutation',
    statements: [
      allow(['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream', 'bedrock:CountTokens'], [
        `arn:${stack.partition}:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6`,
        `arn:${stack.partition}:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0`,
        `arn:${stack.partition}:bedrock:*:${stack.account}:inference-profile/global.anthropic.claude-sonnet-4-6`,
        `arn:${stack.partition}:bedrock:*:${stack.account}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0`,
      ]),
      allow(['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer', 'ecr:BatchCheckLayerAvailability'], [repositoryArn]),
      allow(['kms:Decrypt'], [encryptionKeyArn]),
      allow(['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:DescribeLogStreams', 'logs:PutLogEvents',
        'logs:GetLogEvents', 'logs:FilterLogEvents'], [logs, `${logs}:log-stream:*`]),
      allow(['logs:DescribeLogGroups'], [`arn:${stack.partition}:logs:${stack.region}:${stack.account}:log-group:*`]),
      // These service operations do not support resource-level IAM restrictions.
      allow(['ecr:GetAuthorizationToken', 'xray:PutTraceSegments', 'xray:PutTelemetryRecords',
        'xray:GetSamplingRules', 'xray:GetSamplingTargets'], ['*']),
      new iam.PolicyStatement({ actions: ['cloudwatch:PutMetricData'], resources: ['*'],
        conditions: { StringEquals: { 'cloudwatch:namespace': 'bedrock-agentcore' } } }),
      allow(['bedrock-agentcore:GetWorkloadAccessToken', 'bedrock-agentcore:GetWorkloadAccessTokenForJWT',
        'bedrock-agentcore:GetWorkloadAccessTokenForUserId'], [
        `${base}:workload-identity-directory/default`,
        // Service-generated workload ID is unavailable before the role exists.
        `${base}:workload-identity-directory/default/workload-identity/*`,
      ]),
      allow(['bedrock-agentcore:GetResourceOauth2Token'], [`${base}:token-vault/default`, providerArn]),
      allow(['secretsmanager:GetSecretValue'], [secretArn]),
      allow(['bedrock-agentcore:GetEvent', 'bedrock-agentcore:GetMemory', 'bedrock-agentcore:GetMemoryRecord',
        'bedrock-agentcore:ListActors', 'bedrock-agentcore:ListEvents', 'bedrock-agentcore:ListSessions',
        'bedrock-agentcore:CreateEvent', 'bedrock-agentcore:DeleteEvent', 'bedrock-agentcore:DeleteMemoryRecord',
        'bedrock-agentcore:ListMemoryRecords', 'bedrock-agentcore:RetrieveMemoryRecords'], [memoryArn]),
    ],
  });
  // Namespace conditions on the original Memory grants still apply (intersection).
  iam.PermissionsBoundary.of(runtime.role).apply(boundary);
}
