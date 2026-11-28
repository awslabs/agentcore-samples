// Offline synthesis assertions for the preserving CLI v1 custom stack.
// From the sample root, after installing backend dependencies:
//   node tests/stack-invariants.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createRequire, syncBuiltinESMExports } = require('node:module');

const denyNetwork = () => { throw new Error('Offline stack test attempted network access'); };
require('node:net').Socket.prototype.connect = denyNetwork;
require('node:tls').connect = denyNetwork;
require('node:http').request = require('node:https').request = denyNetwork;
globalThis.fetch = denyNetwork;
syncBuiltinESMExports();

const backend = path.resolve(__dirname, '../cdk-data-analyst-assistant-agentcore-strands');
const backendRequire = createRequire(path.join(backend, 'package.json'));
const ts = backendRequire('typescript');
const cdk = backendRequire('aws-cdk-lib');
const { Template, Match } = backendRequire('aws-cdk-lib/assertions');
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'data-analyst-synth-'));
const oldTsLoader = require.extensions['.ts'];
require.extensions['.ts'] = (module, filename) => {
  const result = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  });
  module._compile(result.outputText, filename);
};

try {
  const { CdkDataAnalystAssistantAgentcoreStrandsStack } = require(path.join(
    backend, 'cdklib/cdk-data-analyst-assistant-agentcore-strands-stack.ts',
  ));
  const app = new cdk.App({ outdir: output, context: {
    'availability-zones:account=000000000000:region=us-east-1': ['us-east-1a', 'us-east-1b'],
  }});
  const stack = new CdkDataAnalystAssistantAgentcoreStrandsStack(
    app, 'CdkDataAnalystAssistantAgentcoreStrandsStack', {
      env: { account: '000000000000', region: 'us-east-1' }, // synthetic test identity only
      application: backendRequire('@aws/agentcore-cdk').transformAgentCoreJson(
        JSON.parse(fs.readFileSync(path.join(backend, '../agentcore/agentcore.json'), 'utf8')),
        { projectRoot: path.dirname(backend) }),
    },
  );
  const template = Template.fromStack(stack);
  template.resourceCountIs('AWS::BedrockAgentCore::Runtime', 1);
  template.resourceCountIs('AWS::BedrockAgentCore::RuntimeEndpoint', 1);
  template.resourceCountIs('AWS::BedrockAgentCore::Memory', 1);
  template.hasResourceProperties('AWS::BedrockAgentCore::Memory', {
    EventExpiryDuration: 90,
    MemoryStrategies: [Match.objectLike({ SemanticMemoryStrategy: {
      Name: 'Facts',
      Description: 'Extracts and stores facts from data analysis conversations',
      Namespaces: ['/facts/{actorId}'],
    }})],
  });
  template.hasResourceProperties('AWS::BedrockAgentCore::Runtime', {
    AgentRuntimeArtifact: { ContainerConfiguration: { ContainerUri: Match.anyValue() } },
    EnvironmentVariables: Match.objectLike({
      MEMORY_ID: Match.anyValue(), GATEWAY_URL: Match.anyValue(),
      READONLY_SECRET_ARN: Match.anyValue(), AURORA_RESOURCE_ARN: Match.anyValue(),
      QUESTION_ANSWERS_TABLE: Match.anyValue(), DATABASE_NAME: Match.anyValue(),
    }),
  });
  template.resourceCountIs('AWS::BedrockAgentCore::Gateway', 1);
  template.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
    ProtocolType: 'MCP', AuthorizerType: 'AWS_IAM',
    PolicyEngineConfiguration: { Arn: Match.anyValue(), Mode: 'ENFORCE' },
  });
  template.hasResourceProperties('AWS::BedrockAgentCore::GatewayTarget', {
    Name: 'DatabaseToolsTarget',
    TargetConfiguration: { Mcp: { Lambda: {
      LambdaArn: Match.anyValue(),
      ToolSchema: { InlinePayload: [
        Match.objectLike({ Name: 'get_tables_information' }),
        Match.objectLike({ Name: 'execute_sql_query' }),
      ] },
    } } },
  });
  template.resourceCountIs('AWS::BedrockAgentCore::PolicyEngine', 1);
  template.resourceCountIs('AWS::BedrockAgentCore::Policy', 4);
  template.resourceCountIs('AWS::BedrockAgentCore::Evaluator', 2);
  for (const level of ['TRACE', 'SESSION']) {
    template.hasResourceProperties('AWS::BedrockAgentCore::Evaluator', {
      Level: level,
      EvaluatorConfig: { LlmAsAJudge: Match.objectLike({
        Instructions: Match.anyValue(), RatingScale: { Numerical: Match.anyValue() },
      }) },
    });
  }
  template.resourceCountIs('AWS::RDS::DBCluster', 1);
  template.hasResourceProperties('AWS::RDS::DBCluster', {
    Engine: 'aurora-postgresql', EnableHttpEndpoint: true,
  });
  template.resourceCountIs('AWS::DynamoDB::Table', 1);
  template.hasResourceProperties('AWS::CloudFormation::CustomResource', {
    S3FileKey: 'video_games_sales_no_headers.csv', TableName: 'video_games_sales_units',
    ReadOnlySecretArn: Match.anyValue(),
  });
  template.resourceCountIs('AWS::Logs::Delivery', 4);
  for (const name of [
    'AgentRuntimeArn', 'MemoryId', 'GatewayUrl', 'PolicyEngineId',
    'QuestionAnswersTableName', 'ReadOnlySecretARN', 'AuroraServerlessDBClusterARN',
    'SqlAccuracyEvaluatorArn', 'ResponseQualityEvaluatorArn',
  ]) assert.ok(template.toJSON().Outputs[name], `Missing output ${name}`);
  const json = template.toJSON();
  const policyText = JSON.stringify(json.Resources);
  assert.ok(!policyText.includes('ecr:PutImage'), 'Runtime must not publish images');
  assert.ok(!policyText.includes('DeployTimestamp'), 'Data loader must be deterministic');
  if (process.env.SYNTH_EVIDENCE) fs.writeFileSync(process.env.SYNTH_EVIDENCE, JSON.stringify(json, null, 2));
  app.synth(); // Does not publish assets, build Docker images, or execute Custom Resources.
  console.log(`PASS: existing custom CDK stack invariants (${Object.keys(template.toJSON().Resources).length} resources)`);
  console.log(`aws-cdk-lib=${backendRequire('aws-cdk-lib/package.json').version}; typescript=${ts.version}`);
} finally {
  if (oldTsLoader) require.extensions['.ts'] = oldTsLoader;
  else delete require.extensions['.ts'];
  fs.rmSync(output, { recursive: true, force: true });
}
