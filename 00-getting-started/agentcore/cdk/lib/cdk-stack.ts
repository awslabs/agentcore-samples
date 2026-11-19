import { AgentCoreApplication, type AgentCoreApplicationProps } from '@aws/agentcore-cdk';
import {
  ArnFormat,
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

export class AgentCoreStack extends Stack {
  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);
    const application = new AgentCoreApplication(this, 'Application', props.application);

    // An empty project is synthesized during `agentcore remove all; agentcore deploy`.
    // Do not keep a policy resource alive after the sample Runtime is removed.
    if (!application.runtimes.has('support')) return;
    const runtime = application.runtime('support');

    // Read the synthesized asset address, not a Runtime attribute: this introduces
    // no Runtime -> role -> Runtime dependency cycle and limits reads to one ZIP.
    const resource = runtime.resource.node.defaultChild as agentcore.CfnRuntime;
    const artifact = this.resolve(resource.agentRuntimeArtifact);
    const source = artifact.codeConfiguration?.code?.s3;
    if (!source?.bucket || !source.prefix) {
      throw new Error('The sample permissions boundary requires a CodeZip artifact.');
    }
    const assetArn = Fn.join('', [
      'arn:', this.partition, ':s3:::', Token.asString(source.bucket), '/', source.prefix,
    ]);
    const logArn = this.formatArn({
      service: 'logs',
      resource: 'log-group',
      resourceName: `/aws/bedrock-agentcore/runtimes/${runtime.runtimeName}*`,
      arnFormat: ArnFormat.COLON_RESOURCE_NAME,
    });

    // CLI v1.0.3 grants broader model/configuration-bundle access by default.
    // A boundary limits the generated role without rewriting the CLI's policies.
    // Effective permissions are the INTERSECTION, not the union, of both policies.
    const boundary = new iam.ManagedPolicy(this, 'SampleRuntimeBoundary', {
      description: 'Limit the customer-support sample to one model and Runtime operation',
      statements: [
        new iam.PolicyStatement({
          actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
          resources: [this.formatArn({
            service: 'bedrock',
            account: '',
            resource: 'foundation-model',
            resourceName: 'amazon.nova-lite-v1:0',
            arnFormat: ArnFormat.SLASH_RESOURCE_NAME,
          })],
        }),
        new iam.PolicyStatement({
          actions: ['s3:GetObject', 's3:GetObjectVersion'],
          resources: [assetArn],
        }),
        // Logging is limited to this sample's Runtime, including its streams.
        new iam.PolicyStatement({
          actions: [
            'logs:CreateLogGroup',
            'logs:CreateLogStream',
            'logs:DescribeLogStreams',
            'logs:PutLogEvents',
            'logs:PutResourcePolicy', // Runtime configures delivery for this log group.
          ],
          resources: [logArn],
        }),
      ],
    });
    // Existing CDK bootstrap buckets may use a customer-managed KMS key.
    // This grants only S3-mediated decryption for this asset (or its bucket when
    // S3 Bucket Keys are enabled), without changing the key or bucket policy.
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
    iam.PermissionsBoundary.of(runtime.role).apply(boundary);
  }
}
