// Optional pinned-CLI contract regression: node tests/oauth_cli_contract.cjs /path/to/@aws/agentcore/dist/main.js
// Extract functions, NEVER run the CLI entrypoint or construct a service client.
// Python test_completion.py covers the create-only ownership gate placed before this upsert.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const ts = createRequire(path.resolve(__dirname, '../agentcore/cdk/package.json'))('typescript');
const bundle = path.resolve(process.argv[2]);
const expectedVersion = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../package.json'))).devDependencies[
  '@aws/agentcore'
];
assert.equal(JSON.parse(fs.readFileSync(path.resolve(bundle, '../../package.json'))).version, expectedVersion);
const source = fs.readFileSync(bundle, 'utf8');
const ast = ts.createSourceFile('main.js', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
const names = [
  'resolveOauth2',
  'requireVendorMatch',
  'undefinedWhenAbsent',
  'secretFromEnv',
  'guidedCustomConfig',
  'oauth2Provision',
  'deployedCredential',
  'requireArn',
  'credentialEnvVarName',
  'keepsV0CredentialNames',
  'previousProviderName',
];
const functions = names.map(name => {
  const nodes = ast.statements.filter(s => ts.isFunctionDeclaration(s) && s.name?.text === name);
  assert.equal(nodes.length, 1, name);
  return nodes[0].getText(ast);
});
const context = {
  CREDENTIAL_ENV_PREFIX: 'AGENTCORE_CREDENTIAL_',
  ProjectStateError: Error,
  MalformedServiceResponseError: Error,
  $ResourceNotFoundException: class extends Error {},
};
vm.createContext(context);
vm.runInContext(functions.join('\n'), context);
const spec = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../agentcore/agentcore.json')));
assert.equal(spec.migratedFrom, 'v1');
const credential = {
  name: 'banking-oauth-contract',
  authorizerType: 'OAuthCredentialProvider',
  vendor: 'CustomOauth2',
  clientId: 'new-client',
  discoveryUrl: 'https://fake.invalid',
};
assert.equal(context.previousProviderName({ spec }, 'default', credential.name), credential.name);
(async () => {
  let updates = 0;
  const response = {
    credentialProviderArn: 'arn:fake-provider',
    credentialProviderVendor: 'CustomOauth2',
    tags: { 'banking-sample-owner': 'unrelated' },
    clientSecretArn: { secretArn: 'arn:fake-secret' },
  };
  const identity = {
    async getOauth2CredentialProvider() {
      return response;
    },
    async updateOauth2CredentialProvider() {
      updates++;
      return response;
    },
  };
  const secretKey = context.credentialEnvVarName(credential.name, '_CLIENT_SECRET');
  const plan = await context.resolveOauth2(identity, credential, credential.name, {}, { [secretKey]: 'fake' }, '/fake');
  assert.equal(plan.kind, 'update');
  await plan.write();
  assert.equal(updates, 1); // The pinned CLI does NOT check ownership; keep our preflight!
  const reuse = await context.resolveOauth2(identity, credential, credential.name, {}, {}, '/fake');
  assert.equal(reuse.reuse.credentialProviderArn, response.credentialProviderArn);
  assert.equal(updates, 1);
  console.log(
    `PASS pinned ${expectedVersion} contract: bare migrated name + secret upserts an unowned provider; no-secret path reuses. Create-only ownership reservation and --reuse-owned gate are mandatory. Fake Identity only.`
  );
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
