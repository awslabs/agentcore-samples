const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { execFileSync } = require('node:child_process');
const cdk = require('aws-cdk-lib');
const { Template } = require('aws-cdk-lib/assertions');
const { transformAgentCoreJson, readAgentCoreProject, resolveTargetStacks } = require('@aws/agentcore-cdk');
const { AgentCoreStack } = require('../dist/lib/cdk-stack');
const root = path.resolve(__dirname, '../../..');
const spec = JSON.parse(fs.readFileSync(path.join(root, 'agentcore/agentcore.json')));
const target = { name: 'default', account: '123456789012', region: 'us-west-2' };

function synth(input, projectRoot = root) {
  const outdir = fs.mkdtempSync(path.join(os.tmpdir(), 'banking-cdk-'));
  try {
    const app = new cdk.App({
      outdir,
      context: {
        '@aws-cdk/core:validateAgainstDefaultRules': Boolean(input.runtimes?.length),
      },
    });
    const stack = new AgentCoreStack(app, 'BankingTest', {
      env: { account: target.account, region: target.region },
      application: transformAgentCoreJson(input, { projectRoot, target }),
    });
    const template = Template.fromStack(stack);
    if (input.runtimes?.length && process.env.BANKING_TEST_PACKAGE) {
      const runtime = Object.values(template.toJSON().Resources).find(
        r => r.Type === 'AWS::BedrockAgentCore::Runtime'
      ).Properties;
      const zips = fs.readdirSync(outdir).filter(name => /^asset\..*\.zip$/.test(name));
      assert.ok(zips.length, 'real CodeZip asset must be synthesized');
      for (const zip of zips) {
        execFileSync(
          process.env.PYTHON || 'python3',
          [
            path.join(root, 'tests/artifact_contract.py'),
            path.join(outdir, zip),
            JSON.stringify(runtime.AgentRuntimeArtifact.CodeConfiguration),
          ],
          { stdio: 'inherit' }
        );
      }
    }
    return template;
  } finally {
    fs.rmSync(outdir, { recursive: true, force: true });
  }
}

test('empty migrated config and stack preserve ownership and output', () => {
  assert.equal(spec.version, 2);
  assert.equal(spec.migratedFrom, 'v1');
  assert.equal(spec.name, 'bankingassistant');
  assert.deepEqual(spec.agentCoreGateways, []);
  assert.deepEqual(spec.policyEngines, []);
  const project = readAgentCoreProject(root);
  assert.equal(project.projectName, spec.name);
  assert.ok(resolveTargetStacks({ projectName: spec.name, targets: [target] }).length);
  const template = synth(spec);
  template.hasOutput('StackNameOutput', { Description: 'Name of the CloudFormation Stack' });
  template.resourceCountIs('AWS::BedrockAgentCore::Gateway', 0);
  template.resourceCountIs('AWS::BedrockAgentCore::PolicyEngine', 0);
  template.resourceCountIs('AWS::IAM::ManagedPolicy', 0);
});

test('v0 fields and malformed v1 runtime JWT fail validation', () => {
  assert.throws(() => transformAgentCoreJson({ ...spec, version: 1 }, { projectRoot: root }));
  assert.throws(() => transformAgentCoreJson({ ...spec, abTests: [] }, { projectRoot: root }));
  assert.throws(() =>
    transformAgentCoreJson(
      {
        ...spec,
        runtimes: [
          {
            name: 'banking_assistant_tools',
            build: 'CodeZip',
            runtimeVersion: 'PYTHON_3_14',
            entrypoint: 'main.py',
            codeLocation: 'app/banking_assistant_tools',
            protocol: 'MCP',
            authorizerType: 'CUSTOM_JWT',
            authorizerConfiguration: { customJWTAuthorizer: {} },
          },
        ],
      },
      { projectRoot: root }
    )
  );
});

test('CLI-generated populated fixture validates without dropping MCP JWT/scopes', () => {
  const generated = JSON.parse(fs.readFileSync(path.join(__dirname, 'runtime-config.json')));
  const app = transformAgentCoreJson(generated, { projectRoot: root, target });
  const runtime = app.spec.runtimes[0];
  assert.equal(runtime.name, 'banking_assistant_tools');
  assert.equal(runtime.protocol, 'MCP');
  assert.equal(runtime.codeLocation, 'app/banking_assistant_tools');
  assert.equal(runtime.entrypoint, 'main.py');
  assert.equal(runtime.authorizerType, 'CUSTOM_JWT');
  assert.deepEqual(runtime.authorizerConfiguration.customJwtAuthorizer.allowedScopes, ['api/mcp']);
  assert.deepEqual(runtime.authorizerConfiguration.customJwtAuthorizer.allowedClients, ['fake-client']);
  assert.equal(app.spec.credentials[0].name, 'banking_assistant_tools-oauth');
  assert.equal(app.spec.migratedFrom, 'v1');
});

test(
  'real CodeZip synthesis limits the MCP execution role',
  {
    skip: !process.env.BANKING_TEST_PACKAGE && 'set BANKING_TEST_PACKAGE=1 with uv and cached app dependencies',
  },
  () => {
    const generated = JSON.parse(fs.readFileSync(path.join(__dirname, 'runtime-config.json')));
    const json = synth(generated).toJSON();
    const resources = Object.values(json.Resources);
    const runtime = resources.find(r => r.Type === 'AWS::BedrockAgentCore::Runtime').Properties;
    assert.equal(runtime.AgentRuntimeName, 'bankingassistant_banking_assistant_tools');
    assert.equal(runtime.ProtocolConfiguration, 'MCP');
    assert.deepEqual(runtime.AuthorizerConfiguration.CustomJWTAuthorizer.AllowedScopes, ['api/mcp']);
    assert.ok(!resources.some(r => r.Type === 'AWS::BedrockAgentCore::Gateway'));
    const boundary = resources.find(r => r.Type === 'AWS::IAM::ManagedPolicy');
    assert.ok(boundary);
    const statements = boundary.Properties.PolicyDocument.Statement;
    const actions = statements.flatMap(s => (Array.isArray(s.Action) ? s.Action : [s.Action]));
    assert.ok(
      actions.every(
        a =>
          a.startsWith('logs:') ||
          a.startsWith('s3:') ||
          a.startsWith('xray:') ||
          a === 'cloudwatch:PutMetricData' ||
          a === 'kms:Decrypt'
      )
    );
    const star = statements.filter(s => s.Resource === '*');
    assert.equal(star.length, 3);
    assert.ok(star.every(s => s.Condition.StringEquals['aws:RequestedRegion'] === target.region));
    assert.equal(
      star.find(s => s.Action === 'cloudwatch:PutMetricData').Condition.StringEquals['cloudwatch:namespace'],
      'bedrock-agentcore'
    );
    // Expand pseudo-parameters only; this is pattern checking, not an IAM simulator.
    const resolve = value =>
      typeof value === 'string'
        ? value
        : value.Ref
          ? { 'AWS::Partition': 'aws' }[value.Ref] || value.Ref
          : value['Fn::Join'][1].map(resolve).join(value['Fn::Join'][0]);
    const write = statements.find(s => [].concat(s.Action).includes('logs:PutLogEvents'));
    const patterns = [].concat(write.Resource).map(resolve);
    const matches = arn =>
      patterns.some(pattern =>
        new RegExp('^' + pattern.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*') + '$').test(arn)
      );
    const prefix = 'arn:aws:logs:us-west-2:123456789012:log-group:/aws/bedrock-agentcore/runtimes/';
    assert.ok(matches(prefix + runtime.AgentRuntimeName + '-AbCdEf1234-DEFAULT:log-stream:runtime-logs'));
    assert.ok(!matches(prefix + runtime.AgentRuntimeName + '_extra-AbCdEf1234-DEFAULT:log-stream:runtime-logs'));
    assert.ok(!matches(prefix + 'other-AbCdEf1234-DEFAULT:log-stream:runtime-logs'));
    assert.ok(!actions.some(a => a.startsWith('bedrock:') || a.startsWith('bedrock-agentcore:')));
    assert.ok(resources.find(r => r.Type === 'AWS::IAM::Role').Properties.PermissionsBoundary);
    const saved = process.env.AGENTCORE_SAMPLE_ASSET_KMS_KEY_ARN;
    try {
      const key = 'arn:aws:kms:us-west-2:123456789012:key/11111111-1111-1111-1111-111111111111';
      process.env.AGENTCORE_SAMPLE_ASSET_KMS_KEY_ARN = key;
      const withKey = Object.values(synth(generated).toJSON().Resources).find(r => r.Type === 'AWS::IAM::ManagedPolicy')
        .Properties.PolicyDocument.Statement;
      const decrypt = withKey.find(s => s.Action === 'kms:Decrypt');
      assert.equal(decrypt.Resource, key);
      assert.deepEqual(decrypt.Condition.StringEquals['kms:ViaService'], {
        'Fn::Join': ['', ['s3.us-west-2.', { Ref: 'AWS::URLSuffix' }]],
      });
      assert.equal(decrypt.Condition.StringEquals['kms:EncryptionContext:aws:s3:arn'].length, 2);
      process.env.AGENTCORE_SAMPLE_ASSET_KMS_KEY_ARN = '*';
      assert.throws(() => synth(generated), /must be a KMS key ARN/);
    } finally {
      if (saved === undefined) delete process.env.AGENTCORE_SAMPLE_ASSET_KMS_KEY_ARN;
      else process.env.AGENTCORE_SAMPLE_ASSET_KMS_KEY_ARN = saved;
    }
  }
);
