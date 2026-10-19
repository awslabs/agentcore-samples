import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import { createHash } from "crypto";
import * as fs from "fs";
import * as path from "path";

// ============================================================================
// Configuration
// ============================================================================

const config = new pulumi.Config();
const agentName = config.get("agentName") || "WeatherAgent";
const memoryName = config.get("memoryName") || "WeatherAgentMemory";
const networkMode = config.get("networkMode") || "PUBLIC";
const imageTag = config.get("imageTag") || "latest";
const stackName = config.get("stackName") || "agentcore-weather";
const description =
  config.get("description") ||
  "End-to-end Weather Agent with AgentCore tools (Browser, Code Interpreter, Memory)";
const ecrRepositoryName = config.get("ecrRepositoryName") || "weather-agent";

// Get the AWS region from the provider configuration
const awsConfig = new pulumi.Config("aws");
const awsRegion = awsConfig.require("region");

// ============================================================================
// Data Sources
// ============================================================================

const currentIdentity = aws.getCallerIdentityOutput({});
const currentRegion = aws.getRegionOutput({});

// ============================================================================
// Browser Tool - For Web Browsing Capabilities
// ============================================================================

const browser = new aws.bedrock.AgentcoreBrowser("browser", {
  name: `${stackName.replace(/-/g, "_")}_browser`,
  description: `Browser tool for ${stackName} weather agent to access weather websites`,
  networkConfiguration: {
    networkMode: networkMode,
  },
  tags: {
    Name: `${stackName}-browser-tool`,
    Module: "AgentCore-Tools",
  },
});

// ============================================================================
// Code Interpreter Tool - For Python Code Execution and Data Analysis
// ============================================================================

const codeInterpreter = new aws.bedrock.AgentcoreCodeInterpreter(
  "code_interpreter",
  {
    name: `${stackName.replace(/-/g, "_")}_code_interpreter`,
    description: `Code interpreter tool for ${stackName} weather agent to analyze weather data`,
    networkConfiguration: {
      networkMode: networkMode,
    },
    tags: {
      Name: `${stackName}-code-interpreter-tool`,
      Module: "AgentCore-Tools",
    },
  },
);

// ============================================================================
// Memory - For Persistent Conversation Context
// ============================================================================

const memory = new aws.bedrock.AgentcoreMemory("memory", {
  name: `${stackName.replace(/-/g, "_")}_${memoryName}`,
  description: `Memory for ${stackName} weather agent to maintain conversation context`,
  eventExpiryDuration: 30,
  tags: {
    Name: `${stackName}-memory`,
    Module: "AgentCore-Tools",
  },
});

// ============================================================================
// S3 Buckets
// ============================================================================

// Agent Source Code Bucket
const agentSourceBucket = new aws.s3.Bucket("agent_source", {
  bucketPrefix: `${stackName}-source-`,
  forceDestroy: true,
  tags: {
    Name: `${stackName}-agent-source`,
    Purpose: "Store agent source code for CodeBuild",
  },
});

// Results Bucket (for agent-generated artifacts)
const results = new aws.s3.Bucket("results", {
  bucketPrefix: `${stackName}-results-`,
  forceDestroy: true,
  tags: {
    Name: `${stackName}-results`,
    Purpose: "Store weather agent generated artifacts",
  },
});

new aws.s3.BucketPublicAccessBlock("agent_source", {
  bucket: agentSourceBucket.id,
  blockPublicAcls: true,
  blockPublicPolicy: true,
  ignorePublicAcls: true,
  restrictPublicBuckets: true,
});

new aws.s3.BucketPublicAccessBlock("results", {
  bucket: results.id,
  blockPublicAcls: true,
  blockPublicPolicy: true,
  ignorePublicAcls: true,
  restrictPublicBuckets: true,
});

new aws.s3.BucketVersioning("agent_source", {
  bucket: agentSourceBucket.id,
  versioningConfiguration: {
    status: "Enabled",
  },
});

new aws.s3.BucketVersioning("results", {
  bucket: results.id,
  versioningConfiguration: {
    status: "Enabled",
  },
});

// ============================================================================
// Upload Agent Source Code to S3
// ============================================================================

const agentSourceObject = new aws.s3.BucketObjectv2("agent_source", {
  bucket: agentSourceBucket.id,
  key: "agent-code.zip",
  source: new pulumi.asset.FileArchive(path.resolve(__dirname, "agent-code")),
  tags: {
    Name: "agent-source-code",
  },
});

// ============================================================================
// ECR Repository - Container Registry for Agent Image
// ============================================================================

const weatherEcr = new aws.ecr.Repository("weather_ecr", {
  name: `${stackName}-${ecrRepositoryName}`,
  imageTagMutability: "MUTABLE",
  imageScanningConfiguration: {
    scanOnPush: true,
  },
  forceDelete: true,
  tags: {
    Name: `${stackName}-ecr-repository`,
    Module: "ECR",
  },
});

new aws.ecr.RepositoryPolicy("weather_ecr", {
  repository: weatherEcr.name,
  policy: pulumi.jsonStringify({
    Version: "2012-10-17",
    Statement: [
      {
        Sid: "AllowPullFromAccount",
        Effect: "Allow",
        Principal: {
          AWS: currentIdentity.apply(
            (id) => `arn:aws:iam::${id.accountId}:root`,
          ),
        },
        Action: ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
      },
    ],
  }),
});

new aws.ecr.LifecyclePolicy("weather_ecr", {
  repository: weatherEcr.name,
  policy: JSON.stringify({
    rules: [
      {
        rulePriority: 1,
        description: "Keep last 5 images",
        selection: {
          tagStatus: "any",
          countType: "imageCountMoreThan",
          countNumber: 5,
        },
        action: {
          type: "expire",
        },
      },
    ],
  }),
});

// ============================================================================
// Agent Execution Role - For AgentCore Runtime
// ============================================================================

const agentExecution = new aws.iam.Role("agent_execution", {
  name: `${stackName}-agent-execution-role`,
  assumeRolePolicy: pulumi.jsonStringify({
    Version: "2012-10-17",
    Statement: [
      {
        Sid: "AssumeRolePolicy",
        Effect: "Allow",
        Principal: {
          Service: "bedrock-agentcore.amazonaws.com",
        },
        Action: "sts:AssumeRole",
        Condition: {
          StringEquals: {
            "aws:SourceAccount": currentIdentity.apply((id) => id.accountId),
          },
          ArnLike: {
            "aws:SourceArn": pulumi
              .all([currentRegion, currentIdentity])
              .apply(
                ([region, identity]) =>
                  `arn:aws:bedrock-agentcore:${region.region}:${identity.accountId}:*`,
              ),
          },
        },
      },
    ],
  }),
  tags: {
    Name: `${stackName}-agent-execution-role`,
    Module: "IAM",
  },
});

const agentExecutionManaged = new aws.iam.RolePolicyAttachment(
  "agent_execution_managed",
  {
    role: agentExecution.name,
    policyArn: "arn:aws:iam::aws:policy/BedrockAgentCoreFullAccess",
  },
);

const agentExecutionRolePolicy = new aws.iam.RolePolicy("agent_execution", {
  name: "AgentCoreExecutionPolicy",
  role: agentExecution.id,
  policy: pulumi.jsonStringify({
    Version: "2012-10-17",
    Statement: [
      {
        Sid: "ECRImageAccess",
        Effect: "Allow",
        Action: [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
        ],
        Resource: weatherEcr.arn,
      },
      {
        Sid: "ECRTokenAccess",
        Effect: "Allow",
        Action: ["ecr:GetAuthorizationToken"],
        Resource: "*",
      },
      {
        Sid: "CloudWatchLogs",
        Effect: "Allow",
        Action: [
          "logs:DescribeLogStreams",
          "logs:CreateLogGroup",
          "logs:DescribeLogGroups",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ],
        Resource: pulumi
          .all([currentRegion, currentIdentity])
          .apply(
            ([region, identity]) =>
              `arn:aws:logs:${region.region}:${identity.accountId}:log-group:/aws/bedrock-agentcore/runtimes/*`,
          ),
      },
      {
        Sid: "XRayTracing",
        Effect: "Allow",
        Action: [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets",
        ],
        Resource: "*",
      },
      {
        Sid: "CloudWatchMetrics",
        Effect: "Allow",
        Action: ["cloudwatch:PutMetricData"],
        Resource: "*",
        Condition: {
          StringEquals: {
            "cloudwatch:namespace": "bedrock-agentcore",
          },
        },
      },
      {
        Sid: "BedrockModelInvocation",
        Effect: "Allow",
        Action: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ],
        Resource: "*",
      },
      {
        Sid: "GetAgentAccessToken",
        Effect: "Allow",
        Action: [
          "bedrock-agentcore:GetWorkloadAccessToken",
          "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
          "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
        ],
        Resource: [
          pulumi
            .all([currentRegion, currentIdentity])
            .apply(
              ([region, identity]) =>
                `arn:aws:bedrock-agentcore:${region.region}:${identity.accountId}:workload-identity-directory/default`,
            ),
          pulumi
            .all([currentRegion, currentIdentity])
            .apply(
              ([region, identity]) =>
                `arn:aws:bedrock-agentcore:${region.region}:${identity.accountId}:workload-identity-directory/default/workload-identity/*`,
            ),
        ],
      },
      {
        Sid: "S3ResultsAccess",
        Effect: "Allow",
        Action: [
          "s3:PutObject",
          "s3:GetObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ],
        Resource: [results.arn, pulumi.interpolate`${results.arn}/*`],
      },
    ],
  }),
});

// ============================================================================
// CodeBuild Service Role - For Docker Image Building
// ============================================================================

const codebuildRole = new aws.iam.Role("codebuild", {
  name: `${stackName}-codebuild-role`,
  assumeRolePolicy: JSON.stringify({
    Version: "2012-10-17",
    Statement: [
      {
        Effect: "Allow",
        Principal: {
          Service: "codebuild.amazonaws.com",
        },
        Action: "sts:AssumeRole",
      },
    ],
  }),
  tags: {
    Name: `${stackName}-codebuild-role`,
    Module: "IAM",
  },
});

const codebuildRolePolicy = new aws.iam.RolePolicy("codebuild", {
  name: "CodeBuildPolicy",
  role: codebuildRole.id,
  policy: pulumi.jsonStringify({
    Version: "2012-10-17",
    Statement: [
      {
        Sid: "CloudWatchLogs",
        Effect: "Allow",
        Action: [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ],
        Resource: pulumi
          .all([currentRegion, currentIdentity])
          .apply(
            ([region, identity]) =>
              `arn:aws:logs:${region.region}:${identity.accountId}:log-group:/aws/codebuild/*`,
          ),
      },
      {
        Sid: "ECRAccess",
        Effect: "Allow",
        Action: [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:GetAuthorizationToken",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
        ],
        Resource: [weatherEcr.arn, "*"],
      },
      {
        Sid: "S3SourceAccess",
        Effect: "Allow",
        Action: ["s3:GetObject", "s3:GetObjectVersion"],
        Resource: pulumi.interpolate`${agentSourceBucket.arn}/*`,
      },
      {
        Sid: "S3BucketAccess",
        Effect: "Allow",
        Action: ["s3:ListBucket", "s3:GetBucketLocation"],
        Resource: agentSourceBucket.arn,
      },
    ],
  }),
});

// ============================================================================
// Build Trigger Lambda - Start and Wait for CodeBuild
// ============================================================================

const agentImageProjectName = `${stackName}-agent-build`;

const buildTriggerRole = new aws.iam.Role("build_trigger", {
  name: `${stackName}-build-trigger-role`,
  assumeRolePolicy: pulumi.jsonStringify({
    Version: "2012-10-17",
    Statement: [
      {
        Effect: "Allow",
        Principal: {
          Service: "lambda.amazonaws.com",
        },
        Action: "sts:AssumeRole",
      },
    ],
  }),
  inlinePolicies: [
    {
      name: "BuildTriggerPolicy",
      policy: pulumi
        .all([currentRegion, currentIdentity])
        .apply(([region, identity]) =>
          JSON.stringify({
            Version: "2012-10-17",
            Statement: [
              {
                Sid: "ManageBuild",
                Effect: "Allow",
                Action: ["codebuild:StartBuild", "codebuild:BatchGetBuilds"],
                Resource: `arn:aws:codebuild:${region.region}:${identity.accountId}:project/${agentImageProjectName}`,
              },
            ],
          }),
        ),
    },
  ],
  tags: {
    Name: `${stackName}-build-trigger-role`,
    Module: "Lambda",
  },
});

const buildTriggerBasicExecution = new aws.iam.RolePolicyAttachment(
  "build_trigger_basic_execution",
  {
    role: buildTriggerRole.name,
    policyArn:
      "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
  },
);

const buildTriggerFunction = new aws.lambda.Function("build_trigger", {
  name: `${stackName}-build-trigger`,
  role: buildTriggerRole.arn,
  runtime: aws.lambda.Runtime.Python3d12,
  handler: "index.handler",
  timeout: 900,
  code: new pulumi.asset.FileArchive(
    path.resolve(__dirname, "lambda/build-trigger"),
  ),
  tags: {
    Name: `${stackName}-build-trigger`,
    Module: "Lambda",
  },
});

// ============================================================================
// CodeBuild Project - Build and Push Docker Image
// ============================================================================

const buildspecContent = fs.readFileSync(
  path.resolve(__dirname, "buildspec.yml"),
  "utf-8",
);
const buildspecFingerprint = createHash("sha256")
  .update(buildspecContent)
  .digest("hex");

const agentImage = new aws.codebuild.Project("agent_image", {
  name: agentImageProjectName,
  description: `Build Weather Agent Docker image for ${stackName}`,
  serviceRole: codebuildRole.arn,
  buildTimeout: 60,
  artifacts: {
    type: "NO_ARTIFACTS",
  },
  environment: {
    computeType: "BUILD_GENERAL1_LARGE",
    image: "aws/codebuild/amazonlinux2-aarch64-standard:3.0",
    type: "ARM_CONTAINER",
    privilegedMode: true,
    imagePullCredentialsType: "CODEBUILD",
    environmentVariables: [
      {
        name: "AWS_DEFAULT_REGION",
        value: currentRegion.apply((r) => r.region),
      },
      {
        name: "AWS_ACCOUNT_ID",
        value: currentIdentity.apply((id) => id.accountId),
      },
      {
        name: "IMAGE_REPO_NAME",
        value: weatherEcr.name,
      },
      {
        name: "IMAGE_TAG",
        value: imageTag,
      },
      {
        name: "STACK_NAME",
        value: stackName,
      },
    ],
  },
  source: {
    type: "S3",
    location: pulumi.interpolate`${agentSourceBucket.id}/${agentSourceObject.key}`,
    buildspec: buildspecContent,
  },
  logsConfig: {
    cloudwatchLogs: {
      groupName: `/aws/codebuild/${agentImageProjectName}`,
    },
  },
  tags: {
    Name: `${stackName}-agent-build`,
    Module: "CodeBuild",
  },
});

// ============================================================================
// Trigger CodeBuild - Build Image Before Creating Runtime
// ============================================================================

const buildTriggerInvocationInput = pulumi
  .all([agentImage.name, currentRegion])
  .apply(([projectName, region]) =>
    JSON.stringify({
      projectName,
      region: region.region,
      pollIntervalSeconds: 15,
    }),
  );

const triggerBuild = new aws.lambda.Invocation(
  "trigger_build",
  {
    functionName: buildTriggerFunction.name,
    input: buildTriggerInvocationInput,
    triggers: {
      sourceVersion: agentSourceObject.versionId,
      imageTag,
      buildspecSha256: buildspecFingerprint,
    },
  },
  {
    dependsOn: [
      agentImage,
      weatherEcr,
      codebuildRolePolicy,
      agentSourceObject,
      buildTriggerBasicExecution,
      buildTriggerFunction,
    ],
  },
);

// ============================================================================
// Memory Initialization Lambda - Populate Activity Preferences
// ============================================================================

const memoryInitRole = new aws.iam.Role("memory_init", {
  name: `${stackName}-memory-init-role`,
  assumeRolePolicy: pulumi.jsonStringify({
    Version: "2012-10-17",
    Statement: [
      {
        Effect: "Allow",
        Principal: {
          Service: "lambda.amazonaws.com",
        },
        Action: "sts:AssumeRole",
      },
    ],
  }),
  inlinePolicies: [
    {
      name: "MemoryInitPolicy",
      policy: memory.arn.apply((memoryArn) =>
        JSON.stringify({
          Version: "2012-10-17",
          Statement: [
            {
              Sid: "CreateMemoryEvent",
              Effect: "Allow",
              Action: ["bedrock-agentcore:CreateEvent"],
              Resource: memoryArn,
            },
          ],
        }),
      ),
    },
  ],
  tags: {
    Name: `${stackName}-memory-init-role`,
    Module: "Lambda",
  },
});

const memoryInitBasicExecution = new aws.iam.RolePolicyAttachment(
  "memory_init_basic_execution",
  {
    role: memoryInitRole.name,
    policyArn:
      "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
  },
);

const memoryInitFunction = new aws.lambda.Function("memory_init", {
  name: `${stackName}-memory-init`,
  role: memoryInitRole.arn,
  runtime: aws.lambda.Runtime.Python3d12,
  handler: "index.handler",
  timeout: 60,
  code: new pulumi.asset.FileArchive(
    path.resolve(__dirname, "lambda/init-memory"),
  ),
  tags: {
    Name: `${stackName}-memory-init`,
    Module: "Lambda",
  },
});

new aws.lambda.Invocation(
  "initialize_memory",
  {
    functionName: memoryInitFunction.name,
    input: pulumi.all([memory.id, currentRegion]).apply(([memoryId, region]) =>
      JSON.stringify({
        memoryId,
        region: region.region,
      }),
    ),
    triggers: {
      memoryId: memory.id,
    },
  },
  {
    dependsOn: [memory, memoryInitFunction, memoryInitBasicExecution],
  },
);

// ============================================================================
// Weather Agent Runtime
// ============================================================================

const runtimeName = `${stackName}_${agentName}`.replace(/-/g, "_");

const weatherAgent = new aws.bedrock.AgentcoreAgentRuntime(
  "weather_agent",
  {
    agentRuntimeName: runtimeName,
    description: description,
    roleArn: agentExecution.arn,
    agentRuntimeArtifact: {
      containerConfiguration: {
        containerUri: pulumi.interpolate`${weatherEcr.repositoryUrl}:${imageTag}`,
      },
    },
    networkConfiguration: {
      networkMode: networkMode,
    },
    environmentVariables: {
      AWS_REGION: awsRegion,
      AWS_DEFAULT_REGION: awsRegion,
      RESULTS_BUCKET: results.id,
      BROWSER_ID: browser.browserId,
      CODE_INTERPRETER_ID: codeInterpreter.codeInterpreterId,
      MEMORY_ID: memory.id,
    },
  },
  {
    dependsOn: [
      triggerBuild,
      agentExecutionRolePolicy,
      agentExecutionManaged,
      browser,
      codeInterpreter,
      memory,
    ],
  },
);

// ============================================================================
// Observability - CloudWatch Logs and X-Ray Traces Delivery
// ============================================================================

const agentRuntimeLogs = new aws.cloudwatch.LogGroup(
  "agent_runtime_logs",
  {
    name: pulumi.interpolate`/aws/vendedlogs/bedrock-agentcore/${weatherAgent.agentRuntimeId}`,
    retentionInDays: 14,
    tags: {
      Name: `${stackName}-agent-logs`,
      Purpose: "Agent runtime application logs",
      Module: "Observability",
    },
  },
  {
    dependsOn: [weatherAgent],
  },
);

const logs = new aws.cloudwatch.LogDeliverySource(
  "logs",
  {
    name: pulumi.interpolate`${weatherAgent.agentRuntimeId}-logs-source`,
    logType: "APPLICATION_LOGS",
    resourceArn: weatherAgent.agentRuntimeArn,
  },
  {
    dependsOn: [weatherAgent],
  },
);

const logsLogDeliveryDestination = new aws.cloudwatch.LogDeliveryDestination(
  "logs",
  {
    name: pulumi.interpolate`${weatherAgent.agentRuntimeId}-logs-destination`,
    deliveryDestinationConfiguration: {
      destinationResourceArn: agentRuntimeLogs.arn,
    },
    tags: {
      Name: `${stackName}-logs-destination`,
      Module: "Observability",
    },
  },
  {
    dependsOn: [agentRuntimeLogs],
  },
);

const logsLogDelivery = new aws.cloudwatch.LogDelivery(
  "logs",
  {
    deliverySourceName: logs.name,
    deliveryDestinationArn: logsLogDeliveryDestination.arn,
    tags: {
      Name: `${stackName}-logs-delivery`,
      Module: "Observability",
    },
  },
  {
    dependsOn: [logs, logsLogDeliveryDestination],
  },
);

const traces = new aws.cloudwatch.LogDeliverySource(
  "traces",
  {
    name: pulumi.interpolate`${weatherAgent.agentRuntimeId}-traces-source`,
    logType: "TRACES",
    resourceArn: weatherAgent.agentRuntimeArn,
  },
  {
    dependsOn: [weatherAgent],
  },
);

const tracesLogDeliveryDestination = new aws.cloudwatch.LogDeliveryDestination(
  "traces",
  {
    name: pulumi.interpolate`${weatherAgent.agentRuntimeId}-traces-destination`,
    deliveryDestinationType: "XRAY",
    tags: {
      Name: `${stackName}-traces-destination`,
      Module: "Observability",
    },
  },
);

const tracesLogDelivery = new aws.cloudwatch.LogDelivery(
  "traces",
  {
    deliverySourceName: traces.name,
    deliveryDestinationArn: tracesLogDeliveryDestination.arn,
    tags: {
      Name: `${stackName}-traces-delivery`,
      Module: "Observability",
    },
  },
  {
    dependsOn: [traces, tracesLogDeliveryDestination],
  },
);

// ============================================================================
// Outputs
// ============================================================================

export const agentRuntimeId = weatherAgent.agentRuntimeId;
export const agentRuntimeArn = weatherAgent.agentRuntimeArn;
export const agentRuntimeVersion = weatherAgent.agentRuntimeVersion;
export const agentEcrRepositoryUrl = weatherEcr.repositoryUrl;
export const agentExecutionRoleArn = agentExecution.arn;
export const codebuildProjectName = agentImage.name;
export const sourceBucketName = agentSourceBucket.id;
export const resultsBucketName = results.id;
export const browserId = browser.browserId;
export const browserArn = browser.browserArn;
export const codeInterpreterId = codeInterpreter.codeInterpreterId;
export const codeInterpreterArn = codeInterpreter.codeInterpreterArn;
export const memoryId = memory.id;
export const memoryArn = memory.arn;
export const logGroupName = agentRuntimeLogs.name;
export const logGroupArn = agentRuntimeLogs.arn;
export const logsDeliveryId = logsLogDelivery.id;
export const tracesDeliveryId = tracesLogDelivery.id;
export const testScriptCommand = pulumi.interpolate`python test_weather_agent.py ${weatherAgent.agentRuntimeArn}`;
