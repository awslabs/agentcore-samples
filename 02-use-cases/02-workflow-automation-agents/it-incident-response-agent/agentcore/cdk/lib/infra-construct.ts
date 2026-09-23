/**
 * InfraConstruct: Supplementary infrastructure that the AgentCore CLI cannot manage.
 *
 * Creates:
 * - DynamoDB tables (users, processes, tickets, changes)
 * - S3 buckets (KB documents, seed data)
 * - Lambda tool functions (wired to Gateway targets via lambdaArnMap)
 * - SNS topic + Trigger Lambda (event ingress)
 * - CloudWatch alarms (observability)
 *
 * Exposes a `lambdaArnMap` mapping target names to their Lambda function ARNs,
 * used by the parent stack to override placeholder ARNs in agentcore.json.
 */

import * as cdk from 'aws-cdk-lib';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3vectors from 'aws-cdk-lib/aws-s3vectors';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as lambda_ from 'aws-cdk-lib/aws-lambda';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as events from 'aws-cdk-lib/aws-events';
import * as lambdaEventSources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import * as path from 'path';

export interface InfraConstructProps {
  /** Pre-created Bedrock Knowledge Base ID (optional — if provided, skips KB creation) */
  kbId?: string;
  /** Whether to destroy data on stack delete (default: true for dev) */
  destroyOnDelete?: boolean;
  /** Existing EventBridge bus name. If not provided, a new bus is created. */
  eventBusName?: string;
  /** Existing Bedrock Guardrail ID. If not provided, a new guardrail is created. */
  guardrailId?: string;
  /** Skip KB creation entirely (useful if embedding model access not available) */
  skipKb?: boolean;
}

export class InfraConstruct extends Construct {
  /** Map of gateway target name → Lambda function ARN */
  public readonly lambdaArnMap: Record<string, string>;

  /** Map of gateway target name → Lambda function object (for granting invoke) */
  public readonly toolFunctions: Record<string, lambda_.Function>;

  /** DynamoDB Tickets table (needed for Runtime env vars) */
  public readonly ticketsTable: dynamodb.Table;

  /** SNS topic for ticket ingress */
  public readonly ticketsTopic: sns.Topic;

  /** Trigger Lambda function */
  public readonly triggerFn: lambda_.Function;

  /** EventBridge bus name for downstream event emission */
  public readonly eventBusName: string;

  /** Bedrock Guardrail ID for PII/content filtering (empty string if not configured) */
  public readonly guardrailId: string;

  /** Bedrock Knowledge Base ID (created or pre-existing; empty if skipped) */
  public readonly knowledgeBaseId: string = '';

  /** Bedrock KB data source ID (empty if KB skipped). Used to trigger ingestion on deploy. */
  public readonly knowledgeBaseDataSourceId: string = '';

  constructor(scope: Construct, id: string, props: InfraConstructProps = {}) {
    super(scope, id);

    const stack = cdk.Stack.of(this);
    const destroyOnDelete = props.destroyOnDelete ?? true;
    const removalPolicy = destroyOnDelete ? cdk.RemovalPolicy.DESTROY : cdk.RemovalPolicy.RETAIN;

    // ─── Bedrock Guardrail ─────────────────────────────────────────
    this.guardrailId = props.guardrailId ?? '';

    // Project root: process.cwd() is always agentcore/cdk/ (set by CDK CLI)
    // Go up 2 levels: agentcore/cdk/ → agentcore/ → project root
    const projectRoot = path.resolve(process.cwd(), '..', '..');

    // ─── DynamoDB Tables ───────────────────────────────────────────

    const usersTable = new dynamodb.Table(this, 'UsersTable', {
      partitionKey: { name: 'user_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy,
    });

    const processesTable = new dynamodb.Table(this, 'ProcessesTable', {
      partitionKey: { name: 'process_name', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy,
    });

    this.ticketsTable = new dynamodb.Table(this, 'TicketsTable', {
      partitionKey: { name: 'ticket_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy,
    });
    this.ticketsTable.addGlobalSecondaryIndex({
      indexName: 'byRequester',
      partitionKey: { name: 'requester_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'created_at', type: dynamodb.AttributeType.STRING },
    });

    const changesTable = new dynamodb.Table(this, 'ChangeRequestsTable', {
      partitionKey: { name: 'change_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy,
    });

    // ─── S3 Buckets ────────────────────────────────────────────────

    const kbBucket = new s3.Bucket(this, 'KbBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
      removalPolicy,
      autoDeleteObjects: destroyOnDelete,
    });

    new s3deploy.BucketDeployment(this, 'KbDocsDeploy', {
      sources: [s3deploy.Source.asset(path.join(projectRoot, 'kb-docs'))],
      destinationBucket: kbBucket,
      destinationKeyPrefix: 'runbooks/',
    });

    const seedBucket = new s3.Bucket(this, 'SeedBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy,
      autoDeleteObjects: destroyOnDelete,
    });

    const seedDataDeploy = new s3deploy.BucketDeployment(this, 'SeedDataDeploy', {
      sources: [s3deploy.Source.asset(path.join(projectRoot, 'seed-data'))],
      destinationBucket: seedBucket,
      destinationKeyPrefix: 'seed/',
    });

    // ─── Bedrock Knowledge Base ──────────────────────────────────────
    // Creates a fully-managed KB with S3 Vectors storage (zero prerequisites).
    // If KB_ID is provided, uses the pre-existing KB instead.
    // If SKIP_KB=true, skips KB creation entirely.
    const skipKb = props.skipKb ?? false;

    if (props.kbId) {
      // Use pre-existing KB
      this.knowledgeBaseId = props.kbId;
    } else if (!skipKb) {
      // Create a new KB backed by S3 Vectors.
      //
      // NOTE: AWS::Bedrock::KnowledgeBase with type S3_VECTORS requires a
      // PRE-EXISTING vector bucket + index — it does NOT auto-create them.
      // (The "quick create" auto-provisioning only exists in the console, not
      // in CloudFormation.) So we create the bucket and index here and pass
      // their ARNs/name into s3VectorsConfiguration.
      //
      // Index settings are IMMUTABLE after creation and must match the
      // embedding model:
      //   - dimension 1024     (Titan Text Embeddings v2 default)
      //   - distance metric cosine
      // NOTE: Do NOT pre-declare nonFilterableMetadataKeys here. For S3 Vectors,
      // nonFilterableMetadataKeys is optional — omitting it means all metadata
      // keys remain filterable by default, which is sufficient for Bedrock KB
      // ingestion. The AMAZON_BEDROCK_TEXT_CHUNK / AMAZON_BEDROCK_METADATA names
      // are an OpenSearch-backend convention, not applicable to S3 Vectors.
      const vectorBucket = new s3vectors.CfnVectorBucket(this, 'KbVectorBucket', {
        vectorBucketName: `it-incident-kb-${stack.account}-${stack.region}`,
      });
      vectorBucket.applyRemovalPolicy(removalPolicy);

      const vectorIndex = new s3vectors.CfnIndex(this, 'KbVectorIndex', {
        indexName: 'it-incident-agent-kb-index',
        vectorBucketName: vectorBucket.vectorBucketName!,
        dataType: 'float32',
        dimension: 1024,
        distanceMetric: 'cosine',
      });
      vectorIndex.addDependency(vectorBucket);
      vectorIndex.applyRemovalPolicy(removalPolicy);

      const kbRole = new iam.Role(this, 'KbExecutionRole', {
        assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com'),
        inlinePolicies: {
          KbPolicy: new iam.PolicyDocument({
            statements: [
              new iam.PolicyStatement({
                sid: 'ReadSourceBucket',
                actions: ['s3:GetObject', 's3:ListBucket'],
                resources: [kbBucket.bucketArn, `${kbBucket.bucketArn}/*`],
              }),
              new iam.PolicyStatement({
                sid: 'EmbeddingModel',
                actions: ['bedrock:InvokeModel'],
                resources: [`arn:aws:bedrock:${stack.region}::foundation-model/amazon.titan-embed-text-v2:0`],
              }),
              new iam.PolicyStatement({
                sid: 'S3VectorsAccess',
                actions: [
                  's3vectors:GetIndex',
                  's3vectors:QueryVectors',
                  's3vectors:GetVectors',
                  's3vectors:PutVectors',
                  's3vectors:ListVectors',
                  's3vectors:DeleteVectors',
                ],
                resources: [vectorBucket.attrVectorBucketArn, vectorIndex.attrIndexArn],
              }),
            ],
          }),
        },
      });

      const kb = new bedrock.CfnKnowledgeBase(this, 'KnowledgeBase', {
        name: 'it-incident-agent-kb',
        description: 'IT incident response runbooks and troubleshooting guides',
        roleArn: kbRole.roleArn,
        knowledgeBaseConfiguration: {
          type: 'VECTOR',
          vectorKnowledgeBaseConfiguration: {
            embeddingModelArn: `arn:aws:bedrock:${stack.region}::foundation-model/amazon.titan-embed-text-v2:0`,
          },
        },
        storageConfiguration: {
          type: 'S3_VECTORS',
          s3VectorsConfiguration: {
            vectorBucketArn: vectorBucket.attrVectorBucketArn,
            indexArn: vectorIndex.attrIndexArn,
            // NOTE: Do NOT include indexName here — it causes CloudFormation
            // oneOf ambiguity error. Only vectorBucketArn + indexArn are valid.
          },
        },
      });
      kb.addDependency(vectorIndex);

      // Data source pointing to the KB docs bucket
      const dataSource = new bedrock.CfnDataSource(this, 'KbDataSource', {
        knowledgeBaseId: kb.attrKnowledgeBaseId,
        name: 'runbooks-s3-source',
        description: 'IT runbook documents from S3',
        dataSourceConfiguration: {
          type: 'S3',
          s3Configuration: {
            bucketArn: kbBucket.bucketArn,
            inclusionPrefixes: ['runbooks/'],
          },
        },
      });
      dataSource.addDependency(kb);

      this.knowledgeBaseId = kb.attrKnowledgeBaseId;
      this.knowledgeBaseDataSourceId = dataSource.attrDataSourceId;

      new cdk.CfnOutput(this, 'KnowledgeBaseId', { value: kb.attrKnowledgeBaseId });
      new cdk.CfnOutput(this, 'KnowledgeBaseArn', { value: kb.attrKnowledgeBaseArn });
      new cdk.CfnOutput(this, 'DataSourceId', { value: dataSource.attrDataSourceId });
    } else {
      this.knowledgeBaseId = '';
    }

    // ─── EventBridge Bus ──────────────────────────────────────────

    if (props.eventBusName) {
      // Use an existing bus provided by the user
      this.eventBusName = props.eventBusName;
    } else {
      // Create a dedicated bus for this agent's events (include stack name for uniqueness)
      const busName = `it-incident-agent-${stack.stackName}`.substring(0, 64);
      const eventBus = new events.EventBus(this, 'AgentEventBus', {
        eventBusName: busName,
      });
      eventBus.applyRemovalPolicy(removalPolicy);
      this.eventBusName = eventBus.eventBusName;

      new cdk.CfnOutput(this, 'EventBusName', { value: eventBus.eventBusName });
      new cdk.CfnOutput(this, 'EventBusArn', { value: eventBus.eventBusArn });
    }

    // ─── Bedrock Guardrail ────────────────────────────────────────

    if (props.guardrailId) {
      this.guardrailId = props.guardrailId;
    } else {
      // Create a guardrail for PII filtering and content safety on event payloads
      const guardrail = new cdk.CfnResource(this, 'AgentGuardrail', {
        type: 'AWS::Bedrock::Guardrail',
        properties: {
          Name: 'it-incident-agent-guardrail',
          Description: 'Filters PII and inappropriate content from ticket payloads before model invocation',
          BlockedInputMessaging:
            'This ticket contains content that cannot be processed. Please remove sensitive information and resubmit.',
          BlockedOutputsMessaging: 'The response was filtered for safety.',
          SensitiveInformationPolicyConfig: {
            PiiEntitiesConfig: [
              { Type: 'EMAIL', Action: 'ANONYMIZE' },
              { Type: 'PHONE', Action: 'ANONYMIZE' },
              { Type: 'US_SOCIAL_SECURITY_NUMBER', Action: 'BLOCK' },
              { Type: 'CREDIT_DEBIT_CARD_NUMBER', Action: 'BLOCK' },
              { Type: 'AWS_ACCESS_KEY', Action: 'BLOCK' },
              { Type: 'AWS_SECRET_KEY', Action: 'BLOCK' },
              { Type: 'IP_ADDRESS', Action: 'ANONYMIZE' },
              { Type: 'NAME', Action: 'ANONYMIZE' },
            ],
          },
          ContentPolicyConfig: {
            FiltersConfig: [
              { Type: 'SEXUAL', InputStrength: 'HIGH', OutputStrength: 'HIGH' },
              { Type: 'VIOLENCE', InputStrength: 'HIGH', OutputStrength: 'HIGH' },
              { Type: 'HATE', InputStrength: 'HIGH', OutputStrength: 'HIGH' },
              { Type: 'INSULTS', InputStrength: 'HIGH', OutputStrength: 'HIGH' },
              { Type: 'MISCONDUCT', InputStrength: 'HIGH', OutputStrength: 'HIGH' },
              { Type: 'PROMPT_ATTACK', InputStrength: 'HIGH', OutputStrength: 'NONE' },
            ],
          },
        },
      });
      this.guardrailId = guardrail.getAtt('GuardrailId').toString();

      new cdk.CfnOutput(this, 'GuardrailId', { value: this.guardrailId });
    }

    // ─── Tool Lambda Functions ─────────────────────────────────────

    const lambdasPath = path.join(projectRoot, 'lambdas');
    const commonEnv: Record<string, string> = {
      USERS_TABLE: usersTable.tableName,
      PROCESSES_TABLE: processesTable.tableName,
      TICKETS_TABLE: this.ticketsTable.tableName,
      CHANGES_TABLE: changesTable.tableName,
      LOG_LEVEL: 'INFO',
    };

    const lookupUserFn = new lambda_.Function(this, 'LookupUserFn', {
      runtime: lambda_.Runtime.PYTHON_3_11,
      handler: 'tools.lookup_user.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      memorySize: 256,
      code: lambda_.Code.fromAsset(lambdasPath),
      environment: commonEnv,
      logRetention: logs.RetentionDays.TWO_WEEKS,
    });
    usersTable.grantReadData(lookupUserFn);
    this.ticketsTable.grantReadData(lookupUserFn);

    const getProcessInfoFn = new lambda_.Function(this, 'GetProcessInfoFn', {
      runtime: lambda_.Runtime.PYTHON_3_11,
      handler: 'tools.get_process_info.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      memorySize: 256,
      code: lambda_.Code.fromAsset(lambdasPath),
      environment: commonEnv,
      logRetention: logs.RetentionDays.TWO_WEEKS,
    });
    processesTable.grantReadData(getProcessInfoFn);

    const createChangeRequestFn = new lambda_.Function(this, 'CreateChangeRequestFn', {
      runtime: lambda_.Runtime.PYTHON_3_11,
      handler: 'tools.create_change_request.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      memorySize: 256,
      code: lambda_.Code.fromAsset(lambdasPath),
      environment: commonEnv,
      logRetention: logs.RetentionDays.TWO_WEEKS,
    });
    changesTable.grantWriteData(createChangeRequestFn);
    usersTable.grantReadWriteData(createChangeRequestFn);

    // Build the ARN map (target name → Lambda ARN)
    this.lambdaArnMap = {
      'lookup-user': lookupUserFn.functionArn,
      'get-process-info': getProcessInfoFn.functionArn,
      'create-change-request': createChangeRequestFn.functionArn,
    };

    // Build the function map (target name → Lambda Function object)
    this.toolFunctions = {
      'lookup-user': lookupUserFn,
      'get-process-info': getProcessInfoFn,
      'create-change-request': createChangeRequestFn,
    };

    // query_kb — only if KB is available (created or pre-existing)
    if (this.knowledgeBaseId) {
      const queryKbFn = new lambda_.Function(this, 'QueryKbFn', {
        runtime: lambda_.Runtime.PYTHON_3_11,
        handler: 'tools.query_kb.lambda_handler',
        timeout: cdk.Duration.seconds(30),
        memorySize: 256,
        code: lambda_.Code.fromAsset(lambdasPath),
        environment: { ...commonEnv, KB_ID: this.knowledgeBaseId },
        logRetention: logs.RetentionDays.TWO_WEEKS,
      });
      queryKbFn.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ['bedrock:Retrieve'],
          resources: [`arn:aws:bedrock:${stack.region}:${stack.account}:knowledge-base/${this.knowledgeBaseId}`],
        })
      );
      this.lambdaArnMap['query-kb'] = queryKbFn.functionArn;
      this.toolFunctions['query-kb'] = queryKbFn;
    }

    // ─── SNS Topic + Trigger Lambda ───────────────────────────────

    const dlq = new sqs.Queue(this, 'TriggerDLQ', {
      retentionPeriod: cdk.Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    this.ticketsTopic = new sns.Topic(this, 'TicketsTopic', {
      displayName: 'IT Incident Tickets',
    });

    this.triggerFn = new lambda_.Function(this, 'TicketEventHandlerFn', {
      runtime: lambda_.Runtime.PYTHON_3_11,
      handler: 'trigger.ticket_event_handler.lambda_handler',
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      code: lambda_.Code.fromAsset(lambdasPath),
      deadLetterQueue: dlq,
      // OBSERVABILITY: Active X-Ray tracing so the SNS → trigger → runtime hop
      // appears in the X-Ray service map (otherwise the trace starts at the runtime).
      tracing: lambda_.Tracing.ACTIVE,
      environment: {
        TICKETS_TABLE: this.ticketsTable.tableName,
        // AGENT_RUNTIME_ARN will be set after AgentCore constructs are created
        AGENT_RUNTIME_ARN: 'PENDING',
        LOG_LEVEL: 'INFO',
      },
      logRetention: logs.RetentionDays.TWO_WEEKS,
    });

    this.ticketsTable.grantWriteData(this.triggerFn);
    // InvokeAgentRuntime permission is added in cdk-stack.ts after the Runtime ARN
    // is resolved — scoped to the specific runtime rather than using a wildcard.

    this.triggerFn.addEventSource(new lambdaEventSources.SnsEventSource(this.ticketsTopic));

    // ─── Observability ─────────────────────────────────────────────

    new cloudwatch.Alarm(this, 'DLQDepthAlarm', {
      metric: dlq.metricApproximateNumberOfMessagesVisible({
        period: cdk.Duration.minutes(1),
      }),
      threshold: 1,
      evaluationPeriods: 1,
      alarmDescription: 'One or more tickets failed processing and landed in the DLQ',
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    new cloudwatch.Alarm(this, 'TriggerErrorAlarm', {
      metric: this.triggerFn.metricErrors({ period: cdk.Duration.minutes(5) }),
      threshold: 1,
      evaluationPeriods: 1,
      alarmDescription: 'Trigger Lambda encountered errors',
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // ─── DynamoDB Seeder (Custom Resource) ────────────────────────

    const seederFn = new lambda_.Function(this, 'SeederFn', {
      runtime: lambda_.Runtime.PYTHON_3_11,
      handler: 'infra.seeder.handler',
      timeout: cdk.Duration.minutes(3),
      memorySize: 256,
      code: lambda_.Code.fromAsset(lambdasPath),
      logRetention: logs.RetentionDays.TWO_WEEKS,
    });
    seedBucket.grantRead(seederFn);
    usersTable.grantWriteData(seederFn);
    processesTable.grantWriteData(seederFn);

    // Optional: KB ingestion permissions
    if (this.knowledgeBaseId) {
      seederFn.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ['bedrock:StartIngestionJob', 'bedrock:GetIngestionJob'],
          resources: [`arn:aws:bedrock:${stack.region}:${stack.account}:knowledge-base/*`],
        })
      );
    }

    // Use CDK Provider framework to guarantee cfnresponse is always sent
    const seederProvider = new cr.Provider(this, 'SeederProvider', {
      onEventHandler: seederFn,
    });

    const seederCr = new cdk.CustomResource(this, 'TriggerSeeder', {
      serviceToken: seederProvider.serviceToken,
      properties: {
        SeedBucket: seedBucket.bucketName,
        UsersTable: usersTable.tableName,
        ProcessesTable: processesTable.tableName,
        ...(this.knowledgeBaseId ? { KnowledgeBaseId: this.knowledgeBaseId } : {}),
        ...(this.knowledgeBaseDataSourceId ? { DataSourceId: this.knowledgeBaseDataSourceId } : {}),
        // Bump version to force re-seed on deploy (v3: DataSourceId now passed →
        // forces the custom resource to re-run and trigger KB ingestion on
        // already-deployed stacks).
        Version: '3',
      },
    });

    // Ensure seed data is uploaded to S3 BEFORE the seeder runs
    seederCr.node.addDependency(seedDataDeploy);

    // ─── Outputs ───────────────────────────────────────────────────

    new cdk.CfnOutput(this, 'TicketsTopicArn', { value: this.ticketsTopic.topicArn });
    new cdk.CfnOutput(this, 'TicketsTableName', { value: this.ticketsTable.tableName });
    new cdk.CfnOutput(this, 'KbBucketName', { value: kbBucket.bucketName });
    new cdk.CfnOutput(this, 'DLQUrl', { value: dlq.queueUrl });
  }
}
