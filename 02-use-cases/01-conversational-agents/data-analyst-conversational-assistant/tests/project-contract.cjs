const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { createRequire } = require('node:module');
const backend = path.resolve(__dirname, '../cdk-data-analyst-assistant-agentcore-strands');
const req = createRequire(path.join(backend, 'package.json'));
req('ts-node').register({ project: path.join(backend, 'tsconfig.json') });
const { assertFreshPortState } = require(path.join(backend, 'cdklib/project-contract.ts'));
const { readAgentCoreProject, resolveTargetStacks } = req('@aws/agentcore-cdk');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'data-contract-'));
try {
  fs.mkdirSync(path.join(dir, 'agentcore/.cli'), {recursive: true});
  fs.copyFileSync(path.join(backend, '../agentcore/agentcore.json'), path.join(dir, 'agentcore/agentcore.json'));
  const write = (name, value) => fs.writeFileSync(path.join(dir, 'agentcore', name), JSON.stringify(value));
  const targets = [{name: 'dev', account: '000000000000', region: 'us-east-1'}];
  write('aws-targets.json', {targets});
  assert.throws(() => readAgentCoreProject(dir), error => error.fileType === 'AWS Targets');
  write('aws-targets.json', []);
  assertFreshPortState(dir, 'DataAnalyst', []);
  write('aws-targets.json', targets);
  assert.throws(() => assertFreshPortState(dir, 'DataAnalyst', targets), /fresh/);
  write('fresh-port.json', {version: 1, projectName: 'DataAnalyst', targets});
  assertFreshPortState(dir, 'DataAnalyst', targets);
  // CLI v1 writes empty credentials before its very first synth.
  write('.cli/deployed-state.json', {targets: {dev: {resources: {credentials: {}}}}});
  assertFreshPortState(dir, 'DataAnalyst', targets);
  write('.cli/deployed-state.json', {targets: {dev: {resources: {runtimes: {old: {runtimeId: 'old'}}}}}});
  assert.throws(() => assertFreshPortState(dir, 'DataAnalyst', targets), /Legacy/);
  write('.cli/deployed-state.json', {targets: {dev: {stackArn: 'arn:aws:cloudformation:us-east-1:000000000000:stack/Other/uuid'}}});
  assert.throws(() => assertFreshPortState(dir, 'DataAnalyst', targets), /different stack/);
  write('.cli/deployed-state.json', {targets: {dev: {stackArn: 'arn:aws:cloudformation:us-east-1:000000000000:stack/AgentCore-DataAnalyst-dev/uuid', resources: {credentials: {}}}}});
  assertFreshPortState(dir, 'DataAnalyst', targets);
  const project = readAgentCoreProject(dir);
  const stacks = resolveTargetStacks({projectName: project.projectName, targets: project.targets});
  assert.equal(stacks[0].stackName, 'AgentCore-DataAnalyst-dev');
  assert.equal(stacks[0].tags['agentcore:target-name'], 'dev');
  console.log('PASS v1 targets, fresh marker, pre-synth CLI state, old-state/wrong-stack refusals, stack selection');
} finally { fs.rmSync(dir, {recursive: true, force: true}); }
