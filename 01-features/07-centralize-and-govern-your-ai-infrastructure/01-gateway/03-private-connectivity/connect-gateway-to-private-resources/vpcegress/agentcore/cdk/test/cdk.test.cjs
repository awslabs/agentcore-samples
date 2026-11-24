const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { App } = require('aws-cdk-lib');
const { Template } = require('aws-cdk-lib/assertions');
const { AgentCoreProjectSpecSchema, transformAgentCoreJson, resolveTargetStacks } = require('@aws/agentcore-cdk');
const { AgentCoreStack } = require('../dist/lib/cdk-stack');

const projectRoot = path.resolve(__dirname, '../../..');
const spec = JSON.parse(fs.readFileSync(path.join(projectRoot, 'agentcore/agentcore.json')));
const target = { name: 'default', account: '123456789012', region: 'us-west-2' };

function synth(config, vpcPermissions) {
  const outdir = fs.mkdtempSync(path.join(os.tmpdir(), 'gateway-cdk-test-'));
  try {
    const app = new App({ outdir });
    const stack = new AgentCoreStack(app, 'OfflineTest', {
      env: target,
      application: transformAgentCoreJson(config, { projectRoot, target }),
      vpcPermissions,
    });
    return Template.fromStack(stack).toJSON();
  } finally {
    fs.rmSync(outdir, { recursive: true, force: true });
  }
}

test('checked-in scaffold validates and synthesizes without resources', () => {
  assert.equal(AgentCoreProjectSpecSchema.safeParse(spec).success, true);
  assert.deepEqual(synth(spec).Resources ?? {}, {});
  assert.equal(spec.migratedFrom, 'v1');
  const stacks = resolveTargetStacks({ projectName: spec.name, targets: [target] });
  assert.equal(stacks[0].stackName, `AgentCore-${spec.name}-default`);
});

test('JWT gateway retains its name, authorizer and Lambda target contract', () => {
  // Use a local fixture schema, never a real Lambda or a network lookup.
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'gateway-tools-'));
  const toolSchemaFile = path.join(fixture, 'tools.json');
  fs.writeFileSync(toolSchemaFile, JSON.stringify([
    { name: 'get_order_tool', description: 'Read one order', inputSchema: { type: 'object', properties: {} } },
  ]));
  try {
    const template = synth({ ...spec, agentCoreGateways: [{
      name: 'lambda-oauth-gateway', protocolType: 'MCP',
      authorizerType: 'CUSTOM_JWT',
      authorizerConfiguration: { customJwtAuthorizer: {
        discoveryUrl: 'https://idp.example.com/.well-known/openid-configuration',
        allowedClients: ['gateway-client'],
      } },
      exceptionLevel: 'DEBUG',
      targets: [{ name: 'lambda-order-tools', targetType: 'lambdaFunctionArn',
        lambdaFunctionArn: { lambdaArn: 'arn:aws:lambda:us-west-2:123456789012:function:offline-orders', toolSchemaFile },
      }],
    }] });
    const resources = Object.values(template.Resources);
    const gateway = resources.find(r => r.Type === 'AWS::BedrockAgentCore::Gateway').Properties;
    assert.equal(gateway.Name, `${spec.name}-lambda-oauth-gateway`);
    assert.equal(gateway.AuthorizerType, 'CUSTOM_JWT');
    assert.deepEqual(gateway.AuthorizerConfiguration.CustomJWTAuthorizer.AllowedClients, ['gateway-client']);
    assert.equal(gateway.ProtocolType, 'MCP');
    const lambdaTarget = resources.find(r => r.Type === 'AWS::BedrockAgentCore::GatewayTarget').Properties;
    assert.equal(lambdaTarget.Name, 'lambda-order-tools');
    assert.equal(lambdaTarget.TargetConfiguration.Mcp.Lambda.LambdaArn, 'arn:aws:lambda:us-west-2:123456789012:function:offline-orders');
    assert.equal(lambdaTarget.CredentialProviderConfigurations[0].CredentialProviderType, 'GATEWAY_IAM_ROLE');
  } finally {
    fs.rmSync(fixture, { recursive: true, force: true });
  }
});

test('SDK-owned provider grants are limited to exact provider, secret and local identity ARNs', () => {
  const gateway = { name: 'vpc-egress-gateway', protocolType: 'MCP', authorizerType: 'AWS_IAM', targets: [] };
  const providerArn = 'arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default/apikeycredentialprovider/lab-key';
  const secretArn = 'arn:aws:secretsmanager:us-west-2:123456789012:secret:lab-key-123456';
  const permissions = { gatewayName: gateway.name, providers: [{ type: 'api-key', providerArn, secretArn }], lambdaArns: [] };
  const config = { ...spec, agentCoreGateways: [gateway] };
  const template = synth(config, permissions);
  const statements = Object.values(template.Resources).filter(r => r.Type === 'AWS::IAM::Policy').flatMap(r => r.Properties.PolicyDocument.Statement);
  const secrets = statements.filter(s => [].concat(s.Action).includes('secretsmanager:GetSecretValue'));
  assert.equal(secrets.length, 1);
  assert.deepEqual([].concat(secrets[0].Resource), [secretArn]);
  assert.ok(JSON.stringify(statements).includes(providerArn));
  assert.ok(!JSON.stringify(statements).includes('configuration-bundle'));
  assert.ok(!JSON.stringify(statements).includes('bedrock:InvokeModel'));
  assert.ok(!JSON.stringify(statements).includes('ec2:CreateNetworkInterface'));
  for (const invalid of [providerArn.replace('123456789012', '222222222222'), providerArn.replace('lab-key', '*'), providerArn.replace('us-west-2', 'us-east-1')]) {
    assert.throws(() => synth(config, { ...permissions, providers: [{ type: 'api-key', providerArn: invalid, secretArn }] }), /Grant/);
  }
});

test('legacy version and unsupported top-level resources fail validation', () => {
  assert.equal(AgentCoreProjectSpecSchema.safeParse({ ...spec, version: 1 }).success, false);
  assert.equal(AgentCoreProjectSpecSchema.safeParse({ ...spec, httpGateways: [] }).success, false);
});
