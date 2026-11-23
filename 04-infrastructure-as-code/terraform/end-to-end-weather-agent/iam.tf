# Data sources
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ============================================================================
# Weather Agent Execution Role - For AgentCore Runtime
# ============================================================================

resource "aws_iam_role" "agent_execution" {
  name = "${var.stack_name}-agent-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AssumeRolePolicy"
      Effect = "Allow"
      Principal = {
        Service = "bedrock-agentcore.amazonaws.com"
      }
      Action = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.id
        }
        ArnLike = {
          "aws:SourceArn" = "arn:aws:bedrock-agentcore:${data.aws_region.current.id}:${data.aws_caller_identity.current.id}:*"
        }
      }
    }]
  })

  tags = {
    Name   = "${var.stack_name}-agent-execution-role"
    Module = "IAM"
    Agent  = "WeatherAgent"
  }
}

# Inline policy for agent execution
resource "aws_iam_role_policy" "agent_execution" {
  name = "AgentCoreExecutionPolicy"
  role = aws_iam_role.agent_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ECR Access
      {
        Sid    = "ECRImageAccess"
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability"
        ]
        Resource = aws_ecr_repository.weather_ecr.arn
      },
      {
        Sid      = "ECRTokenAccess"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      # CloudWatch Logs
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogStreams",
          "logs:CreateLogGroup",
          "logs:DescribeLogGroups",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${data.aws_region.current.id}:${data.aws_caller_identity.current.id}:log-group:/aws/bedrock-agentcore/runtimes/*"
      },
      # X-Ray Tracing
      {
        Sid    = "XRayTracing"
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets"
        ]
        Resource = "*"
      },
      # CloudWatch Metrics
      {
        Sid      = "CloudWatchMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "bedrock-agentcore"
          }
        }
      },
      # Bedrock Model Invocation
      {
        Sid    = "BedrockModelInvocation"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          "arn:aws:bedrock:${data.aws_region.current.id}:${data.aws_caller_identity.current.id}:inference-profile/global.anthropic.claude-sonnet-4-6",
          "arn:aws:bedrock:${data.aws_region.current.id}:${data.aws_caller_identity.current.id}:inference-profile/global.anthropic.claude-haiku-4-5-20251001-v1:0",
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6",
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"
        ]
      },
      # Workload Access Tokens
      {
        Sid    = "GetAgentAccessToken"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:GetWorkloadAccessToken",
          "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
          "bedrock-agentcore:GetWorkloadAccessTokenForUserId"
        ]
        Resource = [
          "arn:aws:bedrock-agentcore:${data.aws_region.current.id}:${data.aws_caller_identity.current.id}:workload-identity-directory/default",
          "arn:aws:bedrock-agentcore:${data.aws_region.current.id}:${data.aws_caller_identity.current.id}:workload-identity-directory/default/workload-identity/*"
        ]
      },
      # Access only the tools, preferences and result object used by the planner.
      {
        Sid      = "WeatherBrowserSessions"
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:StartBrowserSession", "bedrock-agentcore:StopBrowserSession"]
        Resource = aws_bedrockagentcore_browser.browser.browser_arn
      },
      {
        Sid       = "BrowserWebSocket"
        Effect    = "Allow"
        Action    = "bedrock-agentcore:ConnectBrowserAutomationStream"
        Resource  = "*" # No IAM resource type for the signed WebSocket handshake.
        Condition = { StringEquals = { "aws:RequestedRegion" = data.aws_region.current.id } }
      },
      {
        Sid      = "WeatherAnalysis"
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:StartCodeInterpreterSession", "bedrock-agentcore:InvokeCodeInterpreter", "bedrock-agentcore:StopCodeInterpreterSession"]
        Resource = aws_bedrockagentcore_code_interpreter.code_interpreter.code_interpreter_arn
      },
      {
        Sid      = "ReadDemoPreferences"
        Effect   = "Allow"
        Action   = "bedrock-agentcore:ListEvents"
        Resource = aws_bedrockagentcore_memory.memory.arn
        Condition = { StringEquals = {
          "bedrock-agentcore:actorId"   = "user123"
          "bedrock-agentcore:sessionId" = "session456"
        } }
      },
      {
        Sid      = "S3ResultsAccess"
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.results.arn}/results.md"
      }
    ]
  })
}

# ============================================================================
# CodeBuild Service Role - For Docker Image Building
# ============================================================================

resource "aws_iam_role" "codebuild" {
  name = "${var.stack_name}-codebuild-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "codebuild.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = {
    Name   = "${var.stack_name}-codebuild-role"
    Module = "IAM"
  }
}

# Inline policy for CodeBuild
resource "aws_iam_role_policy" "codebuild" {
  name = "CodeBuildPolicy"
  role = aws_iam_role.codebuild.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # CloudWatch Logs
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${data.aws_region.current.id}:${data.aws_caller_identity.current.id}:log-group:/aws/codebuild/*"
      },
      # ECR Access
      {
        Sid    = "ECRAccess"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:GetAuthorizationToken",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload"
        ]
        Resource = [
          aws_ecr_repository.weather_ecr.arn,
          "*"
        ]
      },
      # S3 Source Access
      {
        Sid    = "S3SourceAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion"
        ]
        Resource = "${aws_s3_bucket.agent_source.arn}/*"
      },
      {
        Sid    = "S3BucketAccess"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = aws_s3_bucket.agent_source.arn
      }
    ]
  })
}
