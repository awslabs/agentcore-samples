/**
 * CDK Stack for AgentCore Strands Data Analyst Assistant
 * 
 * Infrastructure for a data analyst assistant powered by Amazon Bedrock AgentCore.
 * Components:
 * - Aurora PostgreSQL database containing video games sales data
 * - DynamoDB tables for query results
 * - VPC with networking and security configuration
 * - IAM roles and permissions for AgentCore
 * - S3 bucket for data imports
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as rds from "aws-cdk-lib/aws-rds";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as iam from "aws-cdk-lib/aws-iam";
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as path from 'path';
import { aws_bedrockagentcore as bedrockagentcore } from 'aws-cdk-lib';

export class CdkDataAnalystAssistantAgentcoreStrandsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ================================
    // STACK PARAMETERS
    // ================================

    // Name of the Aurora PostgreSQL database
    const databaseName = new cdk.CfnParameter(this, "DatabaseName", {
      type: "String",
      description: "The database name",
      default: "video_games_sales",
    });

    // Bedrock model ID for the agent
    const bedrockModelId = new cdk.CfnParameter(this, "BedrockModelId", {
      type: "String",
      description: "The Bedrock model ID for the agent",
      default: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    });

    // ================================
    // DYNAMODB TABLES
    // ================================

    // DynamoDB table containing SQL query results from the agent
    const rawQueryResults = new dynamodb.Table(this, "RawQueryResults", {
      partitionKey: {
        name: "id",
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: "my_timestamp",
        type: dynamodb.AttributeType.NUMBER,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: cdk.RemovalPolicy.DESTROY
    });

    // ================================
    // VPC AND NETWORKING
    // ================================

    // VPC containing public and private subnets for secure database access
    const vpc = new ec2.Vpc(this, "AssistantVPC", {
      ipAddresses: ec2.IpAddresses.cidr("10.0.0.0/21"),
      maxAzs: 3,
      natGateways: 1,
      subnetConfiguration: [
        {
          subnetType: ec2.SubnetType.PUBLIC,
          name: "Ingress",
          cidrMask: 24,
        },
        {
          cidrMask: 24,
          name: "Private",
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
        },
      ],
    });

    // Gateway endpoints providing cost-effective access to AWS services
    vpc.addGatewayEndpoint("S3Endpoint", {
      service: ec2.GatewayVpcEndpointAwsService.S3,
      subnets: [{ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }],
    });

    vpc.addGatewayEndpoint("DynamoDBEndpoint", {
      service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
      subnets: [{ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }],
    });

    // ================================
    // DATABASE INFRASTRUCTURE
    // ================================

    // Security group controlling access to Aurora PostgreSQL cluster (via RDS Data API)
    const sg_db = new ec2.SecurityGroup(
      this,
      "AssistantDBSecurityGroup",
      {
        vpc: vpc,
        allowAllOutbound: true,
        description: "Security group for Aurora PostgreSQL cluster accessed via RDS Data API"
      }
    );

    // Allow inbound PostgreSQL traffic from the same security group
    sg_db.addIngressRule(
      sg_db,
      ec2.Port.tcp(5432),
      "Allow PostgreSQL access from within the same security group"
    );

    // Database credentials stored in AWS Secrets Manager
    const databaseUsername = "postgres";
    const secret = new rds.DatabaseSecret(this, "AssistantSecret", {
      username: databaseUsername,
    });

    // Read-only user secret for least-privilege database access
    const readOnlySecret = new rds.DatabaseSecret(this, "ReadOnlySecret", {
      username: "readonly_user",
    });

    // IAM role enabling Aurora S3 access for data imports
    const auroraS3Role = new iam.Role(this, "AuroraS3Role", {
      assumedBy: new iam.ServicePrincipal("rds.amazonaws.com"),
    });

    // ================================
    // S3 STORAGE
    // ================================

    // S3 bucket containing data for import into Aurora PostgreSQL
    const importBucket = new s3.Bucket(this, "ImportBucket", {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      lifecycleRules: [
        {
          expiration: cdk.Duration.days(7), // Auto-delete objects after 7 days
        },
      ],
    });

    // Grant S3 access to the Aurora role for data imports
    auroraS3Role.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"],
        resources: [
          importBucket.bucketArn,
          `${importBucket.bucketArn}/*`,
        ],
      })
    );

    // Aurora PostgreSQL Serverless v2 cluster containing video games sales data
    let cluster = new rds.DatabaseCluster(this, "AssistantCluster", {
      engine: rds.DatabaseClusterEngine.auroraPostgres({
        version: rds.AuroraPostgresEngineVersion.VER_17_4,
      }),
      writer: rds.ClusterInstance.serverlessV2("writer"),
      serverlessV2MinCapacity: 2,
      serverlessV2MaxCapacity: 4,
      defaultDatabaseName: databaseName.valueAsString,
      vpc,
      vpcSubnets: {
        subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
      },
      securityGroups: [sg_db],
      credentials: rds.Credentials.fromSecret(secret),
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      enableDataApi: true,
      s3ImportRole: auroraS3Role,
      storageEncrypted: true, // Ensure storage encryption
    });

    // ================================
    // AGENTCORE IAM ROLE & PERMISSIONS
    // ================================

    // IAM role with comprehensive permissions for Amazon Bedrock AgentCore
    const agentCoreRole = new iam.Role(this, 'AgentCoreMyRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      inlinePolicies: {
        'AgentCoreExecutionPolicy': new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              sid: 'ECRImageAccess',
              effect: iam.Effect.ALLOW,
              actions: [
                'ecr:BatchCheckLayerAvailability',
                'ecr:BatchGetImage',
                'ecr:GetDownloadUrlForLayer',
                'ecr:PutImage',
                'ecr:InitiateLayerUpload',
                'ecr:UploadLayerPart',
                'ecr:CompleteLayerUpload',
              ],
              resources: [
                `arn:aws:ecr:${this.region}:${this.account}:repository/*`
              ]
            }),
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                'logs:DescribeLogStreams',
                'logs:CreateLogGroup'
              ],
              resources: [
                `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`
              ]
            }),
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                'logs:DescribeLogGroups'
              ],
              resources: [
                `arn:aws:logs:${this.region}:${this.account}:log-group:*`
              ]
            }),
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                'logs:CreateLogStream',
                'logs:PutLogEvents'
              ],
              resources: [
                `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*`
              ]
            }),
            new iam.PolicyStatement({
              sid: 'ECRTokenAccess',
              effect: iam.Effect.ALLOW,
              actions: [
                'ecr:GetAuthorizationToken'
              ],
              resources: ['*']
            }),
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                'xray:PutTraceSegments',
                'xray:PutTelemetryRecords',
                'xray:GetSamplingRules',
                'xray:GetSamplingTargets'
              ],
              resources: ['*']
            }),
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['cloudwatch:PutMetricData'],
              resources: ['*'],
              conditions: {
                StringEquals: {
                  'cloudwatch:namespace': 'bedrock-agentcore'
                }
              }
            }),
            new iam.PolicyStatement({
              sid: 'GetAgentAccessToken',
              effect: iam.Effect.ALLOW,
              actions: [
                'bedrock-agentcore:GetWorkloadAccessToken',
                'bedrock-agentcore:GetWorkloadAccessTokenForJWT',
                'bedrock-agentcore:GetWorkloadAccessTokenForUserId'
              ],
              resources: [
                `arn:aws:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default`,
                `arn:aws:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default/workload-identity/*`
              ]
            }),
            new iam.PolicyStatement({
              sid: 'BedrockModelInvocation',
              effect: iam.Effect.ALLOW,
              actions: [
                'bedrock:InvokeModel',
                'bedrock:InvokeModelWithResponseStream'
              ],
              resources: [
                'arn:aws:bedrock:*::foundation-model/*',
                `arn:aws:bedrock:${this.region}:${this.account}:*`
              ]
            }),
            // New permissions for RDS Data API
            new iam.PolicyStatement({
              sid: 'RDSDataAPIAccess',
              effect: iam.Effect.ALLOW,
              actions: [
                'rds-data:ExecuteStatement',
                'rds-data:BatchExecuteStatement'
              ],
              resources: [
                cluster.clusterArn
              ]
            }),
            // New permissions for Secrets Manager
            new iam.PolicyStatement({
              sid: 'SecretsManagerAccess',
              effect: iam.Effect.ALLOW,
              actions: [
                'secretsmanager:GetSecretValue'
              ],
              resources: [
                secret.secretArn,
                readOnlySecret.secretArn
              ]
            }),
            // Permissions for DynamoDB
            new iam.PolicyStatement({
              sid: 'DynamoDBTableAccess',
              effect: iam.Effect.ALLOW,
              actions: [
                'dynamodb:Query',
                'dynamodb:Scan',
                'dynamodb:GetItem',
                'dynamodb:PutItem',
                'dynamodb:UpdateItem'
              ],
              resources: [
                rawQueryResults.tableArn
              ]
            }),
            // Permissions for AgentCore Memory
            new iam.PolicyStatement({
              sid: 'BedrockAgentCoreMemoryAccess',
              effect: iam.Effect.ALLOW,
              actions: [
                'bedrock-agentcore:GetMemoryRecord',
                'bedrock-agentcore:GetMemory',
                'bedrock-agentcore:RetrieveMemoryRecords',
                'bedrock-agentcore:DeleteMemoryRecord',
                'bedrock-agentcore:ListMemoryRecords',
                'bedrock-agentcore:CreateEvent',
                'bedrock-agentcore:ListSessions',
                'bedrock-agentcore:ListEvents',
                'bedrock-agentcore:GetEvent'
              ],
              resources: [
                `*`
              ]
            }),
            new iam.PolicyStatement({
              sid: 'BedrockModelInvocationMemory',
              effect: iam.Effect.ALLOW,
              actions: [
                'bedrock:InvokeModel',
                'bedrock:InvokeModelWithResponseStream'
              ],
              resources: [
                'arn:aws:bedrock:*::foundation-model/*',
                'arn:aws:bedrock:*:*:inference-profile/*'
              ]
            }),
          ]
        })
      }
    });

    // Add the specific trust relationship with sts:TagSession permission
    (agentCoreRole.node.defaultChild as iam.CfnRole).addPropertyOverride(
      'AssumeRolePolicyDocument',
      {
        Version: '2012-10-17',
        Statement: [
          {
            Sid: 'Statement1',
            Effect: 'Allow',
            Principal: {
              Service: 'bedrock-agentcore.amazonaws.com'
            },
            Action: [
              'sts:AssumeRole',
              'sts:TagSession'
            ]
          }
        ]
      }
    );

    // Add additional RDS permissions to Aurora S3 role
    auroraS3Role.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          "rds:CreateDBSnapshot",
          "rds:CreateDBClusterSnapshot",
          "rds:RestoreDBClusterFromSnapshot",
          "rds:RestoreDBClusterToPointInTime",
          "rds:RestoreDBInstanceFromDBSnapshot",
          "rds:RestoreDBInstanceToPointInTime",
        ],
        resources: [cluster.clusterArn],
      })
    );

    // ================================
    // DOCKER IMAGE ASSET
    // ================================

    // Build and push Docker image automatically during CDK deployment
    // DockerImageAsset creates and manages its own ECR repository
    const dockerImageAsset = new ecr_assets.DockerImageAsset(this, 'RuntimeDockerImage', {
      directory: path.join(__dirname, '../data-analyst-assistant-agentcore-strands'),
      platform: ecr_assets.Platform.LINUX_ARM64
    });

    // ================================
    // BEDROCK AGENTCORE MEMORY (LTM)
    // ================================

    // Long-term memory with semantic strategy for AgentCore
    const uniqueSuffix = cdk.Names.uniqueId(this).slice(-8).toLowerCase().replace(/[^a-z0-9]/g, '');
    const agentMemory = new bedrockagentcore.CfnMemory(this, 'AgentMemory', {
      name: `DataAnalystAssistantMemory_${uniqueSuffix}`,
      eventExpiryDuration: 90, // Events expire after 90 days
      memoryExecutionRoleArn: agentCoreRole.roleArn,
      description: 'Long-term semantic memory for data analyst assistant conversations',
      memoryStrategies: [
        {
          semanticMemoryStrategy: {
            name: 'Facts',
            description: 'Extracts and stores facts about video game sales data analysis conversations',
            namespaces: ['/facts/{actorId}'],
          },
        },
      ],
    });

    // ================================
    // BEDROCK AGENTCORE RUNTIME
    // ================================

    // AgentCore Runtime with container type for the data analyst assistant
    const agentRuntime = new bedrockagentcore.CfnRuntime(this, 'AgentRuntime', {
      agentRuntimeName: `DataAnalystRuntime_${uniqueSuffix}`,
      agentRuntimeArtifact: {
        containerConfiguration: {
          containerUri: dockerImageAsset.imageUri,
        },
      },
      networkConfiguration: {
        networkMode: 'PUBLIC',
      },
      roleArn: agentCoreRole.roleArn,
      description: 'Container runtime for video games sales data analyst assistant',
      environmentVariables: {
        MEMORY_ID: agentMemory.attrMemoryId,
        BEDROCK_MODEL_ID: bedrockModelId.valueAsString,
        READONLY_SECRET_ARN: readOnlySecret.secretArn,
        AURORA_RESOURCE_ARN: cluster.clusterArn,
        DATABASE_NAME: databaseName.valueAsString,
        QUESTION_ANSWERS_TABLE: rawQueryResults.tableName,
        MAX_RESPONSE_SIZE_BYTES: '1048576',
      },
    });
    
    agentRuntime.addDependency(agentMemory);

    // ================================
    // BEDROCK AGENTCORE RUNTIME ENDPOINT
    // ================================

    // Runtime endpoint for invoking the data analyst assistant
    const runtimeEndpoint = new bedrockagentcore.CfnRuntimeEndpoint(this, 'RuntimeEndpoint', {
      agentRuntimeId: agentRuntime.attrAgentRuntimeId,
      name: `DataAnalystEndpoint_${uniqueSuffix}`,
      description: 'Endpoint for invoking the video games sales data analyst assistant',
    });

    // Endpoint depends on runtime being created first
    runtimeEndpoint.addDependency(agentRuntime);

    // ================================
    // OBSERVABILITY - LOG DELIVERY
    // ================================

    // --- AgentCore Runtime Logs ---

    // CloudWatch Log Group for AgentCore Runtime application logs
    const runtimeLogGroup = new logs.LogGroup(this, 'RuntimeLogGroup', {
      logGroupName: `/aws/vendedlogs/bedrock-agentcore/${agentRuntime.attrAgentRuntimeId}`,
      retention: logs.RetentionDays.TWO_WEEKS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Delivery Source — Runtime application logs
    const runtimeLogSource = new logs.CfnDeliverySource(this, 'RuntimeLogSource', {
      name: `rt-${uniqueSuffix}-log-src`,
      logType: 'APPLICATION_LOGS',
      resourceArn: agentRuntime.attrAgentRuntimeArn,
    });
    runtimeLogSource.addDependency(agentRuntime);

    // Delivery Destination — CloudWatch Logs for runtime
    const runtimeLogDestination = new logs.CfnDeliveryDestination(this, 'RuntimeLogDestination', {
      name: `rt-${uniqueSuffix}-log-dst`,
      destinationResourceArn: runtimeLogGroup.logGroupArn,
    });

    // Delivery — connects runtime source to destination
    const runtimeLogDelivery = new logs.CfnDelivery(this, 'RuntimeLogDelivery', {
      deliverySourceName: runtimeLogSource.ref,
      deliveryDestinationArn: runtimeLogDestination.attrArn,
    });
    runtimeLogDelivery.addDependency(runtimeLogSource);
    runtimeLogDelivery.addDependency(runtimeLogDestination);

    // --- AgentCore Runtime Traces (X-Ray) ---

    // Delivery Source — Runtime traces
    const runtimeTracesSource = new logs.CfnDeliverySource(this, 'RuntimeTracesSource', {
      name: `rt-${uniqueSuffix}-trc-src`,
      logType: 'TRACES',
      resourceArn: agentRuntime.attrAgentRuntimeArn,
    });
    runtimeTracesSource.addDependency(agentRuntime);

    // Delivery Destination — X-Ray for traces
    const runtimeTracesDestination = new logs.CfnDeliveryDestination(this, 'RuntimeTracesDestination', {
      name: `rt-${uniqueSuffix}-trc-dst`,
      deliveryDestinationType: 'XRAY',
    });

    // Delivery — connects traces source to X-Ray destination
    const runtimeTracesDelivery = new logs.CfnDelivery(this, 'RuntimeTracesDelivery', {
      deliverySourceName: runtimeTracesSource.ref,
      deliveryDestinationArn: runtimeTracesDestination.attrArn,
    });
    runtimeTracesDelivery.addDependency(runtimeTracesSource);
    runtimeTracesDelivery.addDependency(runtimeTracesDestination);

    // --- AgentCore Memory Logs ---

    // CloudWatch Log Group for AgentCore Memory vended log delivery
    const memoryLogGroup = new logs.LogGroup(this, 'MemoryLogGroup', {
      logGroupName: `/aws/vendedlogs/bedrock-agentcore/memory/${agentMemory.attrMemoryId}`,
      retention: logs.RetentionDays.TWO_WEEKS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Delivery Source — connects the Memory resource as a log source
    const memoryLogSource = new logs.CfnDeliverySource(this, 'MemoryLogSource', {
      name: `mem-${uniqueSuffix}-log-src`,
      logType: 'APPLICATION_LOGS',
      resourceArn: `arn:aws:bedrock-agentcore:${this.region}:${this.account}:memory/${agentMemory.attrMemoryId}`,
    });
    memoryLogSource.addDependency(agentMemory);

    // Delivery Destination — CloudWatch Logs
    const memoryLogDestination = new logs.CfnDeliveryDestination(this, 'MemoryLogDestination', {
      name: `mem-${uniqueSuffix}-log-dst`,
      destinationResourceArn: memoryLogGroup.logGroupArn,
    });

    // Delivery — connects source to destination
    const memoryLogDelivery = new logs.CfnDelivery(this, 'MemoryLogDelivery', {
      deliverySourceName: memoryLogSource.ref,
      deliveryDestinationArn: memoryLogDestination.attrArn,
    });
    memoryLogDelivery.addDependency(memoryLogSource);
    memoryLogDelivery.addDependency(memoryLogDestination);

    // ================================
    // CLOUDFORMATION OUTPUTS
    // ================================

    new cdk.CfnOutput(this, "AuroraServerlessDBClusterARN", {
      value: cluster.clusterArn,
      description: "The ARN of the Aurora Serverless DB Cluster",
    });

    new cdk.CfnOutput(this, "SecretARN", {
      value: secret.secretArn,
      description: "The ARN of the database credentials secret",
    });

    new cdk.CfnOutput(this, "ReadOnlySecretARN", {
      value: readOnlySecret.secretArn,
      description: "The ARN of the read-only database user secret",
    });

    new cdk.CfnOutput(this, "DataSourceBucketName", {
      value: importBucket.bucketName,
      description: "S3 bucket for importing data into Aurora using aws_s3 extension",
    });

    new cdk.CfnOutput(this, "QuestionAnswersTableName", {
      value: rawQueryResults.tableName,
      description: "The name of the DynamoDB table for storing query results",
    });

    new cdk.CfnOutput(this, "QuestionAnswersTableArn", {
      value: rawQueryResults.tableArn,
      description: "The ARN of the DynamoDB table for storing query results",
    });

    new cdk.CfnOutput(this, "AgentRuntimeArn", {
      value: agentRuntime.attrAgentRuntimeArn,
      description: "The ARN of the AgentCore runtime",
    });
    
    new cdk.CfnOutput(this, "MemoryId", {
      value: agentMemory.attrMemoryId,
      description: "The ID of the AgentCore Memory",
    });

  }
}
