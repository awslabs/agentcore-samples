import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { AgentCoreProjectSpecSchema, ToolDefinitionSchema } from '@aws/agentcore-cdk';
import * as fs from 'fs';
import * as path from 'path';
import { execFileSync } from 'child_process';
import { AgentCoreStack, type AgentCoreStackProps } from '../lib/cdk-stack';

const projectRoot = path.resolve(__dirname, '../../..');
const loadSpec = () => AgentCoreProjectSpecSchema.parse(
  JSON.parse(fs.readFileSync(path.join(projectRoot, 'agentcore/agentcore.json'), 'utf8'))
);

function synth(spec = loadSpec(), kbId?: string, credentials?: AgentCoreStackProps['credentials']) {
  const app = new cdk.App();
  const stack = new AgentCoreStack(app, 'AgentCore-ITIncidentAgent-dev', {
    spec, projectRoot, targetName: 'dev', kbId, credentials,
    env: { account: '111111111111', region: 'us-west-2' },
  });
  return Template.fromStack(stack);
}

beforeEach(() => {
  delete process.env.JIRA_OAUTH_CLIENT_ID;
  delete process.env.GATEWAY_AUTH_MODE;
  delete process.env.SKIP_KB;
  delete process.env.JIRA_OAUTH_CLIENT_SECRET;
  delete process.env.JIRA_RUNTIME_USER_ID;
  delete process.env.JIRA_CLOUD_ID;
  delete process.env.JIRA_SITE_URL;
  delete process.env.JIRA_ALLOWED_TOOLS;
  delete process.env.JIRA_OAUTH_SECRET_ARN;
  delete process.env.JIRA_OAUTH_SECRET_VERSION_ID;
  delete process.env.TXN_SEARCH_ALLOW_MUTATION;
  delete process.env.TXN_SEARCH_ACCOUNT_OWNER_APPROVAL;
});

test('Gateway asset schemas validate with the pinned tool schema and preserve names', () => {
  for (const target of loadSpec().agentCoreGateways[0]!.targets) {
    const filename = target.lambdaFunctionArn!.toolSchemaFile;
    const tools = JSON.parse(fs.readFileSync(path.join(projectRoot, filename), 'utf8'));
    expect(tools).toHaveLength(1);
    const tool = ToolDefinitionSchema.parse(tools[0]);
    expect(tool.name).toBe(target.name.replace(/-/g, '_'));
    expect(tool.inputSchema.type).toBe('object');
    expect(ToolDefinitionSchema.safeParse({ ...tools[0], inputSchema: { type: 'invalid' } }).success).toBe(false);
  }
});

test('full scenario validates and synthesizes without AWS', () => {
  const spec = loadSpec();
  const template = synth(spec);
  for (const [type, count] of Object.entries({
    'AWS::BedrockAgentCore::Runtime': 1,
    'AWS::BedrockAgentCore::Memory': 1,
    'AWS::BedrockAgentCore::Gateway': 1,
    'AWS::BedrockAgentCore::GatewayTarget': 4,
    'AWS::BedrockAgentCore::PolicyEngine': 1,
    'AWS::BedrockAgentCore::Policy': 2,
    'AWS::BedrockAgentCore::OnlineEvaluationConfig': 1,
    'AWS::DynamoDB::Table': 4,
    'AWS::Bedrock::KnowledgeBase': 1,
    'AWS::S3Vectors::VectorBucket': 1,
    'AWS::S3Vectors::Index': 1,
    'AWS::Bedrock::Guardrail': 1,
  })) template.resourceCountIs(type, count);
  const resources = template.toJSON().Resources;
  const runtime: any = Object.values(resources).find((r: any) => r.Type === 'AWS::BedrockAgentCore::Runtime');
  expect(runtime.Properties.AgentRuntimeName).toBe('ITIncidentAgent_ITIncidentAgent');
  expect(runtime.Properties.EnvironmentVariables).toEqual(expect.objectContaining({
    GATEWAY_URL: expect.anything(), MEMORY_ITINCIDENTAGENTMEMORY_ID: expect.anything(),
    GUARDRAIL_ID: expect.anything(), TICKETS_TABLE: expect.anything(), EVENT_BUS_NAME: expect.anything(),
    AGENT_MODEL_ID: 'us.anthropic.claude-sonnet-4-6',
    FAST_MODEL_ID: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
  }));
  const gateway: any = Object.values(resources).find((r: any) => r.Type === 'AWS::BedrockAgentCore::Gateway');
  expect(gateway.Properties.PolicyEngineConfiguration).toEqual(expect.objectContaining({ Mode: 'LOG_ONLY' }));
  expect(JSON.stringify(resources)).not.toContain('PLACEHOLDER_');
  expect(spec.agentCoreGateways[0]!.targets[0]!.lambdaFunctionArn!.lambdaArn).toBe('PLACEHOLDER_LOOKUP_USER');
  expect(JSON.stringify(resources)).toContain('incidents/{actorId}/{sessionId}');
  expect(JSON.stringify(resources)).toContain('Builtin.ToolSelectionAccuracy');
  expect(JSON.stringify(resources)).toContain('dynamodb:UpdateItem');
  expect(JSON.stringify(resources)).toContain('bedrock-agentcore:InvokeAgentRuntime');
  expect(JSON.stringify(resources)).toContain('infra.transaction_search.handler');
  const policies: any[] = Object.values(resources).filter((r: any) => r.Type === 'AWS::IAM::Policy');
  const additional = policies.find(r => r.Properties.PolicyName === 'AgentAdditionalPermissions');
  expect(additional.Properties.Roles).toEqual([expect.objectContaining({ Ref: expect.stringContaining('RuntimeExecutionRole') })]);
  expect(additional.Properties.PolicyDocument.Statement).toEqual(expect.arrayContaining([
    expect.objectContaining({ Sid: 'DynamoDBTicketsAccess', Resource: expect.any(Object) }),
    expect.objectContaining({ Sid: 'EventBridgeAccess', Resource: expect.any(Object) }),
  ]));
  const targets: any[] = Object.values(resources).filter((r: any) => r.Type === 'AWS::BedrockAgentCore::GatewayTarget');
  for (const target of targets) {
    expect(target.DependsOn.some((id: string) => id.includes('Gateway') && id.includes('DefaultPolicy'))).toBe(true);
    expect(target.Properties.TargetConfiguration.Mcp.Lambda.LambdaArn['Fn::GetAtt'][0]).toMatch(/^Infra/);
    expect(target.Properties.TargetConfiguration.Mcp.Lambda.ToolSchema.S3.Uri).toMatch(/^s3:/);
  }
  const evaluation: any = Object.values(resources).find((r: any) => r.Type === 'AWS::BedrockAgentCore::OnlineEvaluationConfig');
  expect(evaluation.DependsOn.some((id: string) => id.startsWith('EnableTransactionSearch'))).toBe(true);
});

test('KB disabled omits only query-kb and keeps evaluation/policy', () => {
  process.env.SKIP_KB = 'true';
  const template = synth();
  template.resourceCountIs('AWS::BedrockAgentCore::GatewayTarget', 3);
  template.resourceCountIs('AWS::Bedrock::KnowledgeBase', 0);
  template.resourceCountIs('AWS::BedrockAgentCore::OnlineEvaluationConfig', 1);
  template.resourceCountIs('AWS::BedrockAgentCore::Policy', 2);
});

test('existing KB keeps query-kb without creating a KB', () => {
  const template = synth(loadSpec(), 'KB12345678');
  template.resourceCountIs('AWS::BedrockAgentCore::GatewayTarget', 4);
  template.resourceCountIs('AWS::Bedrock::KnowledgeBase', 0);
});

test('unknown placeholder fails rather than silently deleting a tool', () => {
  const spec = loadSpec();
  spec.agentCoreGateways[0]!.targets[0]!.name = 'misspelled-tool';
  expect(() => synth(spec)).toThrow('Unexpected placeholder for misspelled-tool');
});

test('validation rejects v0 and broken policy/evaluation references', () => {
  expect(AgentCoreProjectSpecSchema.safeParse({ ...loadSpec(), version: 1 }).success).toBe(false);
  const spec = loadSpec();
  spec.onlineEvalConfigs[0]!.agent = 'missing';
  expect(AgentCoreProjectSpecSchema.safeParse(spec).success).toBe(false);
  const badPolicy = loadSpec();
  badPolicy.agentCoreGateways[0]!.policyEngineConfiguration!.policyEngineName = 'missing';
  // The CDK schema accepts this shape; the application resolves the reference.
  expect(() => synth(badPolicy)).toThrow(/missing.*not defined/);
});

test('CUSTOM_JWT keeps semantic search, policy and runtime auth in sync', () => {
  const spec = loadSpec();
  spec.agentCoreGateways[0]!.authorizerType = 'CUSTOM_JWT';
  spec.agentCoreGateways[0]!.authorizerConfiguration = { customJwtAuthorizer: {
    discoveryUrl: 'https://example.invalid/.well-known/openid-configuration',
    allowedAudience: ['test-audience'], allowedClients: ['test-client'], allowedScopes: ['resolve:tickets'],
  } };
  spec.runtimes[0]!.envVars!.push(
    { name: 'GATEWAY_AUTH_MODE', value: 'CUSTOM_JWT' },
    { name: 'GATEWAY_OAUTH_PROVIDER_NAME', value: 'ITIncidentAgent-gateway-m2m' },
    { name: 'GATEWAY_OAUTH_AUDIENCE', value: 'test-audience' },
    { name: 'GATEWAY_OAUTH_SCOPES', value: 'resolve:tickets' },
  );
  spec.credentials.push({ name: 'ITIncidentAgent-gateway-m2m', authorizerType: 'OAuthCredentialProvider',
    vendor: 'CustomOauth2', discoveryUrl: 'https://example.invalid/.well-known/openid-configuration', scopes: ['resolve:tickets'] });
  const providerArn = 'arn:aws:bedrock-agentcore:us-west-2:111111111111:token-vault/default/oauth2credentialprovider/ITIncidentAgent-gateway-m2m';
  const secretArn = 'arn:aws:secretsmanager:us-west-2:111111111111:secret:bedrock-agentcore-identity!fixture-abcdef';
  const credentials = { 'ITIncidentAgent-gateway-m2m': { credentialProviderArn: providerArn, clientSecretArn: secretArn } };
  const resources = synth(spec, undefined, credentials).toJSON().Resources;
  const runtime: any = Object.values(resources).find((r: any) => r.Type === 'AWS::BedrockAgentCore::Runtime');
  expect(runtime.Properties.EnvironmentVariables.GATEWAY_AUTH_MODE).toBe('CUSTOM_JWT');
  const policies = Object.entries(resources).filter(([id, r]: [string, any]) => r.Type === 'AWS::IAM::Policy' && id.includes('Runtime'));
  expect(JSON.stringify(policies)).toContain(providerArn);
  expect(JSON.stringify(policies)).toContain(secretArn);
  expect(JSON.stringify(policies)).not.toContain('bedrock-agentcore-identity!*');
  expect(JSON.stringify(policies)).not.toContain('CreateWorkloadIdentity');
  expect(runtime.Properties.EnvironmentVariables.GATEWAY_OAUTH_AUDIENCE).toBe('test-audience');
  const gateway: any = Object.values(resources).find((r: any) => r.Type === 'AWS::BedrockAgentCore::Gateway');
  expect(gateway.Properties.AuthorizerType).toBe('CUSTOM_JWT');
  expect(gateway.Properties.AuthorizerConfiguration.CustomJWTAuthorizer.AllowedScopes).toEqual(['resolve:tickets']);
  expect(gateway.Properties.ProtocolConfiguration.Mcp.SearchType).toBe('SEMANTIC');
  expect(gateway.Properties.PolicyEngineConfiguration.Mode).toBe('LOG_ONLY');
  spec.runtimes[0]!.envVars!.find(v => v.name === 'GATEWAY_OAUTH_SCOPES')!.value = '';
  expect(() => synth(spec, undefined, credentials)).toThrow(/allowedScopes/);
});

test('Jira external secret/version rotation never embeds plaintext', () => {
  process.env.JIRA_OAUTH_CLIENT_ID = 'fake-client';
  process.env.JIRA_RUNTIME_USER_ID = 'U-1001';
  process.env.JIRA_CLOUD_ID = '11111111-1111-1111-1111-111111111111';
  process.env.JIRA_SITE_URL = 'https://offline.atlassian.net';
  process.env.JIRA_ALLOWED_TOOLS = 'jira_readIssue jira_addComment jira_transitionIssue';
  process.env.JIRA_OAUTH_SECRET_ARN = 'arn:aws:secretsmanager:us-west-2:111111111111:secret:external-jira-abcdef';
  process.env.JIRA_OAUTH_SECRET_VERSION_ID = 'a'.repeat(32);
  const first = synth().toJSON();
  expect(JSON.stringify(first)).toContain('infra.jira_oauth_provider.handler');
  expect(JSON.stringify(first)).toContain('AtlassianOauth2');
  expect(Object.values(first.Resources).some((r: any) => r.Type === 'AWS::SecretsManager::Secret')).toBe(false);
  const resource: any = Object.values(first.Resources).find((r: any) => r.Properties?.ProviderName);
  expect(resource.Properties.SecretVersionId).toEqual({ Ref: 'JiraSecretVersionId' });
  process.env.JIRA_OAUTH_SECRET_VERSION_ID = 'b'.repeat(32);
  const rotated = synth().toJSON();
  expect(rotated.Parameters.JiraSecretVersionId.Default).not.toBe(first.Parameters.JiraSecretVersionId.Default);
  expect(rotated.Resources).toEqual(first.Resources);
  const policies: any[] = Object.values(first.Resources).filter((r: any) => r.Type === 'AWS::IAM::Policy');
  const reads = policies.flatMap(p => p.Properties.PolicyDocument.Statement).filter(s => JSON.stringify(s.Action).includes('secretsmanager:GetSecretValue'));
  expect(reads).toHaveLength(2);
  expect(reads.some(s => s.Resource === process.env.JIRA_OAUTH_SECRET_ARN)).toBe(true);
  expect(reads.every(s => s.Resource !== '*' && !JSON.stringify(s.Resource).includes('identity!*'))).toBe(true);
  process.env.JIRA_OAUTH_CLIENT_SECRET = 'PLAINTEXT-SENTINEL';
  expect(() => synth()).toThrow(/never a plaintext/);
  expect(JSON.stringify(first)).not.toContain('PLAINTEXT-SENTINEL');
});

test('actual application parent namespace request matches only required Memory read grant', () => {
  const request = JSON.parse(execFileSync('python3', ['-c', `
import importlib.util,json,sys,types
captured={}
class Client:
 def __init__(self,**kwargs):pass
 def retrieve_memory_records(self,**kwargs):captured.update(kwargs);return {'memoryRecordSummaries':[]}
sys.modules['config']=types.SimpleNamespace(MEMORY_ID='TestMemory-1234567890',REGION='us-west-2')
sys.modules['bedrock_agentcore.memory']=types.SimpleNamespace(MemoryClient=Client)
spec=importlib.util.spec_from_file_location('enrichment',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.retrieve_past_incidents('U-1002','prior');print(json.dumps(captured))
`, path.join(projectRoot, 'app/ITIncidentAgent/memory/enrichment.py')], { encoding: 'utf8' }));
  expect(request.namespace_path).toBe('incidents/U-1002');
  const resources = synth().toJSON().Resources;
  const memoryId = Object.keys(resources).find(id => resources[id].Type === 'AWS::BedrockAgentCore::Memory')!;
  const memoryArn = { 'Fn::GetAtt': [memoryId, 'MemoryArn'] };
  const statements = Object.entries(resources).filter(([id, r]: [string, any]) => r.Type === 'AWS::IAM::Policy' && id.includes('Runtime'))
    .flatMap(([, r]: [string, any]) => r.Properties.PolicyDocument.Statement);
  const allows = (action: string, resource: any, ns: string) => statements.some((s: any) => {
    const actions = Array.isArray(s.Action) ? s.Action : [s.Action];
    const arns = Array.isArray(s.Resource) ? s.Resource : [s.Resource];
    const match = (pattern: string, value: string) => new RegExp('^' + pattern.replaceAll('*', '.*') + '$').test(value);
    const request: Record<string, string> = { 'bedrock-agentcore:namespacePath': ns };
    const conditionMatches = !s.Condition || Object.entries(s.Condition.StringLike ?? {}).every(([key, value]) => {
      const patterns = Array.isArray(value) ? value : [value];
      return request[key] !== undefined && patterns.some(p => match(String(p), request[key]!));
    });
    return s.Effect === 'Allow' && actions.some((a: string) => match(a, action)) &&
      arns.some((a: any) => a === '*' || JSON.stringify(a) === JSON.stringify(resource)) && conditionMatches;
  });
  expect(allows('bedrock-agentcore:RetrieveMemoryRecords', memoryArn, request.namespace_path)).toBe(true);
  expect(allows('bedrock-agentcore:ListMemoryRecords', memoryArn, request.namespace_path)).toBe(false);
  expect(allows('bedrock-agentcore:RetrieveMemoryRecords', memoryArn, 'unrelated/U-1002')).toBe(false);
  expect(allows('bedrock-agentcore:RetrieveMemoryRecords', 'another-memory-arn', request.namespace_path)).toBe(false);
  // No unconditioned or wildcard-resource READ grant may hide these negatives.
  const reads = statements.filter((s: any) => JSON.stringify(s.Action).includes('RetrieveMemoryRecords'));
  for (const read of reads) {
    expect([read.Resource].flat()).toEqual([memoryArn]);
    expect(read.Condition?.StringLike).toBeDefined();
  }
  const parent = statements.find((s: any) => s.Sid === 'CrossSessionIncidentMemoryRead');
  expect([parent.Action].flat()).toEqual(['bedrock-agentcore:RetrieveMemoryRecords']);
  // SDK event/delete actions do not support namespacePath; this is NOT a claim
  // of per-actor write isolation or an invented DeleteMemoryRecord request shape.
  expect(JSON.stringify(statements)).not.toContain('ConfigurationBundle');
  expect(JSON.stringify(statements)).not.toContain('secretsmanager:GetSecretValue');
});

test('JWT missing requested scope and unapproved account mutation fail synthesis', () => {
  const spec = loadSpec();
  spec.runtimes[0]!.envVars!.push({ name: 'GATEWAY_AUTH_MODE', value: 'CUSTOM_JWT' });
  expect(() => synth(spec)).toThrow(/authentication modes/);
  process.env.TXN_SEARCH_ALLOW_MUTATION = 'true';
  expect(() => synth()).toThrow(/account-owner approval/);
});

test('Transaction Search defaults to read-only observed readiness and keeps evaluators', () => {
  const resources = synth().toJSON().Resources;
  const policy: any = Object.entries(resources).find(([id, r]: [string, any]) => r.Type === 'AWS::IAM::Policy' && id.startsWith('TransactionSearchFn'))?.[1];
  expect(JSON.stringify(policy)).toContain('xray:GetIndexingRules');
  expect(JSON.stringify(policy)).not.toContain('xray:Update');
  expect(JSON.stringify(policy)).not.toContain('logs:PutResourcePolicy');
  const check: any = Object.values(resources).find((r: any) => r.Properties?.IndexingPercentage);
  expect(check.Properties.AllowMutation).toBe('false');
  expect(check.Properties.Account).toBe('111111111111');
});

test('empty optional AgentCore config still preserves custom infrastructure', () => {
  const spec = AgentCoreProjectSpecSchema.parse({ name: 'testproject', version: 2 });
  const template = synth(spec);
  template.resourceCountIs('AWS::DynamoDB::Table', 4);
  template.resourceCountIs('AWS::BedrockAgentCore::Runtime', 0);
  expect(JSON.stringify(template.toJSON())).not.toContain('infra.transaction_search.handler');
});
