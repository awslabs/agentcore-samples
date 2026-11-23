import { AgentCoreApplication, type AgentCoreApplicationProps } from '@aws/agentcore-cdk';
import {
  ArnFormat,
  CfnOutput,
  Fn,
  Stack,
  Token,
  type StackProps,
  aws_iam as iam,
  aws_bedrockagentcore as agentcore,
} from 'aws-cdk-lib';
import { Construct } from 'constructs';

export interface AgentCoreStackProps extends StackProps {
  application: AgentCoreApplicationProps;
}

/**
 * v1 project resources. The temporal gateway, engine and policies remain owned
 * by setup.py, not this stack. Do not duplicate them in agentcore.json.
 */
export class AgentCoreStack extends Stack {
  public readonly application: AgentCoreApplication;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);
    this.application = new AgentCoreApplication(this, 'Application', props.application);
    new CfnOutput(this, 'StackNameOutput', {
      description: 'Name of the CloudFormation Stack',
      value: this.stackName,
    });

    // The MCP tools manipulate only simulated in-memory data. Bedrock is called
    // by the separate Node client, not this Runtime. Limit v1's broad generated
    // execution policy without rewriting the library or granting extra access.
    // Do not leave the boundary behind when the Runtime declaration is removed.
    if (!this.application.runtimes.has('banking_assistant_tools')) return;
    const runtime = this.application.runtime('banking_assistant_tools');
    const resource = runtime.resource.node.defaultChild as agentcore.CfnRuntime;
    // OTel execs its first argument. Use the executable script's explicit
    // relative path (no PATH assumption); CodeConfiguration permits two items.
    resource.addPropertyOverride('AgentRuntimeArtifact.CodeConfiguration.EntryPoint', [
      'opentelemetry-instrument',
      './main.py',
    ]);
    const artifact = this.resolve(resource.agentRuntimeArtifact);
    const source = artifact.codeConfiguration?.code?.s3;
    if (!source?.bucket || !source.prefix) {
      throw new Error('The banking Runtime boundary requires a CodeZip artifact.');
    }
    const assetArn = Fn.join('', ['arn:', this.partition, ':s3:::', Token.asString(source.bucket), '/', source.prefix]);
    const logArn = this.formatArn({
      service: 'logs',
      resource: 'log-group',
      resourceName: `/aws/bedrock-agentcore/runtimes/${runtime.runtimeName}-*`,
      arnFormat: ArnFormat.COLON_RESOURCE_NAME,
    });
    const boundary = new iam.ManagedPolicy(this, 'BankingRuntimeBoundary', {
      description: 'Banking MCP: own artifact/log writes, regional telemetry only',
      statements: [
        new iam.PolicyStatement({
          actions: ['s3:GetObject', 's3:GetObjectVersion'],
          resources: [assetArn],
        }),
        // Runtime IDs are <name>-<generated ID>; '_' extends a name, not an ID.
        new iam.PolicyStatement({
          actions: ['logs:CreateLogGroup', 'logs:DescribeLogStreams', 'logs:PutResourcePolicy'],
          resources: [logArn, `${logArn}:*`],
        }),
        new iam.PolicyStatement({
          actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
          resources: [`${logArn}:log-stream:*`],
        }),
        // X-Ray ingestion/sampling and metric publishing have no resource ARN
        // scope. Keep only the pinned Runtime contract, bound to this region;
        // metrics additionally require the bedrock-agentcore namespace.
        new iam.PolicyStatement({
          actions: [
            'xray:PutTraceSegments',
            'xray:PutTelemetryRecords',
            'xray:GetSamplingRules',
            'xray:GetSamplingTargets',
          ],
          resources: ['*'],
          conditions: { StringEquals: { 'aws:RequestedRegion': this.region } },
        }),
        new iam.PolicyStatement({
          actions: ['cloudwatch:PutMetricData'],
          resources: ['*'],
          conditions: {
            StringEquals: { 'aws:RequestedRegion': this.region, 'cloudwatch:namespace': 'bedrock-agentcore' },
          },
        }),
      ],
    });
    // Optional existing bootstrap asset key: S3-mediated decryption only, with
    // the object/bucket encryption context. Never change shared key policies.
    const assetKeyArn = process.env.AGENTCORE_SAMPLE_ASSET_KMS_KEY_ARN;
    if (assetKeyArn) {
      if (!/^arn:[a-z0-9-]+:kms:[a-z0-9-]+:\d{12}:key\/[a-zA-Z0-9-]+$/.test(assetKeyArn)) {
        throw new Error('AGENTCORE_SAMPLE_ASSET_KMS_KEY_ARN must be a KMS key ARN.');
      }
      const decrypt = new iam.PolicyStatement({
        actions: ['kms:Decrypt'],
        resources: [assetKeyArn],
        conditions: {
          StringEquals: {
            'kms:ViaService': `s3.${this.region}.${this.urlSuffix}`,
            'kms:EncryptionContext:aws:s3:arn': [
              assetArn,
              Fn.join('', ['arn:', this.partition, ':s3:::', Token.asString(source.bucket)]),
            ],
          },
        },
      });
      runtime.addToRolePolicy(decrypt);
      boundary.addStatements(decrypt);
    }
    // DescribeLogGroups is metadata-only and does not support resource-level
    // permissions. It cannot authorize writes to other Runtime log groups.
    const describeLogs = new iam.PolicyStatement({
      actions: ['logs:DescribeLogGroups'],
      resources: ['*'],
      conditions: { StringEquals: { 'aws:RequestedRegion': this.region } },
    });
    runtime.addToRolePolicy(describeLogs);
    boundary.addStatements(describeLogs);
    iam.PermissionsBoundary.of(runtime.role).apply(boundary);
  }
}
