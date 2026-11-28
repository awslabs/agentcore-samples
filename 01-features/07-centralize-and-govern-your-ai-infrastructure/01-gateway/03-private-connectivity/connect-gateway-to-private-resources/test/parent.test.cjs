const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawnSync } = require('node:child_process');
const { DISABLE_ASSET_STAGING_CONTEXT } = require('aws-cdk-lib/cx-api');
const root = path.resolve(__dirname, '..');

// Exact parent app and installed dependencies, fake context ONLY. No lookups,
// image builds, deployments, EKS Helm execution or Lambda execution occur here.
test('parent synthesizes all retained branches and preserves private resource contracts', () => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'vpc-parent-'));
  try {
    const context = { ...JSON.parse(fs.readFileSync(path.join(root, 'cdk.json'))).context,
      accountA: '123456789012', accountB: '222222222222', baseDomain: 'example.com',
      privateDomain: 'internal.example.com', hostedZoneId: 'Z1234567890',
      publicCertArn: 'arn:aws:acm:us-west-2:123456789012:certificate/00000000-0000-0000-0000-000000000000',
      [DISABLE_ASSET_STAGING_CONTEXT]: true,
    };
    for (const [account, region] of [['123456789012','us-west-2'], ['123456789012','us-east-1'], ['222222222222','us-west-2']]) {
      context[`availability-zones:account=${account}:region=${region}`] = [region + 'a', region + 'b'];
    }
    const result = spawnSync(process.execPath, ['--require', path.join(__dirname, 'no-network.cjs'), 'dist/bin/vpcegress.js'], {
      cwd: root, encoding: 'utf8', env: {
        PATH: process.env.PATH, HOME: out, TMPDIR: out, CDK_OUTDIR: out,
        CDK_CONTEXT_JSON: JSON.stringify(context), AWS_EC2_METADATA_DISABLED: 'true',
        AWS_ACCESS_KEY_ID: 'testing', AWS_SECRET_ACCESS_KEY: 'testing',
      },
    });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const manifest = JSON.parse(fs.readFileSync(path.join(out, 'manifest.json')));
    assert.deepEqual(manifest.missing ?? [], []);
    const stacks = Object.entries(manifest.artifacts).filter(([, value]) => value.type === 'aws:cloudformation:stack');
    assert.equal(stacks.length, 19);
    assert.ok(!stacks.some(([name]) => name === 'SharedAgentCoreGateway'));
    for (const [, stack] of stacks) {
      const { Resources } = JSON.parse(fs.readFileSync(path.join(out, stack.properties.templateFile)));
      assert.ok(!Object.values(Resources).some(r => r.Type === 'AWS::BedrockAgentCore::Gateway'),
        'only the nested CLI project owns the Gateway');
    }
    for (const [, artifact] of stacks) {
      for (const entries of Object.values(artifact.metadata ?? {})) {
        assert.equal(entries.filter(e => e.type === 'aws:cdk:error').length, 0);
      }
    }
    const template = name => JSON.parse(fs.readFileSync(path.join(out, `${name}.template.json`)));
    const vpc = template('VpcegressStack-USWest2');
    const defaultRestriction = Object.fromEntries(Object.entries(vpc.Resources).filter(([, r]) => r.Type === 'Custom::VpcRestrictDefaultSG'));
    assert.equal(context['@aws-cdk/aws-ec2:restrictDefaultSecurityGroup'], true);
    assert.equal(Object.keys(defaultRestriction).length, 1);
    // Feed the actual synthesis result into the SDK/request composition suite.
    if (process.env.VPC_NETWORK_EVIDENCE) fs.writeFileSync(process.env.VPC_NETWORK_EVIDENCE, JSON.stringify(defaultRestriction));
    for (const key of ['IsolatedSubnetIds','PrivateSubnetIds','PublicSubnetIds','VpcId']) assert.ok(vpc.Outputs[key]);
    const guards = Object.values(vpc.Resources).filter(r => r.Type === 'AWS::Lambda::Function' && r.Properties.Code?.ZipFile?.includes('Clean up owned lab security groups'));
    assert.equal(guards.length, 1);
    assert.ok(!guards[0].Properties.Code.ZipFile.includes('delete_security_group'));
    assert.ok(!JSON.stringify(vpc).includes('ec2:DeleteSecurityGroup'));
    const ecs = template('McpEcs');
    const balancer = Object.values(ecs.Resources).find(r => r.Type === 'AWS::ElasticLoadBalancingV2::LoadBalancer');
    assert.equal(balancer.Properties.Scheme, 'internal');
    assert.equal(Object.values(ecs.Resources).filter(r => r.Type === 'AWS::ECS::Service').length, 2);
    const api = template('PrivateApigw');
    const rest = Object.values(api.Resources).find(r => r.Type === 'AWS::ApiGateway::RestApi');
    assert.deepEqual(rest.Properties.EndpointConfiguration.Types, ['PRIVATE']);
    assert.ok(JSON.stringify(rest.Properties.Policy).includes('aws:sourceVpce'));
    const ca = template('PrivateCaBackend');
    const statements = Object.values(ca.Resources).filter(r => r.Type === 'AWS::IAM::Policy').flatMap(r => r.Properties.PolicyDocument.Statement);
    const deletion = statements.find(s => [].concat(s.Action).includes('acm:DeleteCertificate'));
    assert.ok(deletion.Condition.StringEquals['aws:ResourceTag/vpc-egress-stack']);
    assert.notEqual(deletion.Resource, '*');
    const assets = JSON.parse(fs.readFileSync(path.join(out, 'McpEcs.assets.json')));
    assert.equal(Object.keys(assets.dockerImages).length, 2);
    for (const image of Object.values(assets.dockerImages)) assert.equal(image.source.platform, 'linux/amd64');
  } finally { fs.rmSync(out, { recursive: true, force: true }); }
});
