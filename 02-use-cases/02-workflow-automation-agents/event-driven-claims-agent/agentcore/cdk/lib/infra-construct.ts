/**
 * InfraConstruct: Supplementary infrastructure for the Claims Agent.
 *
 * Creates resources that the AgentCore CLI cannot manage natively:
 * - DynamoDB tables (Policies, Claims, Reviews)
 * - S3 bucket (claims email inbox, EventBridge enabled)
 * - Lambda tool functions (6 tools wired to Gateway via lambdaArnMap)
 * - Trigger Lambda (EventBridge → Runtime invocation)
 * - EventBridge rule (S3 PutObject → Trigger)
 * - SNS topic (human review notifications)
 *
 * Cognito is managed externally (scripts/setup_cognito.sh) and its values
 * are passed via environment variables at synth time.
 *
 * Exposes `lambdaArnMap` for the parent stack to patch placeholder ARNs in agentcore.json.
 */

import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as lambda_ from 'aws-cdk-lib/aws-lambda';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as events from 'aws-cdk-lib/aws-events';
import * as eventsTargets from 'aws-cdk-lib/aws-events-targets';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import { Construct } from 'constructs';
import * as path from 'path';

export interface InfraConstructProps {
  /** Whether to destroy data on stack delete (default: true for dev) */
  destroyOnDelete?: boolean;
}

export class InfraConstruct extends Construct {
  /** Map of gateway target name → Lambda function ARN */
  public readonly lambdaArnMap: Record<string, string>;
  /** Trigger Lambda function (needs Runtime ARN injected after creation) */
  public readonly triggerFn: lambda_.Function;
  /** Cognito OIDC discovery URL — read from env, used for Gateway CUSTOM_JWT authorizer */
  public readonly cognitoDiscoveryUrl: string;
  /** Cognito app client ID — read from env, used for Gateway allowedClients */
  public readonly cognitoClientId: string;

  constructor(scope: Construct, id: string, props: InfraConstructProps = {}) {
    super(scope, id);

    const stack = cdk.Stack.of(this);
    const destroyOnDelete = props.destroyOnDelete ?? true;
    const removalPolicy = destroyOnDelete ? cdk.RemovalPolicy.DESTROY : cdk.RemovalPolicy.RETAIN;

    // Project root: process.cwd() is agentcore/cdk/ → go up 2 levels
    const projectRoot = path.resolve(process.cwd(), '..', '..');

    // ─── DynamoDB Tables ───────────────────────────────────────────

    const policiesTable = new dynamodb.Table(this, 'PoliciesTable', {
      tableName: `ClaimsAgent-${stack.stackName.split('-').pop() || 'dev'}-Policies`,
      partitionKey: { name: 'policy_number', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
    });

    const claimsTable = new dynamodb.Table(this, 'ClaimsTable', {
      tableName: `ClaimsAgent-${stack.stackName.split('-').pop() || 'dev'}-Claims`,
      partitionKey: { name: 'claim_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
    });

    // Add GSI for listing claims by status (avoids full table scan)
    claimsTable.addGlobalSecondaryIndex({
      indexName: 'status-index',
      partitionKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'created_at', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    const reviewsTable = new dynamodb.Table(this, 'ReviewsTable', {
      tableName: `ClaimsAgent-${stack.stackName.split('-').pop() || 'dev'}-Reviews`,
      partitionKey: { name: 'review_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
    });

    // Add GSI for finding reviews by claim_id (avoids full table scan)
    reviewsTable.addGlobalSecondaryIndex({
      indexName: 'claim-id-index',
      partitionKey: { name: 'claim_id', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // ─── SNS Topic (human review alerts) ──────────────────────────

    const reviewTopic = new sns.Topic(this, 'ReviewTopic', {
      topicName: 'ClaimsAgent-HumanReview',
    });

    // ─── S3 Bucket (claims email inbox) ───────────────────────────

    const inboxBucket = new s3.Bucket(this, 'InboxBucket', {
      bucketName: `claims-inbox-${stack.account}-${stack.region}`,
      removalPolicy,
      autoDeleteObjects: destroyOnDelete,
      eventBridgeEnabled: true,
    });

    // ─── Cognito values (externally managed — read from env) ──────
    // Cognito is created by scripts/setup_cognito.sh BEFORE deploy.
    // These values are passed via environment variables at synth time.
    this.cognitoDiscoveryUrl = process.env.COGNITO_DISCOVERY_URL || 'PLACEHOLDER_DISCOVERY_URL';
    this.cognitoClientId = process.env.AGENTCORE_GATEWAY_CLIENT_ID || 'PLACEHOLDER_CLIENT_ID';

    // ─── Lambda Tool Functions ─────────────────────────────────────

    const lambdasPath = path.join(projectRoot, 'lambdas');

    const policyLookupFn = new lambda_.Function(this, 'PolicyLookupFn', {
      functionName: 'ClaimsAgent-PolicyLookup',
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'policy_lookup')),
      environment: { POLICIES_TABLE: policiesTable.tableName },
      timeout: cdk.Duration.seconds(10),
    });
    policiesTable.grantReadData(policyLookupFn);

    const createClaimFn = new lambda_.Function(this, 'CreateClaimFn', {
      functionName: 'ClaimsAgent-CreateClaim',
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'create_claim')),
      environment: { CLAIMS_TABLE: claimsTable.tableName },
      timeout: cdk.Duration.seconds(10),
    });
    claimsTable.grantReadWriteData(createClaimFn);

    const humanReviewFn = new lambda_.Function(this, 'HumanReviewFn', {
      functionName: 'ClaimsAgent-HumanReview',
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'human_review')),
      environment: {
        REVIEWS_TABLE: reviewsTable.tableName,
        REVIEW_SNS_TOPIC_ARN: reviewTopic.topicArn,
      },
      timeout: cdk.Duration.seconds(10),
    });
    reviewsTable.grantReadWriteData(humanReviewFn);
    reviewTopic.grantPublish(humanReviewFn);

    const notificationFn = new lambda_.Function(this, 'NotificationFn', {
      functionName: 'ClaimsAgent-Notification',
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'notification')),
      environment: { SENDER_EMAIL: process.env.SENDER_EMAIL || 'noreply@example.com' },
      timeout: cdk.Duration.seconds(10),
    });
    notificationFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ses:SendEmail', 'ses:SendRawEmail'],
        resources: [`arn:aws:ses:${stack.region}:${stack.account}:identity/*`],
      })
    );

    const listPendingFn = new lambda_.Function(this, 'ListPendingFn', {
      functionName: 'ClaimsAgent-ListPending',
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'list_pending_claims')),
      environment: { CLAIMS_TABLE: claimsTable.tableName },
      timeout: cdk.Duration.seconds(10),
    });
    claimsTable.grantReadData(listPendingFn);

    const resolveClaimFn = new lambda_.Function(this, 'ResolveClaimFn', {
      functionName: 'ClaimsAgent-ResolveClaim',
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'resolve_claim')),
      environment: {
        CLAIMS_TABLE: claimsTable.tableName,
        REVIEWS_TABLE: reviewsTable.tableName,
      },
      timeout: cdk.Duration.seconds(10),
    });
    claimsTable.grantReadWriteData(resolveClaimFn);
    reviewsTable.grantReadWriteData(resolveClaimFn);

    // ─── Lambda ARN Map (gateway target name → function ARN) ──────

    this.lambdaArnMap = {
      'policy-lookup': policyLookupFn.functionArn,
      'create-claim': createClaimFn.functionArn,
      'human-review': humanReviewFn.functionArn,
      'notification': notificationFn.functionArn,
      'list-pending-claims': listPendingFn.functionArn,
      'resolve-claim': resolveClaimFn.functionArn,
    };

    // ─── Dead-Letter Queue (failed claim triggers) ────────────────

    const triggerDlq = new sqs.Queue(this, 'TriggerDLQ', {
      queueName: 'ClaimsAgent-TriggerDLQ',
      retentionPeriod: cdk.Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    // Alarm when failed claims land in the DLQ — ensures ops visibility
    new cloudwatch.Alarm(this, 'TriggerDLQAlarm', {
      alarmName: 'ClaimsAgent-FailedClaims',
      alarmDescription: 'Claims trigger DLQ has messages — failed claim processing needs attention',
      metric: triggerDlq.metricApproximateNumberOfMessagesVisible({
        period: cdk.Duration.minutes(1),
      }),
      threshold: 1,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // ─── Trigger Lambda (EventBridge → Runtime) ───────────────────

    this.triggerFn = new lambda_.Function(this, 'TriggerFn', {
      functionName: 'ClaimsAgent-Trigger',
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda_.Code.fromAsset(path.join(lambdasPath, 'trigger')),
      environment: {
        // AGENTCORE_RUNTIME_ARN injected by parent stack after Runtime is created
        AGENTCORE_RUNTIME_ARN: 'PENDING',
      },
      timeout: cdk.Duration.seconds(90),
      deadLetterQueue: triggerDlq,
      retryAttempts: 2,
    });
    inboxBucket.grantRead(this.triggerFn);

    // ─── EventBridge Rule: S3 PutObject → Trigger Lambda ──────────

    new events.Rule(this, 'ClaimInboxRule', {
      ruleName: 'ClaimsAgent-InboxTrigger',
      eventPattern: {
        source: ['aws.s3'],
        detailType: ['Object Created'],
        detail: {
          bucket: { name: [inboxBucket.bucketName] },
          object: { key: [{ prefix: 'claims-inbox/' }] },
        },
      },
      targets: [new eventsTargets.LambdaFunction(this.triggerFn)],
    });

    // ─── Outputs ──────────────────────────────────────────────────

    new cdk.CfnOutput(this, 'InboxBucketName', { value: inboxBucket.bucketName });
    new cdk.CfnOutput(this, 'ReviewTopicArn', { value: reviewTopic.topicArn });
    new cdk.CfnOutput(this, 'TriggerDLQUrl', { value: triggerDlq.queueUrl });
  }
}
