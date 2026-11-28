// Offline real Cedar evaluator. The request is a service-shaped fixture, not AWS.
const {createRequire} = require('node:module');
const path = require('node:path');
const fs = require('node:fs');
const requireCdk = createRequire(path.resolve(__dirname, '../agentcore/cdk/package.json'));
const cedar = requireCdk('@cedar-policy/cedar-wasm/nodejs');
const spec = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../agentcore/agentcore.json')));
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const policies = Object.fromEntries(spec.policyEngines[0].policies.map(p => [p.name, p.statement]));
const result = cedar.isAuthorized({
  principal: {type: 'AgentCore::Workload', id: 'offline'},
  action: {type: 'AgentCore::Action', id: input.name},
  resource: {type: 'AgentCore::Gateway', id: 'offline'},
  context: {input: input.arguments}, entities: [], policies: {staticPolicies: policies},
});
if (result.type !== 'success' || result.response.diagnostics.errors.length) throw new Error(JSON.stringify(result));
process.stdout.write(JSON.stringify(result.response));
