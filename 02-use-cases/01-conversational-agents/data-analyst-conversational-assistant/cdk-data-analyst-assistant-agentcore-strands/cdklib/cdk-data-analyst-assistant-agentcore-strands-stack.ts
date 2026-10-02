/**
 * CDK Stack for AgentCore Strands Data Analyst Assistant
 *
 * Infrastructure for a data analyst assistant powered by Amazon Bedrock AgentCore.
 * Deploys everything in a single 'cdk deploy' — no manual post-steps required.
 *
 * Components:
 * - Aurora PostgreSQL database containing sample data
 * - Automatic data loading via Custom Resource (table creation + CSV import + read-only user)
 * - DynamoDB tables for query results
 * - VPC with networking and security configuration
 * - IAM roles and permissions for AgentCore
 * - S3 bucket for data imports
 * - AgentCore Runtime, Memory, and Observability
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as rds from "aws-cdk-lib/aws-rds";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import * as iam from "aws-cdk-lib/aws-iam";
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as cr from 'aws-cdk-lib/custom-resources';
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
      maxAzs: 2,
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

    // Aurora PostgreSQL Serverless v2 cluster containing sample data
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
    // AUTOMATIC DATA LOADING (Custom Resource)
    // ================================

    // Upload CSV data file to S3 during deployment
    const csvDeployment = new s3deploy.BucketDeployment(this, 'CsvDeployment', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '../resources/database'))],
      destinationBucket: importBucket,
      retainOnDelete: false,
    });

    // Lambda function that initializes the database (table + data + read-only user)
    const dataLoaderFn = new lambda.Function(this, 'DataLoaderFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../resources/lambdas/data-loader')),
      timeout: cdk.Duration.minutes(10),
      memorySize: 256,
      initialPolicy: [
        new iam.PolicyStatement({
          actions: ['rds-data:ExecuteStatement', 'rds-data:BatchExecuteStatement'],
          resources: [cluster.clusterArn],
        }),
        new iam.PolicyStatement({
          actions: ['secretsmanager:GetSecretValue'],
          resources: [secret.secretArn, readOnlySecret.secretArn],
        }),
        new iam.PolicyStatement({
          actions: ['s3:GetObject', 's3:ListBucket'],
          resources: [importBucket.bucketArn, `${importBucket.bucketArn}/*`],
        }),
      ],
    });

    // Custom Resource that triggers the data loader Lambda during cdk deploy
    const dataLoaderCr = new cdk.CustomResource(this, 'DataLoaderCustomResource', {
      serviceToken: new cr.Provider(this, 'DataLoaderProvider', {
        onEventHandler: dataLoaderFn,
      }).serviceToken,
      properties: {
        ClusterArn: cluster.clusterArn,
        AdminSecretArn: secret.secretArn,
        ReadOnlySecretArn: readOnlySecret.secretArn,
        DatabaseName: databaseName.valueAsString,
        BucketName: importBucket.bucketName,
        S3FileKey: 'video_games_sales_no_headers.csv',
        TableName: 'video_games_sales_units',
        // Change this value to force re-import on next deploy
        DeployTimestamp: Date.now().toString(),
      },
    });

    // Ensure data loading happens after cluster and CSV upload are ready
    dataLoaderCr.node.addDependency(cluster);
    dataLoaderCr.node.addDependency(csvDeployment);

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
                'bedrock-agentcore:DeleteEvent',
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
            // Gateway invocation permission
            new iam.PolicyStatement({
              sid: 'AgentCoreGatewayAccess',
              effect: iam.Effect.ALLOW,
              actions: [
                'bedrock-agentcore:InvokeGateway',
                'bedrock-agentcore:GetGateway',
                'bedrock-agentcore:ListGatewayTargets'
              ],
              resources: [
                `arn:aws:bedrock-agentcore:${this.region}:${this.account}:gateway/*`
              ]
            }),
            // Guardrail invocation permission
            new iam.PolicyStatement({
              sid: 'BedrockGuardrailAccess',
              effect: iam.Effect.ALLOW,
              actions: [
                'bedrock:ApplyGuardrail'
              ],
              resources: [
                `arn:aws:bedrock:${this.region}:${this.account}:guardrail/*`
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
            description: 'Extracts and stores facts from data analysis conversations',
            namespaces: ['/facts/{actorId}'],
          },
        },
      ],
    });

    // ================================
    // AGENTCORE GATEWAY + LAMBDA TARGET
    // ================================

    // Lambda function exposing PostgreSQL tools for Gateway
    const dbToolsLambda = new lambda.Function(this, 'DbToolsLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'lambda_function.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambdas/db_tools')),
      timeout: cdk.Duration.minutes(2),
      memorySize: 256,
      environment: {
        READONLY_SECRET_ARN: readOnlySecret.secretArn,
        AURORA_RESOURCE_ARN: cluster.clusterArn,
        DATABASE_NAME: databaseName.valueAsString,
        MAX_RESPONSE_SIZE_BYTES: '1048576',
      },
      initialPolicy: [
        new iam.PolicyStatement({
          actions: ['rds-data:ExecuteStatement', 'rds-data:BatchExecuteStatement'],
          resources: [cluster.clusterArn],
        }),
        new iam.PolicyStatement({
          actions: ['secretsmanager:GetSecretValue'],
          resources: [readOnlySecret.secretArn],
        }),
      ],
    });

    // Allow AgentCore Gateway to invoke the Lambda
    dbToolsLambda.addPermission('AllowAgentCoreGateway', {
      principal: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      action: 'lambda:InvokeFunction',
    });

    // IAM role for Gateway to invoke Lambda targets, access Policy Engine, and run guardrail checks
    const gatewayRole = new iam.Role(this, 'GatewayRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      inlinePolicies: {
        'GatewayLambdaInvoke': new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['lambda:InvokeFunction'],
              resources: [dbToolsLambda.functionArn],
            }),
            new iam.PolicyStatement({
              actions: [
                'bedrock-agentcore:AuthorizeAction',
                'bedrock-agentcore:PartiallyAuthorizeActions',
                'bedrock-agentcore:GetPolicyEngine',
                'bedrock-agentcore:CheckAuthorizePermissions',
              ],
              resources: [
                `arn:aws:bedrock-agentcore:${this.region}:${this.account}:policy-engine/*`,
                `arn:aws:bedrock-agentcore:${this.region}:${this.account}:gateway/*`,
                `arn:aws:bedrock-agentcore:${this.region}:${this.account}:/policy-engines/*/target-resource/*`,
              ],
            }),
            new iam.PolicyStatement({
              actions: ['bedrock:InvokeGuardrailChecks'],
              resources: ['*'],
            }),
          ],
        }),
      },
    });

    // Gateway resource (IAM-based auth — agent runtime invokes via its execution role)
    const gateway = new bedrockagentcore.CfnGateway(this, 'ToolsGateway', {
      name: `VideoGamesToolsGateway-${uniqueSuffix}`,
      authorizerType: 'NONE',
      protocolType: 'MCP',
      roleArn: gatewayRole.roleArn,
      description: 'Gateway exposing database tools as MCP targets',
    });

    // Register the Lambda as a Gateway target with inline tool schema
    const gatewayTarget = new bedrockagentcore.CfnGatewayTarget(this, 'DbToolsGatewayTarget', {
      gatewayIdentifier: gateway.attrGatewayIdentifier,
      name: 'DatabaseToolsTarget',
      description: 'PostgreSQL query tools for data analysis',
      credentialProviderConfigurations: [
        { credentialProviderType: 'GATEWAY_IAM_ROLE' },
      ],
      targetConfiguration: {
        mcp: {
          lambda: {
            lambdaArn: dbToolsLambda.functionArn,
            toolSchema: {
              inlinePayload: [
                {
                  name: 'get_tables_information',
                  description: 'Get database schema metadata including table structures, columns, and relationships for the PostgreSQL database',
                  inputSchema: {
                    type: 'object',
                    properties: {},
                    required: [],
                  },
                },
                {
                  name: 'execute_sql_query',
                  description: 'Execute a read-only SQL query against the PostgreSQL database',
                  inputSchema: {
                    type: 'object',
                    properties: {
                      sql_query: {
                        type: 'string',
                        description: 'The SQL query to execute (read-only SELECT statements only)',
                      },
                      description: {
                        type: 'string',
                        description: 'A clear description of what this query analyzes',
                      },
                    },
                    required: ['sql_query', 'description'],
                  },
                },
              ],
            },
          },
        },
      },
    });

    gatewayTarget.addDependency(gateway);

    // ================================
    // AGENTCORE POLICY ENGINE (Cedar Authorization + Guardrails-in-Policy)
    // ================================

    // Policy Engine provides fine-grained Cedar-based authorization and guardrail
    // content filtering for Gateway tool calls.
    const policyEngine = new bedrockagentcore.CfnPolicyEngine(this, 'PolicyEngine', {
      name: `VideoGamesSalesPolicy_${uniqueSuffix}`,
      description: 'Cedar authorization + guardrails-in-policy for data analyst gateway tools',
    });

    // Helper to create a policy using 'Definition.Policy.Statement' format
    // (required for guardrails-in-policy Cedar syntax that CDK types don't yet support)
    const createPolicy = (id: string, name: string, description: string, statement: string, dependsOn: cdk.CfnResource) => {
      const policy = new cdk.CfnResource(this, id, {
        type: 'AWS::BedrockAgentCore::Policy',
        properties: {
          PolicyEngineId: policyEngine.attrPolicyEngineId,
          Name: name,
          Description: description,
          ValidationMode: 'IGNORE_ALL_FINDINGS',
          Definition: {
            Policy: {
              Statement: statement,
            },
          },
        },
      });
      policy.addDependency(dependsOn);
      return policy;
    };

    // Cedar policy: allow all tool calls (default permit)
    const allowDefaultPolicy = createPolicy(
      'AllowDefaultPolicy',
      'allowDefaultToolAccess',
      'Permit all tool calls that are not explicitly forbidden',
      'permit(principal, action, resource is AgentCore::Gateway);',
      policyEngine,
    );

    // Guardrail policy: block prompt injection attempts
    createPolicy(
      'BlockPromptAttackPolicy',
      'blockPromptAttacks',
      'Block prompt injection attempts detected with high confidence',
      'forbid(principal, action == AgentCore::Action::"Http", resource is AgentCore::Gateway)\nwhen guardrails { BedrockGuardrails::PromptAttack(["PROMPT_INJECTION"], [context.input.prompt])["PROMPT_INJECTION"].confidenceScore.greaterThan(decimal("0.4")) };',
      allowDefaultPolicy,
    );

    // Guardrail policy: block violent or hateful content
    createPolicy(
      'BlockHarmfulContentPolicy',
      'blockHarmfulContent',
      'Block requests containing violence or hate speech',
      'forbid(principal, action == AgentCore::Action::"Http", resource is AgentCore::Gateway)\nwhen guardrails { BedrockGuardrails::ContentFilter(["VIOLENCE", "HATE"], [context.input.prompt]).maxConfidenceScore().greaterThan(decimal("0.2")) };',
      allowDefaultPolicy,
    );

    // Guardrail policy: suppress PII in output
    createPolicy(
      'SuppressPiiOutputPolicy',
      'suppressPiiInOutput',
      'Suppress responses containing SSN, credit card numbers, email, or phone numbers',
      'suppressOutput(principal, action == AgentCore::Action::"Http", resource is AgentCore::Gateway)\nwhen guardrails { BedrockGuardrails::SensitiveInformation(["US_SOCIAL_SECURITY_NUMBER", "CREDIT_DEBIT_CARD_NUMBER", "EMAIL", "PHONE"], [context.output.text]).maxConfidenceScore().greaterThan(decimal("0.2")) };',
      allowDefaultPolicy,
    );

    // Attach Policy Engine to Gateway in ENFORCE mode.
    // The Gateway must be updated with this config BEFORE action-referencing policies
    // can be created (they need the schema from attached gateway targets).
    gateway.policyEngineConfiguration = {
      arn: policyEngine.attrPolicyEngineArn,
      mode: 'ENFORCE',
    };

    // Policies that reference specific actions (e.g. DatabaseToolsTarget___execute_sql_query)
    // require the policy engine to be attached to the gateway AND the gateway target to
    // exist — otherwise "Cannot find Action in schema".
    allowDefaultPolicy.addDependency(gateway);
    allowDefaultPolicy.addDependency(gatewayTarget);

    // ================================
    // AGENTCORE EVALUATORS (LLM-as-a-Judge)
    // ================================

    // SQL Accuracy Evaluator — judges whether generated SQL correctly answers the user's question
    const sqlAccuracyEvaluator = new bedrockagentcore.CfnEvaluator(this, 'SqlAccuracyEvaluator', {
      evaluatorName: `SqlAccuracy_${uniqueSuffix}`,
      description: 'Evaluates whether the generated SQL query correctly answers the user question',
      level: 'TRACE',
      evaluatorConfig: {
        llmAsAJudge: {
          instructions: `You are evaluating a data analyst agent that generates SQL queries against a database.

Context: {context}

The assistant responded with: {assistant_turn}

Evaluate:
1. Does the SQL query correctly address what the user asked?
2. Are the correct columns, aggregations, and filters used?
3. Is the result accurate and complete?

Score 5 if the SQL perfectly answers the question with correct logic.
Score 4 if mostly correct with minor issues (e.g., missing a column).
Score 3 if partially correct but has logical errors.
Score 2 if the approach is wrong but shows some understanding.
Score 1 if completely incorrect or irrelevant.`,
          modelConfig: {
            bedrockEvaluatorModelConfig: {
              modelId: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
            },
          },
          ratingScale: {
            numerical: [
              { value: 1, label: 'Incorrect', definition: 'Completely incorrect SQL or irrelevant response' },
              { value: 2, label: 'Poor', definition: 'Wrong approach but shows some understanding' },
              { value: 3, label: 'Partial', definition: 'Partially correct with logical errors' },
              { value: 4, label: 'Good', definition: 'Mostly correct with minor issues' },
              { value: 5, label: 'Excellent', definition: 'Perfect SQL that correctly answers the question' },
            ],
          },
        },
      },
    });

    // Response Quality Evaluator — judges overall helpfulness and clarity of the response
    const responseQualityEvaluator = new bedrockagentcore.CfnEvaluator(this, 'ResponseQualityEvaluator', {
      evaluatorName: `ResponseQuality_${uniqueSuffix}`,
      description: 'Evaluates the overall quality, clarity, and helpfulness of the agent response',
      level: 'SESSION',
      evaluatorConfig: {
        llmAsAJudge: {
          instructions: `You are evaluating the overall response quality of a data analyst conversational assistant.

Context: {context}

Available tools: {available_tools}

Actual tool trajectory: {actual_tool_trajectory}

Evaluate the agent's response for:
1. Clarity: Is the response well-structured and easy to understand?
2. Completeness: Does it fully answer the user's question?
3. Accuracy: Are the numbers and insights correct based on the data?
4. Helpfulness: Does it provide useful context or insights beyond raw data?
5. Appropriate scope: Does it stay within the available data domain?

Score 5 for excellent responses that are clear, complete, accurate, and insightful.
Score 4 for good responses with minor areas for improvement.
Score 3 for adequate responses that answer the question but lack depth.
Score 2 for poor responses that are confusing or partially wrong.
Score 1 for responses that fail to address the question or are incorrect.`,
          modelConfig: {
            bedrockEvaluatorModelConfig: {
              modelId: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
            },
          },
          ratingScale: {
            numerical: [
              { value: 1, label: 'Failed', definition: 'Failed to address the question or incorrect' },
              { value: 2, label: 'Poor', definition: 'Poor response — confusing or partially wrong' },
              { value: 3, label: 'Adequate', definition: 'Adequate — answers question but lacks depth' },
              { value: 4, label: 'Good', definition: 'Good — minor areas for improvement' },
              { value: 5, label: 'Excellent', definition: 'Excellent — clear, complete, accurate, insightful' },
            ],
          },
        },
      },
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
      description: 'Container runtime for data analyst conversational assistant',
      environmentVariables: {
        MEMORY_ID: agentMemory.attrMemoryId,
        BEDROCK_MODEL_ID: bedrockModelId.valueAsString,
        READONLY_SECRET_ARN: readOnlySecret.secretArn,
        AURORA_RESOURCE_ARN: cluster.clusterArn,
        DATABASE_NAME: databaseName.valueAsString,
        QUESTION_ANSWERS_TABLE: rawQueryResults.tableName,
        MAX_RESPONSE_SIZE_BYTES: '1048576',
        GATEWAY_URL: gateway.attrGatewayUrl,
      },
    });
    
    agentRuntime.addDependency(agentMemory);
    agentRuntime.addDependency(gateway);

    // ================================
    // BEDROCK AGENTCORE RUNTIME ENDPOINT
    // ================================

    // Runtime endpoint for invoking the data analyst assistant
    const runtimeEndpoint = new bedrockagentcore.CfnRuntimeEndpoint(this, 'RuntimeEndpoint', {
      agentRuntimeId: agentRuntime.attrAgentRuntimeId,
      name: `DataAnalystEndpoint_${uniqueSuffix}`,
      description: 'Endpoint for invoking the data analyst conversational assistant',
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

    // --- AgentCore Gateway Logs ---

    // CloudWatch Log Group for AgentCore Gateway invocation logs
    const gatewayLogGroup = new logs.LogGroup(this, 'GatewayLogGroup', {
      logGroupName: `/aws/vendedlogs/bedrock-agentcore/gateway/${gateway.attrGatewayIdentifier}`,
      retention: logs.RetentionDays.TWO_WEEKS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Delivery Source — Gateway application logs
    const gatewayLogSource = new logs.CfnDeliverySource(this, 'GatewayLogSource', {
      name: `gw-${uniqueSuffix}-log-src`,
      logType: 'APPLICATION_LOGS',
      resourceArn: `arn:aws:bedrock-agentcore:${this.region}:${this.account}:gateway/${gateway.attrGatewayIdentifier}`,
    });
    gatewayLogSource.addDependency(gateway);

    // Delivery Destination — CloudWatch Logs for gateway
    const gatewayLogDestination = new logs.CfnDeliveryDestination(this, 'GatewayLogDestination', {
      name: `gw-${uniqueSuffix}-log-dst`,
      destinationResourceArn: gatewayLogGroup.logGroupArn,
    });

    // Delivery — connects gateway source to destination
    const gatewayLogDelivery = new logs.CfnDelivery(this, 'GatewayLogDelivery', {
      deliverySourceName: gatewayLogSource.ref,
      deliveryDestinationArn: gatewayLogDestination.attrArn,
    });
    gatewayLogDelivery.addDependency(gatewayLogSource);
    gatewayLogDelivery.addDependency(gatewayLogDestination);

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

    new cdk.CfnOutput(this, "GatewayUrl", {
      value: gateway.attrGatewayUrl,
      description: "The MCP endpoint URL for the AgentCore Gateway",
    });

    new cdk.CfnOutput(this, "PolicyEngineId", {
      value: policyEngine.attrPolicyEngineId,
      description: "The ID of the AgentCore Policy Engine for Cedar authorization + guardrails",
    });

    new cdk.CfnOutput(this, "SqlAccuracyEvaluatorArn", {
      value: sqlAccuracyEvaluator.attrEvaluatorArn,
      description: "The ARN of the SQL Accuracy evaluator",
    });

    new cdk.CfnOutput(this, "ResponseQualityEvaluatorArn", {
      value: responseQualityEvaluator.attrEvaluatorArn,
      description: "The ARN of the Response Quality evaluator",
    });

  }
}
