import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import { createHash } from "crypto";
import * as fs from "fs";
import * as path from "path";

// ============================================================================
// Configuration
// ============================================================================

const config = new pulumi.Config();
const orchestratorName = config.get("orchestratorName") || "OrchestratorAgent";
const specialistName = config.get("specialistName") || "SpecialistAgent";
const networkMode = config.get("networkMode") || "PUBLIC";
const imageTag = config.get("imageTag") || "latest";
const stackName = config.get("stackName") || "agentcore-multi-agent";
const ecrRepositoryName = config.get("ecrRepositoryName") || "multi-agent";

// Get the AWS region from the provider configuration
const awsConfig = new pulumi.Config("aws");
const awsRegion = awsConfig.require("region");

// ============================================================================
// Data Sources
// ============================================================================

const currentIdentity = aws.getCallerIdentityOutput({});
const currentRegion = aws.getRegionOutput({});

// ============================================================================
// S3 Buckets for Agent Source Code
// ============================================================================

const orchestratorSourceBucket = new aws.s3.Bucket("orchestrator_source", {
  bucketPrefix: `${stackName}-orch-src-`,
  forceDestroy: true,
  tags: {
    Name: `${stackName}-orchestrator-source`,
    Purpose: "Store Orchestrator agent source code for CodeBuild",
  },
});

const specialistSourceBucket = new aws.s3.Bucket("specialist_source", {
  bucketPrefix: `${stackName}-spec-src-`,
  forceDestroy: true,
  tags: {
    Name: `${stackName}-specialist-source`,
    Purpose: "Store Specialist agent source code for CodeBuild",
  },
});

new aws.s3.BucketPublicAccessBlock("orchestrator_source", {
  bucket: orchestratorSourceBucket.id,
  blockPublicAcls: true,
  blockPublicPolicy: true,
  ignorePublicAcls: true,
  restrictPublicBuckets: true,
});

new aws.s3.BucketPublicAccessBlock("specialist_source", {
  bucket: specialistSourceBucket.id,
  blockPublicAcls: true,
  blockPublicPolicy: true,
  ignorePublicAcls: true,
  restrictPublicBuckets: true,
});

new aws.s3.BucketVersioning("orchestrator_source", {
  bucket: orchestratorSourceBucket.id,
  versioningConfiguration: {
    status: "Enabled",
  },
});

new aws.s3.BucketVersioning("specialist_source", {
  bucket: specialistSourceBucket.id,
  versioningConfiguration: {
    status: "Enabled",
  },
});

// ============================================================================
// Upload Agent Source Code to S3
// ============================================================================

const orchestratorSourceObject = new aws.s3.BucketObjectv2(
  "orchestrator_source",
  {
    bucket: orchestratorSourceBucket.id,
    key: "agent-orchestrator-code.zip",
    source: new pulumi.asset.FileArchive(
      path.resolve(__dirname, "agent-orchestrator-code"),
    ),
    tags: {
      Name: "agent-orchestrator-source-code",
    },
  },
);

const specialistSourceObject = new aws.s3.BucketObjectv2("specialist_source", {
  bucket: specialistSourceBucket.id,
  key: "agent-specialist-code.zip",
  source: new pulumi.asset.FileArchive(
    path.resolve(__dirname, "agent-specialist-code"),
  ),
  tags: {
    Name: "agent-specialist-source-code",
  },
});

// ============================================================================
// ECR Repositories - Container Registries for Agent Images
// ============================================================================

const orchestratorEcr = new aws.ecr.Repository("orchestrator", {
  name: `${stackName}-${ecrRepositoryName}-orchestrator`,
  imageTagMutability: "MUTABLE",
  imageScanningConfiguration: {
    scanOnPush: true,
  },
  forceDelete: true,
  tags: {
    Name: `${stackName}-orchestrator-ecr-repository`,
    Module: "ECR",
  },
});

const specialistEcr = new aws.ecr.Repository("specialist", {
  name: `${stackName}-${ecrRepositoryName}-specialist`,
  imageTagMutability: "MUTABLE",
  imageScanningConfiguration: {
    scanOnPush: true,
  },
  forceDelete: true,
  tags: {
    Name: `${stackName}-specialist-ecr-repository`,
    Module: "ECR",
  },
});

new aws.ecr.RepositoryPolicy("orchestrator", {
  repository: orchestratorEcr.name,
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

new aws.ecr.RepositoryPolicy("specialist", {
  repository: specialistEcr.name,
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

new aws.ecr.LifecyclePolicy("orchestrator", {
  repository: orchestratorEcr.name,
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

new aws.ecr.LifecyclePolicy("specialist", {
  repository: specialistEcr.name,
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
// Orchestrator Agent Execution Role - For AgentCore Runtime
// ============================================================================

const orchestratorExecution = new aws.iam.Role("orchestrator_execution", {
  name: `${stackName}-orchestrator-execution-role`,
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
    Name: `${stackName}-orchestrator-execution-role`,
    Module: "IAM",
  },
});

const orchestratorExecutionManaged = new aws.iam.RolePolicyAttachment(
  "orchestrator_execution_managed",
  {
    role: orchestratorExecution.name,
    policyArn: "arn:aws:iam::aws:policy/BedrockAgentCoreFullAccess",
  },
);

const orchestratorExecutionRolePolicy = new aws.iam.RolePolicy(
  "orchestrator_execution",
  {
    name: "OrchestratorCoreExecutionPolicy",
    role: orchestratorExecution.id,
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
          Resource: orchestratorEcr.arn,
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
      ],
    }),
  },
);

// ============================================================================
// Orchestrator A2A Policy - Allows Orchestrator to Invoke Specialist
// ============================================================================

const orchestratorInvokeSpecialist = new aws.iam.RolePolicy(
  "orchestrator_invoke_specialist",
  {
    name: "OrchestratorInvokeSpecialistPolicy",
    role: orchestratorExecution.id,
    policy: pulumi.jsonStringify({
      Version: "2012-10-17",
      Statement: [
        {
          Sid: "InvokeSpecialistRuntime",
          Effect: "Allow",
          Action: ["bedrock-agentcore:InvokeAgentRuntime"],
          Resource: pulumi
            .all([currentRegion, currentIdentity])
            .apply(
              ([region, identity]) =>
                `arn:aws:bedrock-agentcore:${region.region}:${identity.accountId}:runtime/*`,
            ),
        },
      ],
    }),
  },
);

// ============================================================================
// Specialist Agent Execution Role - For AgentCore Runtime
// ============================================================================

const specialistExecution = new aws.iam.Role("specialist_execution", {
  name: `${stackName}-specialist-execution-role`,
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
    Name: `${stackName}-specialist-execution-role`,
    Module: "IAM",
  },
});

const specialistExecutionManaged = new aws.iam.RolePolicyAttachment(
  "specialist_execution_managed",
  {
    role: specialistExecution.name,
    policyArn: "arn:aws:iam::aws:policy/BedrockAgentCoreFullAccess",
  },
);

const specialistExecutionRolePolicy = new aws.iam.RolePolicy(
  "specialist_execution",
  {
    name: "SpecialistCoreExecutionPolicy",
    role: specialistExecution.id,
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
          Resource: specialistEcr.arn,
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
      ],
    }),
  },
);

// ============================================================================
// CodeBuild Service Role - For Docker Image Building (Both Agents)
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
        Resource: [orchestratorEcr.arn, specialistEcr.arn, "*"],
      },
      {
        Sid: "S3SourceAccess",
        Effect: "Allow",
        Action: ["s3:GetObject", "s3:GetObjectVersion"],
        Resource: [
          pulumi.interpolate`${orchestratorSourceBucket.arn}/*`,
          pulumi.interpolate`${specialistSourceBucket.arn}/*`,
        ],
      },
      {
        Sid: "S3BucketAccess",
        Effect: "Allow",
        Action: ["s3:ListBucket", "s3:GetBucketLocation"],
        Resource: [orchestratorSourceBucket.arn, specialistSourceBucket.arn],
      },
    ],
  }),
});

// ============================================================================
// Build Trigger Lambda - Start and Wait for CodeBuild
// ============================================================================

const orchestratorProjectName = `${stackName}-orchestrator-build`;
const specialistProjectName = `${stackName}-specialist-build`;

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
                Resource: [
                  `arn:aws:codebuild:${region.region}:${identity.accountId}:project/${orchestratorProjectName}`,
                  `arn:aws:codebuild:${region.region}:${identity.accountId}:project/${specialistProjectName}`,
                ],
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
// CodeBuild Projects - Build and Push Docker Images
// ============================================================================

const orchestratorBuildspecContent = fs.readFileSync(
  path.resolve(__dirname, "buildspec-orchestrator.yml"),
  "utf-8",
);
const orchestratorBuildspecFingerprint = createHash("sha256")
  .update(orchestratorBuildspecContent)
  .digest("hex");

const specialistBuildspecContent = fs.readFileSync(
  path.resolve(__dirname, "buildspec-specialist.yml"),
  "utf-8",
);
const specialistBuildspecFingerprint = createHash("sha256")
  .update(specialistBuildspecContent)
  .digest("hex");

const orchestratorImage = new aws.codebuild.Project("orchestrator_image", {
  name: orchestratorProjectName,
  description: `Build Orchestrator agent Docker image for ${stackName}`,
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
        value: orchestratorEcr.name,
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
    location: pulumi.interpolate`${orchestratorSourceBucket.id}/${orchestratorSourceObject.key}`,
    buildspec: orchestratorBuildspecContent,
  },
  logsConfig: {
    cloudwatchLogs: {
      groupName: `/aws/codebuild/${orchestratorProjectName}`,
    },
  },
  tags: {
    Name: `${stackName}-orchestrator-build`,
    Module: "CodeBuild",
  },
});

const specialistImage = new aws.codebuild.Project("specialist_image", {
  name: specialistProjectName,
  description: `Build Specialist agent Docker image for ${stackName}`,
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
        value: specialistEcr.name,
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
    location: pulumi.interpolate`${specialistSourceBucket.id}/${specialistSourceObject.key}`,
    buildspec: specialistBuildspecContent,
  },
  logsConfig: {
    cloudwatchLogs: {
      groupName: `/aws/codebuild/${specialistProjectName}`,
    },
  },
  tags: {
    Name: `${stackName}-specialist-build`,
    Module: "CodeBuild",
  },
});

// ============================================================================
// Trigger CodeBuild - Sequential Build Process
// Specialist builds first (independent), then Orchestrator
// ============================================================================

const triggerBuildSpecialist = new aws.lambda.Invocation(
  "trigger_build_specialist",
  {
    functionName: buildTriggerFunction.name,
    input: pulumi
      .all([specialistImage.name, currentRegion])
      .apply(([projectName, region]) =>
        JSON.stringify({
          projectName,
          region: region.region,
          pollIntervalSeconds: 15,
        }),
      ),
    triggers: {
      sourceVersion: specialistSourceObject.versionId,
      imageTag,
      buildspecSha256: specialistBuildspecFingerprint,
    },
  },
  {
    dependsOn: [
      specialistImage,
      specialistEcr,
      codebuildRolePolicy,
      specialistSourceObject,
      buildTriggerBasicExecution,
      buildTriggerFunction,
    ],
  },
);

const triggerBuildOrchestrator = new aws.lambda.Invocation(
  "trigger_build_orchestrator",
  {
    functionName: buildTriggerFunction.name,
    input: pulumi
      .all([orchestratorImage.name, currentRegion])
      .apply(([projectName, region]) =>
        JSON.stringify({
          projectName,
          region: region.region,
          pollIntervalSeconds: 15,
        }),
      ),
    triggers: {
      sourceVersion: orchestratorSourceObject.versionId,
      imageTag,
      buildspecSha256: orchestratorBuildspecFingerprint,
    },
  },
  {
    dependsOn: [
      orchestratorImage,
      orchestratorEcr,
      codebuildRolePolicy,
      orchestratorSourceObject,
      buildTriggerBasicExecution,
      buildTriggerFunction,
      triggerBuildSpecialist,
    ],
  },
);

// ============================================================================
// Specialist Agent Runtime - Independent Agent
// ============================================================================

const specialistRuntimeName = `${stackName.replace(/-/g, "_")}_${specialistName}`;

const specialistAgent = new aws.bedrock.AgentcoreAgentRuntime(
  "specialist",
  {
    agentRuntimeName: specialistRuntimeName,
    description: `Specialist agent runtime for ${stackName}`,
    roleArn: specialistExecution.arn,
    agentRuntimeArtifact: {
      containerConfiguration: {
        containerUri: pulumi.interpolate`${specialistEcr.repositoryUrl}:${imageTag}`,
      },
    },
    networkConfiguration: {
      networkMode: networkMode,
    },
    environmentVariables: {
      AWS_REGION: awsRegion,
      AWS_DEFAULT_REGION: awsRegion,
    },
  },
  {
    dependsOn: [
      triggerBuildSpecialist,
      specialistExecutionRolePolicy,
      specialistExecutionManaged,
    ],
  },
);

// ============================================================================
// Orchestrator Agent Runtime - Depends on Specialist Agent
// ============================================================================

const orchestratorRuntimeName = `${stackName.replace(/-/g, "_")}_${orchestratorName}`;

const orchestratorAgent = new aws.bedrock.AgentcoreAgentRuntime(
  "orchestrator",
  {
    agentRuntimeName: orchestratorRuntimeName,
    description: `Orchestrator agent runtime for ${stackName}`,
    roleArn: orchestratorExecution.arn,
    agentRuntimeArtifact: {
      containerConfiguration: {
        containerUri: pulumi.interpolate`${orchestratorEcr.repositoryUrl}:${imageTag}`,
      },
    },
    networkConfiguration: {
      networkMode: networkMode,
    },
    environmentVariables: {
      AWS_REGION: awsRegion,
      AWS_DEFAULT_REGION: awsRegion,
      SPECIALIST_ARN: specialistAgent.agentRuntimeArn,
    },
  },
  {
    dependsOn: [
      specialistAgent,
      triggerBuildOrchestrator,
      orchestratorExecutionRolePolicy,
      orchestratorInvokeSpecialist,
      orchestratorExecutionManaged,
    ],
  },
);

// ============================================================================
// Outputs
// ============================================================================

export const orchestratorRuntimeId = orchestratorAgent.agentRuntimeId;
export const orchestratorRuntimeArn = orchestratorAgent.agentRuntimeArn;
export const orchestratorRuntimeVersion = orchestratorAgent.agentRuntimeVersion;
export const orchestratorEcrRepositoryUrl = orchestratorEcr.repositoryUrl;
export const orchestratorExecutionRoleArn = orchestratorExecution.arn;

export const specialistRuntimeId = specialistAgent.agentRuntimeId;
export const specialistRuntimeArn = specialistAgent.agentRuntimeArn;
export const specialistRuntimeVersion = specialistAgent.agentRuntimeVersion;
export const specialistEcrRepositoryUrl = specialistEcr.repositoryUrl;
export const specialistExecutionRoleArn = specialistExecution.arn;

export const orchestratorCodebuildProjectName = orchestratorImage.name;
export const specialistCodebuildProjectName = specialistImage.name;
export const orchestratorSourceBucketName = orchestratorSourceBucket.id;
export const specialistSourceBucketName = specialistSourceBucket.id;

export const testScriptCommand = pulumi.interpolate`python test_multi_agent.py ${orchestratorAgent.agentRuntimeArn}`;
