// Exercise the actual compiled bin/config reader in a disposable fresh project.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const source = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'claims-v1-entrypoint-'));
try {
  fs.mkdirSync(path.join(temporary, 'agentcore/cdk'), {recursive:true});
  for (const dir of ['app', 'lambdas']) fs.cpSync(path.join(source,dir), path.join(temporary,dir), {
    recursive:true, filter: p => !/(?:^|\/)(?:\.venv|__pycache__)(?:\/|$)/.test(p),
  });
  fs.cpSync(path.join(source,'agentcore/cdk/dist'), path.join(temporary,'agentcore/cdk/dist'), {recursive:true});
  for (const item of ['package.json','package-lock.json','tsconfig.json','cdk.json','bin','lib']) {
    fs.cpSync(path.join(source,'agentcore/cdk',item), path.join(temporary,'agentcore/cdk',item), {recursive:true});
  }
  fs.symlinkSync(path.join(source,'agentcore/cdk/node_modules'), path.join(temporary,'agentcore/cdk/node_modules'));
  const spec = JSON.parse(fs.readFileSync(path.join(source,'agentcore/agentcore.json')));
  spec.name = 'Claimsoffline01';
  fs.writeFileSync(path.join(temporary,'agentcore/agentcore.json'), JSON.stringify(spec));
  fs.writeFileSync(path.join(temporary,'agentcore/aws-targets.json'), JSON.stringify([
    {name:'dev',account:'111122223333',region:'us-west-2'}]));
  const ledger = {version:1, owner:'offline-owner', project:spec.name,target:'dev',account:'111122223333',region:'us-west-2',
    notifications:false, client_id:'offlineclient',
    secret_arn:'arn:aws:secretsmanager:us-west-2:111122223333:secret:bedrock-agentcore-identity!offline-abcdef',
    discovery_url:'https://cognito-idp.us-west-2.amazonaws.com/us-west-2_offline/.well-known/openid-configuration',
    provider_arn:'arn:aws:bedrock-agentcore:us-west-2:111122223333:token-vault/default/oauth2credentialprovider/Claimsoffline01_dev_cognito-gateway-m2m'};
  fs.writeFileSync(path.join(temporary,'.claims-v1.json'), JSON.stringify(ledger));
  fs.mkdirSync(path.join(temporary,'agentcore/.cli'));
  const deployed = {targets:{dev:{resources:{credentials:{'cognito-gateway-m2m':{
    credentialProviderArn:ledger.provider_arn,
    clientSecretArn:'arn:aws:secretsmanager:us-west-2:111122223333:secret:bedrock-agentcore-identity!offline-abcdef'}}}}}};
  fs.writeFileSync(path.join(temporary,'agentcore/.cli/deployed-state.json'), JSON.stringify(deployed));
  const env = {...process.env, CDK_DISABLE_CLI_TELEMETRY:'1', CDK_NOTICES:'false', CDK_DISABLE_VERSION_CHECK:'1',
    AWS_ACCESS_KEY_ID:'offline', AWS_SECRET_ACCESS_KEY:'offline', AWS_SESSION_TOKEN:'offline', AWS_EC2_METADATA_DISABLED:'true',
    CDK_DEFAULT_ACCOUNT:'111122223333', CDK_DEFAULT_REGION:'us-west-2',
    AWS_CONFIG_FILE:'/dev/null',AWS_SHARED_CREDENTIALS_FILE:'/dev/null',CDK_OUTDIR:path.join(temporary,'assembly'),
    NODE_OPTIONS:`--require=${path.join(source,'tests/offline.cjs')}`};
  delete env.AWS_PROFILE; delete env.AWS_DEFAULT_PROFILE;
  const run = () => spawnSync(process.execPath,['dist/bin/cdk.js'], {cwd:path.join(temporary,'agentcore/cdk'),env,encoding:'utf8'});
  let result = run();
  assert.equal(result.status,0,result.stderr);
  const template = JSON.parse(fs.readFileSync(path.join(temporary,'assembly','AgentCore-Claimsoffline01-dev.template.json')));
  assert.ok(Object.values(template.Resources).some(r=>r.Type==='AWS::DynamoDB::Table'));
  if (process.env.CLAIMS_TEST_CLI) {
    console.log('Running native CLI build with telemetry/notices disabled and network guard');
    const build = spawnSync(process.env.CLAIMS_TEST_CLI,['build','--json'], {cwd:temporary, env, encoding:'utf8', timeout:60000});
    console.log(build.stdout); console.log(build.stderr);
    assert.equal(build.status,0,'Pinned native CLI build (network guarded)');
  }
  ledger.account = '999999999999';
  fs.writeFileSync(path.join(temporary,'.claims-v1.json'),JSON.stringify(ledger));
  result = run(); assert.notEqual(result.status,0); assert.match(result.stderr,/target differs/);
  ledger.account = '111122223333';
  fs.writeFileSync(path.join(temporary,'.claims-v1.json'),JSON.stringify(ledger));
  deployed.targets.dev.resources.credentials['cognito-gateway-m2m'].credentialProviderArn += '-unowned';
  fs.writeFileSync(path.join(temporary,'agentcore/.cli/deployed-state.json'),JSON.stringify(deployed));
  result = run(); assert.notEqual(result.status,0); assert.match(result.stderr,/Credential state differs/);
  fs.unlinkSync(path.join(temporary,'.claims-v1.json'));
  result = run(); assert.notEqual(result.status,0); assert.match(result.stderr,/claims-v1/);
  console.log('PASS: actual bin/config/deployed-state path; mismatches and legacy absence refused');
} finally { fs.rmSync(temporary,{recursive:true,force:true}); }
