// Real pinned CDK schema + construct + CloudFormation synthesis (no service calls).
// Container source is the actual app, not a fixture ZIP/image. Image build is separate.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '..');
const requireCdk = createRequire(path.join(root, 'agentcore/cdk/package.json'));
const { App } = requireCdk('aws-cdk-lib');
const { Template } = requireCdk('aws-cdk-lib/assertions');
const { transformAgentCoreJson } = requireCdk('@aws/agentcore-cdk');
const { AgentCoreStack } = requireCdk('./dist/lib/cdk-stack');
const input = JSON.parse(fs.readFileSync(path.join(root, 'agentcore/agentcore.json')));
input.name = 'Claimsoffline01';
const provider = 'Claimsoffline01_dev_cognito-gateway-m2m';
const credential = {
  credentialProviderArn: `arn:aws:bedrock-agentcore:us-west-2:111122223333:token-vault/default/oauth2credentialprovider/${provider}`,
  clientSecretArn: 'arn:aws:secretsmanager:us-west-2:111122223333:secret:bedrock-agentcore-identity!offline-abcdef',
};
function make(spec = input, notifications = false) {
  const application = transformAgentCoreJson(spec, {projectRoot: root,
    target: { name: 'dev', account: '111122223333', region: 'us-west-2' },
    credentials: {'cognito-gateway-m2m': credential}});
  const app = new App();
  const stack = new AgentCoreStack(app, 'AgentCore-Claimsoffline01-dev', {
    env: {account: '111122223333', region: 'us-west-2'}, application, notifications,
    discoveryUrl: 'https://cognito-idp.us-west-2.amazonaws.com/us-west-2_offline/.well-known/openid-configuration',
    clientId: 'offlineclient',
  });
  return Template.fromStack(stack).toJSON();
}
const template = make();
if (process.env.CLAIMS_TEMPLATE_OUT) fs.writeFileSync(process.env.CLAIMS_TEMPLATE_OUT, JSON.stringify(template, null, 2));
const resources = Object.entries(template.Resources);
const ofType = t => resources.filter(([,r]) => r.Type === t);
for (const [type, count] of Object.entries({
  'AWS::DynamoDB::Table': 3, 'AWS::S3::Bucket': 1, 'AWS::SNS::Topic': 1,
  'AWS::Events::Rule': 1, 'AWS::SQS::Queue': 1, 'AWS::CloudWatch::Alarm': 1,
  'AWS::BedrockAgentCore::Runtime': 1, 'AWS::BedrockAgentCore::Memory': 1,
  'AWS::BedrockAgentCore::Gateway': 1, 'AWS::BedrockAgentCore::GatewayTarget': 6,
  'AWS::BedrockAgentCore::PolicyEngine': 1, 'AWS::BedrockAgentCore::Policy': 2,
  'AWS::BedrockAgentCore::Evaluator': 1, 'AWS::BedrockAgentCore::OnlineEvaluationConfig': 1,
})) assert.equal(ofType(type).length, count, type);
const runtime = ofType('AWS::BedrockAgentCore::Runtime')[0][1].Properties;
assert.equal(runtime.AuthorizerConfiguration, undefined, 'Runtime must stay IAM inbound');
assert.equal(runtime.EnvironmentVariables.AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER, provider);
assert.equal(runtime.EnvironmentVariables.AGENT_MODEL_ID, 'global.anthropic.claude-sonnet-4-6');
assert.match(JSON.stringify(runtime), /MEMORY_CLAIMSAGENTMEMORY_ID/);
assert.doesNotMatch(JSON.stringify(runtime), /CLIENT_SECRET/);
const gateway = ofType('AWS::BedrockAgentCore::Gateway')[0][1].Properties;
assert.equal(gateway.AuthorizerType, 'CUSTOM_JWT');
assert.equal(gateway.PolicyEngineConfiguration.Mode, 'ENFORCE');
assert.deepEqual(gateway.AuthorizerConfiguration.CustomJWTAuthorizer.AllowedClients, ['offlineclient']);
const gatewayRole = gateway.RoleArn['Fn::GetAtt'][0];
const invokePolicies = ofType('AWS::IAM::Policy').filter(([,r]) =>
  r.Properties.Roles?.some(role => role.Ref === gatewayRole) &&
  JSON.stringify(r.Properties.PolicyDocument).includes('lambda:InvokeFunction'));
assert.equal(invokePolicies.length, 1);
for (const [,target] of ofType('AWS::BedrockAgentCore::GatewayTarget')) {
  assert.match(JSON.stringify(target.Properties), /Fn::GetAtt/);
  assert.ok(target.DependsOn.includes(invokePolicies[0][0]), 'target ordered after Gateway Lambda-invoke IAM');
}
const boundary = ofType('AWS::IAM::ManagedPolicy').find(([id]) => id.startsWith('ClaimsRuntimeBoundary'));
assert.ok(boundary);
const bounded = JSON.stringify(boundary[1]);
assert.doesNotMatch(bounded, /ConfigurationBundle|GetResourceApiKey|foundation-model\/\*|logs:PutResourcePolicy/);
assert.match(bounded, /global.anthropic.claude-sonnet-4-6/);
assert.match(bounded, /us.anthropic.claude-haiku-4-5-20251001-v1:0/);
assert.match(bounded, /bedrock-agentcore-identity!offline-abcdef/);
assert.ok(ofType('AWS::IAM::Role').some(([,r]) => r.Properties.PermissionsBoundary?.Ref === boundary[0]));
assert.equal(ofType('AWS::Logs::Delivery').length, 4);
assert.ok(ofType('AWS::Logs::LogGroup').every(([,r]) => r.DeletionPolicy === 'Delete'));
const memory = ofType('AWS::BedrockAgentCore::Memory')[0][1].Properties;
assert.equal(memory.EventExpiryDuration, 90);
assert.match(JSON.stringify(memory), /claims\/\{actorId\}\/facts/);
assert.match(JSON.stringify(memory), /claims\/\{actorId\}\/\{sessionId\}/);
const tools = ofType('AWS::Lambda::Function').filter(([,r]) => r.Properties.FunctionName?.startsWith('Claimsoffline01-dev-'));
assert.equal(tools.length, 7);
assert.equal(tools.find(([,r]) => r.Properties.FunctionName.endsWith('-Notification'))[1].Properties.Environment.Variables.NOTIFICATIONS_ENABLED, 'false');
assert.doesNotMatch(JSON.stringify(template), /ses:SendEmail|sns:Publish/);
assert.match(JSON.stringify(make(input, true)), /ses:SendEmail/);
const wrong = structuredClone(input);
wrong.agentCoreGateways[0].targets[0].name = 'unowned';
assert.throws(() => make(wrong), /No supplementary Lambda ARN/);
const unenforced = structuredClone(input);
unenforced.agentCoreGateways[0].policyEngineConfiguration.mode = 'LOG_ONLY';
assert.throws(() => make(unenforced), /ENFORCE/);
if (process.env.CLAIMS_TEMPLATE_OUT) fs.writeFileSync(process.env.CLAIMS_TEMPLATE_OUT, JSON.stringify(template, null, 2));
console.log(`PASS: real v1 schema/construct synth; ${resources.length} resources; no cloud/image build`);
