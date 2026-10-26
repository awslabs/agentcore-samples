import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import {
  Annotations,
  ArnFormat,
  CfnOutput,
  CfnParameter,
  Duration,
  RemovalPolicy,
  Stack,
  StackProps,
} from 'aws-cdk-lib';
import * as glue from 'aws-cdk-lib/aws-glue';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';
import {
  directAccessResources,
  engineGlueRoleName,
  glueJobName,
  LoadConfig,
  migrationApiAdapter,
  MigrationConfig,
  ResolvedRegistryEndpoint,
  ResolvedRegistryMapping,
  stagingBucketName,
} from './config';
import { buildPythonLibraryWheel } from './glue-python-library';

export interface MigrationEngineStackProps extends StackProps {
  readonly config: MigrationConfig;
  readonly registryMappings: ResolvedRegistryMapping[];
}

// Documentation carried on the grouped <prefix>/config parameter. Because all run knobs live in
// one JSON document, this description is where an operator learns what each key does, its allowed
// values, and its safe default -- it is shown right next to the value in the SSM console.
const RUN_CONFIG_DESCRIPTION =
  'Agent Registry migration run settings. Edit the "key = value" lines in the value itself; ' +
  'every setting is documented inline. Values are validated when a job reads them.';

// Documentation carried on the grouped <prefix>/registries parameter.
const REGISTRIES_DESCRIPTION =
  'Registries to migrate: one mapping per line in the value itself, documented inline. ' +
  'Add a line to migrate another registry, delete a line to stop. Do not change a mapping ' +
  'mid-run: transform/load reconciles against the extract manifest and fails if a mapping differs.';

// The Glue version the jobs run on. It is here rather than in configuration because it is not a
// preference: 5.0 is the runtime whose interpreter is new enough for the SDK that carries the target
// service model (see GLUE_SDK_MODULES), and the jobs are validated against it.
const GLUE_VERSION = '5.0';

// The SDK the jobs install at startup. `agent-registry-control` -- the target's service model --
// first shipped in botocore 1.43.66, which requires Python 3.10 or newer, so the jobs use the Glue
// runtime whose interpreter satisfies that. Both jobs are single-threaded boto3 scripts and never
// create a SparkContext.
//
// Pinned exactly, not floored: an unpinned install resolves whatever is current when a migration
// runs, so two runs of one cutover could stage and load with different SDKs. Raising this is a
// deliberate edit, and `agent-registry-migration check` reports the version each side actually got.
const GLUE_SDK_MODULES = 'boto3==1.43.66,botocore==1.43.66';

export class MigrationEngineStack extends Stack {
  public readonly glueRole: iam.IRole;
  public readonly stagingBucket: s3.Bucket;
  public readonly workflow: glue.CfnWorkflow;
  public readonly extractJob: glue.CfnJob;
  public readonly transformLoadJob: glue.CfnJob;

  public constructor(scope: Construct, id: string, props: MigrationEngineStackProps) {
    super(scope, id, props);

    const { config } = props;
    this.stagingBucket = new s3.Bucket(this, 'StagingBucket', {
      bucketName: stagingBucketName(config.engine.stackName, config.engine.account, config.engine.region),
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      removalPolicy: RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
      lifecycleRules: [
        {
          id: 'ExpireRawStagedRecords',
          prefix: 'runs/',
          expiration: Duration.days(config.engine.stagingRetentionDays),
          noncurrentVersionExpiration: Duration.days(config.engine.stagingRetentionDays),
          abortIncompleteMultipartUploadAfter: Duration.days(7),
        },
        {
          id: 'ExpireReports',
          prefix: 'reports/',
          expiration: Duration.days(config.engine.reportRetentionDays),
          noncurrentVersionExpiration: Duration.days(config.engine.reportRetentionDays),
          abortIncompleteMultipartUploadAfter: Duration.days(7),
        },
        {
          // Run/attempt locks are pure bookkeeping: they exist to make a run id single-use while
          // the run matters. Without this rule one small object per run accumulates forever.
          // The other two things under state/ are deliberately NOT covered, because both must
          // outlive every run: state/watermarks/ is the incremental-load position, and state/idmap/
          // is which target record each source record became -- expiring that would make the next run
          // migrate every renamed record a second time.
          id: 'ExpireRunLocks',
          prefix: 'state/locks/',
          expiration: Duration.days(config.engine.stagingRetentionDays),
          noncurrentVersionExpiration: Duration.days(config.engine.stagingRetentionDays),
        },
        {
          // Bucket-wide, and noncurrent-only: superseded versions (re-published job artifacts
          // under app/, rewritten watermarks under state/) are otherwise kept indefinitely
          // because versioning is on. This rule never touches a current object.
          id: 'ExpireSupersededVersions',
          noncurrentVersionExpiration: Duration.days(config.engine.stagingRetentionDays),
          abortIncompleteMultipartUploadAfter: Duration.days(7),
        },
      ],
    });

    // Two IAM modes (see EngineConfig.createIamRoles):
    //  - true  (internal testing): create the execution role and grant it precisely what the
    //           jobs need, so a deployment is self-contained.
    //  - false (production): create NO roles. Import the customer-managed role and skip every
    //           grant; the required permissions are listed in docs/iam.md for the customer to
    //           attach themselves.
    const createIamRoles = config.engine.createIamRoles;
    if (createIamRoles) {
      this.glueRole = new iam.Role(this, 'GlueExecutionRole', {
        roleName: engineGlueRoleName(config),
        assumedBy: new iam.ServicePrincipal('glue.amazonaws.com'),
        description: 'Execution role for Agent Registry extract and transform/load Glue jobs',
      });
    } else {
      const suppliedRoleArn =
        config.engine.glueRoleArn ??
        new CfnParameter(this, 'GlueExecutionRoleArn', {
          type: 'String',
          description:
            'ARN of the customer-managed IAM role the Glue jobs run as. Required because ' +
            'engine.createIamRoles is false, so this stack creates no IAM roles. See docs/iam.md ' +
            'for the permissions this role needs.',
          allowedPattern: '^arn:[^:]*:iam::\\d{12}:role/.+$',
        }).valueAsString;
      // mutable:false keeps CDK from attaching policies to a role it does not own, so this
      // stack stays free of IAM mutations.
      this.glueRole = iam.Role.fromRoleArn(this, 'GlueExecutionRole', suppliedRoleArn, {
        mutable: false,
      });
      Annotations.of(this).addInfo(
        'engine.createIamRoles is false: no IAM roles or policies are created. Ensure the ' +
          'supplied Glue role has the permissions listed in docs/iam.md (S3 staging bucket, SSM ' +
          'configuration parameters, CloudWatch logs/metrics, registry read/write, and any ' +
          'sts:AssumeRole targets).',
      );
    }

    if (createIamRoles) {
      this.grantEnginePermissions(config, props.registryMappings);
    }

    const commonLibraryWheel = buildPythonLibraryWheel({
      sourceDir: path.join(process.cwd(), 'glue', 'common'),
      outputDir: path.join(process.cwd(), 'build', 'glue-lib'),
    });
    const wheelFileName = path.basename(commonLibraryWheel);
    const commonLibraryUrl = this.stagingBucket.s3UrlForObject(`app/${wheelFileName}`);
    const extractScriptUrl = this.stagingBucket.s3UrlForObject('app/extract.py');
    const transformLoadScriptUrl = this.stagingBucket.s3UrlForObject('app/transform_load.py');
    let appArtifacts: s3deploy.BucketDeployment | undefined;
    if (createIamRoles) {
      // Keep every runtime artifact -- the two Glue scripts and the library wheel -- in the
      // single staging bucket under app/. A BucketDeployment (rather than s3assets.Asset) is used
      // because Glue Python shell pip-installs the wheel from --extra-py-files and requires its
      // real PEP 427 filename, which a plain asset's content-hash key would not preserve.
      appArtifacts = new s3deploy.BucketDeployment(this, 'AppArtifactsDeployment', {
        sources: [
          s3deploy.Source.asset(path.dirname(commonLibraryWheel)),
          s3deploy.Source.asset(path.join(process.cwd(), 'glue'), {
            exclude: ['common', 'common/**', '**/__pycache__/**'],
          }),
        ],
        destinationBucket: this.stagingBucket,
        destinationKeyPrefix: 'app',
        prune: true,
      });
    } else {
      // A BucketDeployment provisions a Lambda (and therefore an IAM role), so it is skipped in
      // the no-IAM mode. Artifacts are published with the deployer's own credentials instead:
      // `agent-registry-migration deploy` publishes them with the deployer's own credentials.
      // The bucket name is only known after deployment, so point at the stack output that carries
      // the ready-to-run command rather than interpolating a token into this message.
      Annotations.of(this).addInfo(
        'Job artifacts are not uploaded by this stack because engine.createIamRoles is false. ' +
          '`agent-registry-migration deploy` uploads them for you straight after this deploy; if ' +
          'you deployed with the CDK directly, run that command before starting a job.',
      );
    }
    if (createIamRoles) {
      this.stagingBucket.grantRead(this.glueRole, 'app/*');
    }
    // Customer-facing run knobs: ONE grouped parameter holding a small JSON object, so an
    // operator opens a single place, sees every knob together with its documentation, and edits
    // it as one key/value document. (This replaced a parameter-per-knob layout, which spread the
    // run configuration over many separate parameters.) The jobs validate every value on read --
    // see validate_runtime_configuration in settings.py -- which replaces the per-parameter
    // allowedPattern regexes that a grouped JSON value cannot express.
    const runConfigValue = renderRunConfigDocument(config);
    const runConfigTier = parameterTierFor(this, `${config.engine.parameterPrefix}/config`, runConfigValue);
    new ssm.StringParameter(this, 'RunConfigParameter', {
      parameterName: `${config.engine.parameterPrefix}/config`,
      description: RUN_CONFIG_DESCRIPTION,
      stringValue: runConfigValue,
      tier: runConfigTier,
    });

    // Internal API adapter + transform rules — baked by this solution, not a customer input.
    // Kept in one parameter the customer never edits (managed entirely by CDK).
    const adapterValue = JSON.stringify({
      schemaVersion: 1,
      // What the deployment already knows, published so the commands do not have to be told it.
      // With this, `--staging-bucket` becomes optional: a job or CLI invocation that was given the
      // configuration prefix can look the bucket up here. Not part of the replay fingerprint,
      // which covers `transform` + `api.target` only.
      engine: {
        stagingBucket: this.stagingBucket.bucketName,
        parameterPrefix: config.engine.parameterPrefix,
        deploymentId: config.engine.deploymentId,
      },
      transform: {
        ...config.runtime.transform,
        implementationHash: migrationImplementationHash(),
      },
      api: migrationApiAdapter(),
    });
    const adapterTier = parameterTierFor(this, `${config.engine.parameterPrefix}/adapter`, adapterValue);
    new ssm.StringParameter(this, 'AdapterParameter', {
      parameterName: `${config.engine.parameterPrefix}/adapter`,
      description: 'Internal API adapter and transform configuration for Agent Registry migration (managed by CDK; do not edit).',
      stringValue: adapterValue,
      tier: adapterTier,
    });

    // Registry mappings: ONE grouped parameter holding the full list, rather than a parameter per
    // field per side per mapping (which produced 6+ parameters per registry). Operators read and
    // edit the whole routing table in one place, and the jobs validate it on read.
    const registriesValue = renderRegistriesDocument(props.registryMappings);
    const registriesTier = parameterTierFor(this, `${config.engine.parameterPrefix}/registries`, registriesValue);
    new ssm.StringParameter(this, 'RegistriesParameter', {
      parameterName: `${config.engine.parameterPrefix}/registries`,
      description: REGISTRIES_DESCRIPTION,
      stringValue: registriesValue,
      tier: registriesTier,
    });

    if (props.registryMappings.length === 0) {
      Annotations.of(this).addWarning(
        'No registry mappings are configured. Add mappings before starting the Glue workflow.',
      );
    }

    // The commands take no arguments only because they default to one prefix. A deployment that
    // publishes somewhere else still works, but every command then needs --config-prefix, and the
    // failure ("No migration deployment found at ...") would not point at the config file. Say it
    // here, at deploy time, instead.
    if (config.engine.parameterPrefix !== CLI_DEFAULT_PARAMETER_PREFIX) {
      Annotations.of(this).addWarning(
        `This deployment publishes its configuration at ${config.engine.parameterPrefix}, not the ` +
          `${CLI_DEFAULT_PARAMETER_PREFIX} the commands look for by default. Either drop ` +
          'engine.parameterPrefix and engine.deploymentId to use the default, or keep ' +
          'engine.parameterPrefix in your configuration file so the CLI reads the same place this ' +
          'deployment writes.',
      );
    }

    const commonJobArguments: Record<string, string> = {
      '--CONFIG_PREFIX': config.engine.parameterPrefix,
      '--STAGING_BUCKET': this.stagingBucket.bucketName,
      '--extra-py-files': commonLibraryUrl,
      '--enable-metrics': 'true',
      // The clients talk to the control planes via modeled boto3 operations, and both service models
      // come from the worker's SDK rather than from this repository, so the worker has to carry
      // agent-registry-control. No Glue image does: the Python shell runtime is boto3 1.21 and the
      // Glue 5.0 Spark runtime is boto3 1.34, both older than the release that first shipped the
      // model. Hence the pin -- see GLUE_SDK_MODULES for why these versions and not others.
      '--additional-python-modules': GLUE_SDK_MODULES,
      // Glue's own bookmarks are not how this tool resumes. An INCREMENTAL run reads the watermark
      // it wrote to the staging bucket, which an operator can inspect, override with --changed-after
      // and replay; a Glue bookmark is opaque state that would silently disagree with it. Set
      // explicitly rather than left to the default so that is a decision on the record.
      '--job-bookmark-option': 'job-bookmark-disable',
    };

    this.extractJob = new glue.CfnJob(this, 'ExtractJob', {
      name: glueJobName(config.engine.stackName, config.engine.account, config.engine.region, 'extract'),
      role: this.glueRole.roleArn,
      description: 'Extract preview Agent Registry records into replayable S3 JSONL staging',
      command: {
        name: 'glueetl',
        pythonVersion: '3',
        scriptLocation: extractScriptUrl,
      },
      glueVersion: GLUE_VERSION,
      defaultArguments: commonJobArguments,
      executionProperty: { maxConcurrentRuns: 1 },
      // workerType/numberOfWorkers rather than maxCapacity: the two are mutually exclusive on a
      // glueetl job, and setting both is a deploy-time CloudFormation error.
      workerType: config.engine.glueWorkerType,
      numberOfWorkers: config.engine.glueNumberOfWorkers,
      maxRetries: 0,
      timeout: config.engine.glueTimeoutMinutes,
    });

    this.transformLoadJob = new glue.CfnJob(this, 'TransformLoadJob', {
      name: glueJobName(config.engine.stackName, config.engine.account, config.engine.region, 'transform-load'),
      role: this.glueRole.roleArn,
      description: 'Transform preview records, idempotently load target records, and emit migration reports',
      command: {
        name: 'glueetl',
        pythonVersion: '3',
        scriptLocation: transformLoadScriptUrl,
      },
      glueVersion: GLUE_VERSION,
      defaultArguments: commonJobArguments,
      executionProperty: { maxConcurrentRuns: 1 },
      workerType: config.engine.glueWorkerType,
      numberOfWorkers: config.engine.glueNumberOfWorkers,
      maxRetries: 0,
      timeout: config.engine.glueTimeoutMinutes,
    });

    // The jobs read their script + wheel from app/, so ensure the artifacts are uploaded
    // before the jobs are created. In the no-IAM mode there is no deployment construct to wait
    // on because artifacts are published separately (see PublishArtifactsCommand).
    if (appArtifacts) {
      this.extractJob.node.addDependency(appArtifacts);
      this.transformLoadJob.node.addDependency(appArtifacts);
    }

    this.workflow = new glue.CfnWorkflow(this, 'MigrationWorkflow', {
      description: 'Extract followed by transform/load for Agent Registry preview-to-new-version migration',
      defaultRunProperties: {
        configurationPrefix: config.engine.parameterPrefix,
        stagingBucket: this.stagingBucket.bucketName,
      },
    });

    new glue.CfnTrigger(this, 'ExtractOnDemandTrigger', {
      type: 'ON_DEMAND',
      workflowName: this.workflow.ref,
      description: 'Starting trigger for the migration workflow',
      actions: [{ jobName: this.extractJob.ref }],
    });

    Annotations.of(this).addInfo(
      'Transform/load requires manual approval after reviewing the extract report. Only the ' +
        'on-demand Extract trigger is provisioned; start transform/load manually with the run ID.',
    );

    new CfnOutput(this, 'StagingBucketName', {
      value: this.stagingBucket.bucketName,
    });
    new CfnOutput(this, 'ConfigurationParameterPrefix', {
      value: config.engine.parameterPrefix,
    });
    new CfnOutput(this, 'GlueWorkflowName', {
      value: this.workflow.ref,
    });
    // Both job names are surfaced so an operator can poll a run (`aws glue get-job-runs`) or start
    // the load stage without digging through the console.
    new CfnOutput(this, 'ExtractJobName', {
      value: this.extractJob.ref,
      description: 'Glue job that reads the Preview registries (read-only)',
    });
    new CfnOutput(this, 'TransformLoadJobName', {
      value: this.transformLoadJob.ref,
      description: 'Glue job that transforms staged records and loads them into the target registries',
    });
    new CfnOutput(this, 'StartWorkflowCommand', {
      value: `aws glue start-workflow-run --name ${this.workflow.ref}`,
    });
    new CfnOutput(this, 'ExtractReportLocation', {
      value: `s3://${this.stagingBucket.bucketName}/reports/run_id=<run-id>/extract-summary.json`,
    });
    new CfnOutput(this, 'StartTransformCommand', {
      value: `aws glue start-job-run --job-name ${this.transformLoadJob.ref} --arguments '{"--RUN_ID":"<run-id>"}'`,
    });
    if (createIamRoles) {
      new CfnOutput(this, 'GlueExecutionRoleArn', {
        value: this.glueRole.roleArn,
        description: 'Glue execution role created by this stack',
      });
    } else {
      // A distinct construct id: in this mode 'GlueExecutionRoleArn' is the CloudFormation
      // *parameter* the operator supplies, so the output cannot reuse that id.
      new CfnOutput(this, 'GlueExecutionRoleArnInUse', {
        value: this.glueRole.roleArn,
        description: 'Customer-managed Glue execution role supplied to this stack (no roles were created)',
      });
      new CfnOutput(this, 'PublishArtifactsCommand', {
        value: 'agent-registry-migration deploy',
        description:
          'This stack cannot upload the Glue scripts and library wheel, because uploading would ' +
          'require creating an IAM role. The CLI deploy command does it instead, after every ' +
          'deploy and after any code change.',
      });
    }

    // Both rules check a Glue *security configuration*, which cdk-nag only evaluates for Spark jobs
    // -- the Python shell jobs these replaced were never assessed against them. A security
    // configuration requires a customer-managed KMS key, so honouring either would add a key (and
    // its cost, rotation and grants) to every deployment.
    for (const job of [this.extractJob, this.transformLoadJob]) {
      NagSuppressions.addResourceSuppressions(job, [
        {
          id: 'AwsSolutions-GL1',
          reason:
            'Glue log output is metadata about the migration -- run ids, record ids, counts and ' +
            'error messages -- and never record content, which travels between the control planes ' +
            'and the staging bucket only. CloudWatch Logs encrypts it at rest with an AWS-owned key; ' +
            'a security configuration would add a customer-managed KMS key without changing which ' +
            'data is exposed.',
        },
        {
          id: 'AwsSolutions-GL3',
          reason:
            'These jobs write no bookmark data to encrypt. Bookmarks are explicitly disabled in ' +
            'defaultArguments: incremental runs are driven by the tool\'s own watermark in the ' +
            'staging bucket, which is auditable and replayable, rather than by Glue state.',
        },
      ]);
    }

    NagSuppressions.addResourceSuppressions(this.stagingBucket, [
      {
        id: 'AwsSolutions-S1',
        reason:
          'This private, TLS-only, versioned bucket retains sensitive migration evidence. Enabling ' +
          'server access logging would require another retained bucket and change the deployment and ' +
          'storage workflow; migration operations are instead evidenced by Glue logs and run reports.',
      },
    ]);

    if (createIamRoles) {
      const directResources = directAccessResources(config);
      const directRecordWildcardFindings = [
        ...directResources.source,
        ...directResources.target,
      ]
        .filter(
          (resource) =>
            resource.endsWith('/record/*') && !resource.slice(0, -1).includes('*'),
        )
        .map((resource) => `Resource::${resource}`);
      const parameterPathArn = Stack.of(this).formatArn({
        service: 'ssm',
        resource: 'parameter',
        resourceName: `${config.engine.parameterPrefix.replace(/^\//, '')}/*`,
      });
      const glueLogGroupArn = Stack.of(this).formatArn({
        service: 'logs',
        resource: 'log-group',
        resourceName: '/aws-glue/*',
        arnFormat: ArnFormat.COLON_RESOURCE_NAME,
      });
      NagSuppressions.addResourceSuppressionsByPath(
        this,
        `/${this.glueRole.node.path}/DefaultPolicy/Resource`,
        [
          {
            id: 'AwsSolutions-IAM5',
            reason:
              'The Glue jobs need AWS-defined S3 action families and object wildcards only beneath ' +
              'the named staging-bucket prefixes, the configured SSM path, Glue log groups, and ' +
              'record children of explicitly configured registries.',
            appliesTo: [
              'Action::s3:GetObject*',
              'Action::s3:GetBucket*',
              'Action::s3:List*',
              'Action::s3:Abort*',
              {
                regex:
                  '/Resource::<StagingBucket[A-F0-9]+\\.Arn>\\/(app|runs|reports|state)\\/\\*/',
              },
              `Resource::${parameterPathArn}`,
              `Resource::${glueLogGroupArn}`,
              ...directRecordWildcardFindings,
            ],
          },
        ],
      );

      const bucketDeploymentProviderPath =
        `/${this.node.path}/Custom::CDKBucketDeployment8693BB64968944B69AAFB0CC9EB8756C`;
      NagSuppressions.addResourceSuppressionsByPath(
        this,
        bucketDeploymentProviderPath,
        [
          {
            id: 'AwsSolutions-IAM4',
            reason:
              'The singleton BucketDeployment provider is generated and owned by AWS CDK; its ' +
              'AWSLambdaBasicExecutionRole policy supplies only the provider Lambda log permissions.',
            appliesTo: [
              'Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole',
            ],
          },
          {
            id: 'AwsSolutions-IAM5',
            reason:
              'The AWS CDK BucketDeployment provider must list/read the bootstrap asset bucket and ' +
              'copy, prune, and abort multipart uploads under this stack staging bucket. These ' +
              'wildcards are limited to those two object namespaces.',
            appliesTo: [
              'Action::s3:GetObject*',
              'Action::s3:GetBucket*',
              'Action::s3:List*',
              'Action::s3:Abort*',
              'Action::s3:DeleteObject*',
              {
                regex: '/Resource::<StagingBucket[A-F0-9]+\\.Arn>\\/\\*/',
              },
              {
                regex:
                  '/Resource::arn:[^:]+:s3:::cdk-[a-z0-9]+-assets-[0-9]{12}-[a-z0-9-]+\\/\\*/',
              },
            ],
          },
          {
            id: 'AwsSolutions-L1',
            reason:
              'The singleton provider runtime is selected by the pinned AWS CDK BucketDeployment ' +
              'construct and cannot be changed without replacing the deployment provider workflow.',
          },
        ],
        true,
      );
    }
  }

  /**
   * Grant the Glue execution role exactly the permissions the two jobs need.
   *
   * Only called when `engine.createIamRoles` is true. This is the authoritative list of
   * required permissions -- the policy in docs/iam.md mirrors it, so keep the
   * two in sync when changing anything here.
   */
  private grantEnginePermissions(
    config: MigrationConfig,
    registryMappings: ResolvedRegistryMapping[],
  ): void {
    // S3 staging: read job artifacts, read/write staged run data, reports, and the engine state
    // under state/ (run locks, which lifecycle expires, and incremental watermarks, which it
    // deliberately keeps).
    this.stagingBucket.grantRead(this.glueRole, 'app/*');
    this.stagingBucket.grantRead(this.glueRole, 'runs/*');
    this.stagingBucket.grantRead(this.glueRole, 'reports/*');
    this.stagingBucket.grantRead(this.glueRole, 'state/*');
    this.stagingBucket.grantPut(this.glueRole, 'runs/*');
    this.stagingBucket.grantPut(this.glueRole, 'reports/*');
    this.stagingBucket.grantPut(this.glueRole, 'state/*');
    this.glueRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        sid: 'ReadMigrationConfiguration',
        actions: ['ssm:GetParameter', 'ssm:GetParameters', 'ssm:GetParametersByPath'],
        resources: [
          Stack.of(this).formatArn({
            service: 'ssm',
            resource: 'parameter',
            resourceName: `${config.engine.parameterPrefix.replace(/^\//, '')}/*`,
          }),
        ],
      }),
    );
    this.glueRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        sid: 'WriteGlueLogs',
        actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
        resources: [
          Stack.of(this).formatArn({
            service: 'logs',
            resource: 'log-group',
            resourceName: '/aws-glue/*',
            arnFormat: ArnFormat.COLON_RESOURCE_NAME,
          }),
        ],
      }),
    );
    const glueMetricsPolicy = new iam.Policy(this, 'GlueMetricsPolicy', {
      statements: [
        new iam.PolicyStatement({
          sid: 'PublishGlueMetrics',
          actions: ['cloudwatch:PutMetricData'],
          resources: ['*'],
          conditions: {
            StringEquals: {
              'cloudwatch:namespace': 'Glue',
            },
          },
        }),
      ],
    });
    this.glueRole.attachInlinePolicy(glueMetricsPolicy);
    NagSuppressions.addResourceSuppressions(
      glueMetricsPolicy,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason:
            'CloudWatch PutMetricData does not support resource-level ARNs; this dedicated policy ' +
            'is restricted to the Glue namespace by its cloudwatch:namespace condition.',
          appliesTo: ['Resource::*'],
        },
      ],
      true,
    );

    const assumableRoleArns = unique(
      registryMappings
        .flatMap((mapping) => [mapping.source.roleArn, mapping.target.roleArn])
        .filter((roleArn): roleArn is string => Boolean(roleArn)),
    );
    if (assumableRoleArns.length > 0) {
      this.glueRole.addToPrincipalPolicy(
        new iam.PolicyStatement({
          sid: 'AssumeRegistryAccessRoles',
          actions: ['sts:AssumeRole'],
          resources: assumableRoleArns,
        }),
      );
    }

    // Same-account mappings use the Glue execution identity directly. Cross-account
    // mappings retain the scoped AssumeRole path shown in the architecture diagram.
    const directResources = directAccessResources(config);
    if (directResources.source.length > 0 && config.iam.previewReadActions.length > 0) {
      this.glueRole.addToPrincipalPolicy(
        new iam.PolicyStatement({
          sid: 'ReadSameAccountPreviewRegistries',
          actions: config.iam.previewReadActions,
          resources: directResources.source,
        }),
      );
    }
    if (directResources.target.length > 0 && config.iam.targetWriteActions.length > 0) {
      this.glueRole.addToPrincipalPolicy(
        new iam.PolicyStatement({
          sid: 'WriteSameAccountTargetRegistries',
          actions: config.iam.targetWriteActions,
          resources: directResources.target,
        }),
      );
    }
  }

}

/** One run knob as it appears in the published `<prefix>/config` document. */
interface PublishedKnob {
  /**
   * The name the job reads it under (see `_build_load` in settings.py). Equal to the
   * `LoadConfig` field name for every knob except `mode`, which the document calls `loadMode`.
   */
  readonly name: string;
  /** Inline documentation, shown in the SSM console immediately above the value. */
  readonly comment: readonly string[];
  readonly value: string;
  /**
   * Whether this knob belongs under "the settings a migration actually uses" rather than
   * "rarely changed". Purely presentational.
   */
  readonly primary?: boolean;
}

/**
 * Every run knob this stack publishes, keyed by the `LoadConfig` field it comes from.
 *
 * The return type is `Record<keyof LoadConfig, PublishedKnob>` deliberately: TypeScript then
 * refuses to compile if a knob is added to `LoadConfig` and not published here. That is not
 * hypothetical -- `matchSourceStatus` was validated by `loadMigrationConfig` and read by
 * `_build_load`, but missing from this document, so a deployed run silently used the default
 * while the same configuration file was honoured by a local run. A missing knob is now a build
 * error rather than a divergence nobody sees.
 *
 * `test_settings.PublishedRunKnobsCoverEveryKnobTheJobReads` closes the loop from the other side,
 * checking this document against the knobs `_build_load` actually reads.
 */
function publishedRunKnobs(load: LoadConfig): Record<keyof LoadConfig, PublishedKnob> {
  return {
    dryRun: {
      name: 'dryRun',
      primary: true,
      comment: [
        'Whether a run writes to the target registry is decided per run, not stored here:',
        '  agent-registry-migration run          -- transform and report, writing NOTHING to the target registry',
        '  agent-registry-migration run --live   -- create the records',
        'This value is only the default for a job started outside the CLI (the Glue console, say),',
        'where there is nobody to state the intent. Leaving it true keeps that path safe.',
      ],
      value: String(load.dryRun),
    },
    mode: {
      name: 'loadMode',
      primary: true,
      comment: [
        'FULL = migrate every source record. INCREMENTAL = only records updated at/after the cutoff',
        '       below (or, when that is empty, since this mapping last loaded successfully).',
      ],
      value: load.mode,
    },
    changedAfter: {
      name: 'changedAfter',
      primary: true,
      comment: [
        'INCREMENTAL cutoff, ISO-8601 UTC (example: 2026-08-01T00:00:00Z). Empty = use the watermark.',
      ],
      value: load.changedAfter ?? '',
    },
    matchSourceStatus: {
      name: 'matchSourceStatus',
      primary: true,
      comment: [
        'true (default) = put each migrated record into the status its Preview record holds. The service',
        '       creates every record in DRAFT, and a DRAFT record is not returned by data-plane',
        '       search or the browsing APIs, so an approved record would arrive invisible.',
        'false = leave everything in DRAFT for review inside the target registry before publishing.',
      ],
      value: String(load.matchSourceStatus),
    },
    failOnRecordError: {
      name: 'failOnRecordError',
      comment: [
        'false (default) = skip a failed record and list it in the report; every other record still',
        '       loads. true = stop the run (nonzero exit) the moment any record fails.',
      ],
      value: String(load.failOnRecordError),
    },
    loadConcurrency: {
      name: 'loadConcurrency',
      comment: [
        'How many records the load stage processes at once (1-32). The per-record cost is waiting on',
        'the new Registry API, so raising this shortens a run roughly proportionally. Use 1 to debug.',
      ],
      value: String(load.loadConcurrency),
    },
    recordsPerObject: {
      name: 'recordsPerObject',
      comment: ['Records per staged JSONL object (1-10000).'],
      value: String(load.recordsPerObject),
    },
    dumpExtractedRecords: {
      name: 'dumpExtractedRecords',
      comment: [
        'true = also write a readable copy of every extracted record under the report. false saves',
        '       storage on very large estates (the id crosswalk and record comparison are unaffected).',
      ],
      value: String(load.dumpExtractedRecords),
    },
    allowReplayConfigurationDrift: {
      name: 'allowReplayConfigurationDrift',
      comment: [
        'EXPERT: allow a DRY RUN of records extracted before the transform/target settings changed.',
        'Live writes always require an exact match, whatever this says.',
      ],
      value: String(load.allowReplayConfigurationDrift),
    },
  };
}

/**
 * Render the run settings as an editable `key = value` document.
 *
 * A flat list with inline comments is far easier for an operator to change than a JSON blob:
 * every setting sits on its own line next to an explanation of what it does.
 */
function renderRunConfigDocument(config: MigrationConfig): string {
  const knobs = Object.values(publishedRunKnobs(config.runtime.load));
  const section = (heading: string, entries: PublishedKnob[]): string[] => [
    '# ============================================================================',
    `# ${heading}`,
    '# ============================================================================',
    '',
    ...entries.flatMap((knob) => [
      ...knob.comment.map((line) => `# ${line}`),
      `${knob.name} = ${knob.value}`,
      '',
    ]),
  ];
  return [
    '# Agent Registry migration -- run settings.',
    '#',
    '# Your configuration file is the source of truth: edit it and run',
    '#   agent-registry-migration deploy',
    '# which republishes this parameter. Editing it here works, and a redeploy overwrites it.',
    '',
    ...section(
      'The settings a migration actually uses.',
      knobs.filter((knob) => knob.primary),
    ),
    ...section(
      'Rarely changed. The defaults below are the right answer for almost every run.',
      knobs.filter((knob) => !knob.primary),
    ),
  ].join('\n');
}

/**
 * Render the routing table as an editable `key = value` document -- one line per registry.
 *
 * Adding a registry to the migration is appending a line; removing one is deleting its line.
 */
function renderRegistriesDocument(mappings: ResolvedRegistryMapping[]): string {
  const lines = [
    '# Agent Registry migration -- registries to migrate (one mapping per line).',
    '#',
    '# Format:',
    '#   <mappingId> = source=<accountId>/<region>/<registryId>, target=<accountId>/<region>/<registryId>',
    '#',
    '# Add a line to migrate another registry. Delete a line to stop migrating it.',
    '# Optional cross-account fields, appended to the same comma-separated list:',
    '#   source.roleArn=arn:aws:iam::<account>:role/<name>, source.externalId=<id>',
    '#   target.roleArn=arn:aws:iam::<account>:role/<name>, target.externalId=<id>',
    '#',
    '# Do not change a mapping mid-run: transform/load reconciles against the extract manifest',
    '# and fails if a mapping differs.',
    '',
  ];
  if (mappings.length === 0) {
    lines.push('# (no registries configured yet -- add a line above to start migrating)');
  }
  for (const mapping of mappings) {
    lines.push(`${mapping.id} = ${renderEndpointFields(mapping)}`);
  }
  lines.push('');
  return lines.join('\n');
}

function renderEndpointFields(mapping: ResolvedRegistryMapping): string {
  const fields: string[] = [];
  for (const side of ['source', 'target'] as const) {
    const endpoint = mapping[side];
    fields.push(`${side}=${endpoint.accountId}/${endpoint.region}/${endpoint.registryId}`);
  }
  // Optional fields follow the compact triples so the common case stays on one readable line.
  for (const side of ['source', 'target'] as const) {
    const endpoint = mapping[side] as unknown as Record<string, unknown>;
    for (const field of ['registryArn', 'roleArn', 'externalId']) {
      const value = endpoint[field];
      if (value !== undefined && value !== null && value !== '') {
        fields.push(`${side}.${field}=${String(value)}`);
      }
    }
  }
  return fields.join(', ');
}

// Directories whose contents never run inside a Glue job, so they must not influence the
// implementation hash. The hash is a replay fingerprint: transform/load refuses to live-load an
// extract that was staged under different migration logic. Hashing test code would make an
// in-flight extract un-loadable just because a test was added, which is a false positive.
const IMPLEMENTATION_HASH_EXCLUDED_DIRS = new Set(['tests', '__pycache__']);

function migrationImplementationHash(): string {
  const root = path.join(process.cwd(), 'glue');
  const files = collectPythonFiles(root).sort();
  const hash = crypto.createHash('sha256');
  for (const file of files) {
    hash.update(path.relative(root, file));
    hash.update('\0');
    hash.update(fs.readFileSync(file));
    hash.update('\0');
  }
  return hash.digest('hex');
}

/**
 * Collect the runtime Python files under `directory`, skipping test and cache directories.
 *
 * Symlinks are skipped, explicitly rather than incidentally. A `Dirent` for a symlink reports
 * `isFile()` and `isDirectory()` as both false, so this walker already ignored them; the
 * corresponding `_python_files` in adapter_defaults.py uses `Path.is_file()`/`is_dir()`, which
 * *follow* symlinks, and so had to be taught to skip them too. Both walkers must produce the same
 * hash for the same tree, because that hash is the replay fingerprint binding a staged extract to
 * the code that staged it -- if they disagree, an extract staged locally cannot be loaded by the
 * deployed job, and vice versa.
 */
function collectPythonFiles(directory: string): string[] {
  const files: string[] = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) {
      continue;
    }
    if (entry.isDirectory()) {
      if (IMPLEMENTATION_HASH_EXCLUDED_DIRS.has(entry.name)) {
        continue;
      }
      files.push(...collectPythonFiles(entryPath));
    } else if (entry.isFile() && entry.name.endsWith('.py')) {
      files.push(entryPath);
    }
  }
  return files;
}

/**
 * The prefix every command falls back to when `--config-prefix` is omitted.
 *
 * Must stay equal to `DEFAULT_CONFIG_PREFIX` in `glue/common/migration_common/settings.py`; a test
 * (`test_settings.CliDefaultPrefixMatchesTheStack`) reads both and fails if they drift.
 */
const CLI_DEFAULT_PARAMETER_PREFIX = '/agent-registry-migration/default';

const STANDARD_PARAMETER_LIMIT_BYTES = 4096;
const ADVANCED_PARAMETER_LIMIT_BYTES = 8192;

/**
 * Choose the SSM tier that fits, rather than failing once the mapping list outgrows Standard.
 *
 * Standard parameters cap at 4 KB, which is roughly 30 same-account mappings and fewer once
 * cross-account role ARNs are on the lines. For a tool whose premise is "many accounts and
 * regions", telling the operator to split the estate across deployments is a workaround, so a list
 * that does not fit is moved to the Advanced tier (8 KB) with the cost called out. Only beyond
 * Advanced does this become an error, and then it says what to do.
 */
function parameterTierFor(scope: Construct, name: string, value: string): ssm.ParameterTier {
  const size = Buffer.byteLength(value, 'utf8');
  if (size <= STANDARD_PARAMETER_LIMIT_BYTES) {
    return ssm.ParameterTier.STANDARD;
  }
  if (size <= ADVANCED_PARAMETER_LIMIT_BYTES) {
    Annotations.of(scope).addWarning(
      `${name} is ${size} bytes, over the ${STANDARD_PARAMETER_LIMIT_BYTES}-byte Standard limit, ` +
        'so it is created in the Advanced tier. Advanced parameters are billed per parameter per ' +
        'month and per API interaction; see SSM pricing. Reduce the mapping list, or split the ' +
        'estate across deployments (each with its own engine.deploymentId), to stay on Standard.',
    );
    return ssm.ParameterTier.ADVANCED;
  }
  throw new Error(
    `${name} is ${size} bytes; the SSM maximum is ${ADVANCED_PARAMETER_LIMIT_BYTES} bytes even in ` +
      'the Advanced tier. Split the registry mappings across more than one deployment, each with ' +
      'its own engine.deploymentId (which gives it its own parameterPrefix and staging bucket).',
  );
}

function unique(values: string[]): string[] {
  return [...new Set(values)].sort();
}


