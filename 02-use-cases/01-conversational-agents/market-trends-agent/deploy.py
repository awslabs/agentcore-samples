#!/usr/bin/env python3
"""
Complete Market Trends Agent Deployment Script
Handles IAM role creation, permissions, container deployment, and agent setup

IAM Role Setup
--------------
The execution role trusts bedrock-agentcore.amazonaws.com with a condition
scoped to A/B test resources in your account:

    "Condition": {
        "StringEquals": {"aws:SourceAccount": "<account-id>"},
        "ArnLike":      {"aws:SourceArn": "arn:aws:bedrock-agentcore:*:<account-id>:*"}
    }

The permissions policy uses explicit least-privilege statements:

  BedrockModelInvocation     — bedrock:InvokeModel* scoped to foundation models
  ECRImageAccess             — ecr:BatchGetImage, GetDownloadUrlForLayer
  CloudWatch Logs (runtime)  — CreateLogGroup/Stream, PutLogEvents scoped to runtimes/*
  XRay                       — PutTraceSegments, PutTelemetryRecords, GetSamplingRules/Targets
  GetAgentAccessToken        — GetWorkloadAccessToken* scoped to workload identity
  BedrockAgentCoreMemory     — memory CRUD scoped to memory/*
  BedrockAgentCoreBrowser    — browser session ops scoped to browser resources
  SSMParameterAccess         — GetParameter/PutParameter scoped to market-trends-agent/*
  InvokeAgentRuntime         — bedrock-agentcore:InvokeAgentRuntime scoped to runtime/*
                               (required when gateway forwards requests via GATEWAY_IAM_ROLE)
  ABTestAgentCoreResources   — GetGateway, GetGatewayTarget, ListGatewayTargets,
                               CreateGatewayRule, UpdateGatewayRule, GetGatewayRule,
                               DeleteGatewayRule, ListGatewayRules,
                               GetOnlineEvaluationConfig, GetEvaluator,
                               GetConfigurationBundle, GetConfigurationBundleVersion,
                               ListConfigurationBundleVersions
                               scoped to account ARNs with aws:ResourceAccount condition
  ABTestCloudWatchLogs       — CreateLogGroup, CreateLogStream, PutLogEvents,
                               DescribeLogGroups/Streams, DescribeIndexPolicies, PutIndexPolicy,
                               StartQuery, GetQueryResults, StopQuery, FilterLogEvents, GetLogEvents
                               scoped to evaluations/*, runtimes/*, and aws/spans log groups
"""

import argparse
import json
import logging
import boto3
import time
from pathlib import Path

from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class MarketTrendsAgentDeployer:
    """Complete deployer for Market Trends Agent"""

    def __init__(self, region: str = "us-east-1"):
        self.region = region
        self.iam_client = boto3.client("iam", region_name=region)
        self.ssm_client = boto3.client("ssm", region_name=region)

    def create_execution_role(self, role_name: str) -> str:
        """Create IAM execution role with least-privilege permissions.

        Trust policy: bedrock-agentcore.amazonaws.com, conditioned on
        aws:SourceAccount and aws:SourceArn scoped to ab-test/* resources.

        Permissions: explicit statements for runtime, memory, browser, SSM,
        A/B test gateway/eval/bundle reads, and CloudWatch Logs score aggregation.
        See module docstring for the full statement breakdown.
        """

        # Get account ID for trust policy and resource ARNs
        account_id = boto3.client("sts").get_caller_identity()["Account"]

        # Trust policy for Bedrock AgentCore
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                    "Condition": {
                        "StringEquals": {
                            "aws:SourceAccount": account_id,
                        },
                        "ArnLike": {
                            "aws:SourceArn": f"arn:aws:bedrock-agentcore:*:{account_id}:ab-test/*",
                        },
                    },
                }
            ],
        }

        # Comprehensive execution policy with least privilege permissions
        execution_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "BedrockModelInvocation",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                    ],
                    "Resource": [
                        "arn:aws:bedrock:*::foundation-model/*",
                        f"arn:aws:bedrock:{self.region}:{account_id}:*",
                    ],
                },
                {
                    "Sid": "ECRImageAccess",
                    "Effect": "Allow",
                    "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                    "Resource": [f"arn:aws:ecr:{self.region}:{account_id}:repository/*"],
                },
                {
                    "Effect": "Allow",
                    "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                    "Resource": [
                        f"arn:aws:logs:{self.region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["logs:DescribeLogGroups"],
                    "Resource": [f"arn:aws:logs:{self.region}:{account_id}:log-group:*"],
                },
                {
                    "Effect": "Allow",
                    "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                    "Resource": [
                        f"arn:aws:logs:{self.region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
                    ],
                },
                {
                    "Sid": "ECRTokenAccess",
                    "Effect": "Allow",
                    "Action": ["ecr:GetAuthorizationToken"],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "xray:PutTraceSegments",
                        "xray:PutTelemetryRecords",
                        "xray:GetSamplingRules",
                        "xray:GetSamplingTargets",
                    ],
                    "Resource": ["*"],
                },
                {
                    "Effect": "Allow",
                    "Resource": "*",
                    "Action": "cloudwatch:PutMetricData",
                    "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
                },
                {
                    "Sid": "GetAgentAccessToken",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetWorkloadAccessToken",
                        "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                        "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                    ],
                    "Resource": [
                        f"arn:aws:bedrock-agentcore:{self.region}:{account_id}:workload-identity-directory/default",
                        f"arn:aws:bedrock-agentcore:{self.region}:{account_id}:workload-identity-directory/default/workload-identity/market-trends-agent-*",
                    ],
                },
                {
                    "Sid": "BedrockAgentCoreMemoryOperations",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:ListMemories",
                        "bedrock-agentcore:ListEvents",
                        "bedrock-agentcore:CreateEvent",
                        "bedrock-agentcore:RetrieveMemories",
                        "bedrock-agentcore:GetMemoryStrategies",
                        "bedrock-agentcore:DeleteMemory",
                        "bedrock-agentcore:GetMemory",
                        "bedrock-agentcore:RetrieveMemoryRecords",
                    ],
                    "Resource": [f"arn:aws:bedrock-agentcore:{self.region}:{account_id}:memory/*"],
                },
                {
                    "Sid": "BedrockAgentCoreBrowserOperations",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetBrowserSession",
                        "bedrock-agentcore:StartBrowserSession",
                        "bedrock-agentcore:StopBrowserSession",
                        "bedrock-agentcore:CreateBrowserSession",
                        "bedrock-agentcore:DeleteBrowserSession",
                        "bedrock-agentcore:ConnectBrowserAutomationStream",
                    ],
                    "Resource": [
                        f"arn:aws:bedrock-agentcore:{self.region}:{account_id}:browser-custom/*",
                        "arn:aws:bedrock-agentcore:*:aws:browser/*",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "ssm:GetParameter",
                        "ssm:PutParameter",
                        "ssm:DeleteParameter",
                    ],
                    "Resource": f"arn:aws:ssm:{self.region}:{account_id}:parameter/bedrock-agentcore/market-trends-agent/*",
                    "Sid": "SSMParameterAccess",
                },
                {
                    "Sid": "InvokeAgentRuntime",
                    "Effect": "Allow",
                    "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                    "Resource": [f"arn:aws:bedrock-agentcore:{self.region}:{account_id}:runtime/*"],
                },
                {
                    "Sid": "ABTestAgentCoreResources",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetGateway",
                        "bedrock-agentcore:GetGatewayTarget",
                        "bedrock-agentcore:ListGatewayTargets",
                        "bedrock-agentcore:CreateGatewayRule",
                        "bedrock-agentcore:UpdateGatewayRule",
                        "bedrock-agentcore:GetGatewayRule",
                        "bedrock-agentcore:DeleteGatewayRule",
                        "bedrock-agentcore:ListGatewayRules",
                        "bedrock-agentcore:GetOnlineEvaluationConfig",
                        "bedrock-agentcore:GetEvaluator",
                        "bedrock-agentcore:GetConfigurationBundle",
                        "bedrock-agentcore:GetConfigurationBundleVersion",
                        "bedrock-agentcore:ListConfigurationBundleVersions",
                    ],
                    "Resource": f"arn:aws:bedrock-agentcore:*:{account_id}:*",
                    "Condition": {
                        "StringEquals": {
                            "aws:ResourceAccount": account_id,
                        }
                    },
                },
                {
                    "Sid": "ABTestCloudWatchLogs",
                    "Effect": "Allow",
                    "Action": [
                        "logs:DescribeLogGroups",
                        "logs:DescribeIndexPolicies",
                        "logs:PutIndexPolicy",
                        "logs:StartQuery",
                        "logs:GetQueryResults",
                        "logs:StopQuery",
                        "logs:FilterLogEvents",
                        "logs:GetLogEvents",
                    ],
                    "Resource": [
                        f"arn:aws:logs:*:{account_id}:log-group:/aws/bedrock-agentcore/evaluations/*",
                        f"arn:aws:logs:*:{account_id}:log-group:/aws/bedrock-agentcore/evaluations/*:*",
                        f"arn:aws:logs:*:{account_id}:log-group:aws/spans",
                        f"arn:aws:logs:*:{account_id}:log-group:aws/spans:*",
                    ],
                },
            ],
        }

        try:
            # Create the role
            logger.info(f"🔐 Creating IAM role: {role_name}")
            role_response = self.iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Execution role for Market Trends Agent with comprehensive permissions",
            )

            # Attach the comprehensive execution policy
            logger.info(f"📋 Attaching comprehensive execution policy to role: {role_name}")
            self.iam_client.put_role_policy(
                RoleName=role_name,
                PolicyName="MarketTrendsAgentComprehensivePolicy",
                PolicyDocument=json.dumps(execution_policy),
            )

            role_arn = role_response["Role"]["Arn"]
            logger.info(f"✅ Created IAM role with ARN: {role_arn}")

            # Wait for role to propagate
            logger.info("⏳ Waiting for role to propagate...")
            time.sleep(10)

            return role_arn

        except self.iam_client.exceptions.EntityAlreadyExistsException:
            logger.info(f"📋 IAM role {role_name} already exists, using existing role")

            # Update the existing role with comprehensive permissions
            logger.info("📋 Updating existing role with comprehensive permissions...")
            self.iam_client.put_role_policy(
                RoleName=role_name,
                PolicyName="MarketTrendsAgentComprehensivePolicy",
                PolicyDocument=json.dumps(execution_policy),
            )

            role_response = self.iam_client.get_role(RoleName=role_name)
            return role_response["Role"]["Arn"]

        except Exception as e:
            logger.error(f"❌ Failed to create IAM role: {e}")
            raise

    def create_agentcore_memory(self) -> str:
        """Create AgentCore Memory and store ARN in SSM Parameter Store"""
        try:
            from bedrock_agentcore.memory import MemoryClient
            from bedrock_agentcore.memory.constants import StrategyType

            memory_name = "MarketTrendsAgentMultiStrategy"
            memory_client = MemoryClient(region_name=self.region)

            # Check if memory ARN already exists in SSM
            param_name = "/bedrock-agentcore/market-trends-agent/memory-arn"
            try:
                response = self.ssm_client.get_parameter(Name=param_name)
                existing_memory_arn = response["Parameter"]["Value"]
                logger.info(f"✅ Found existing memory ARN in SSM: {existing_memory_arn}")
                return existing_memory_arn
            except self.ssm_client.exceptions.ParameterNotFound:
                logger.info("No existing memory ARN found in SSM, creating new memory...")

            # Check if memory exists by name
            try:
                memories = memory_client.list_memories()
                for memory in memories:
                    if memory.get("name") == memory_name and memory.get("status") == "ACTIVE":
                        memory_arn = memory["arn"]
                        logger.info(f"✅ Found existing active memory: {memory_arn}")

                        # Store in SSM for future use
                        self.ssm_client.put_parameter(
                            Name=param_name,
                            Value=memory_arn,
                            Type="String",
                            Overwrite=True,
                            Description="Memory ARN for Market Trends Agent",
                        )
                        logger.info("💾 Stored existing memory ARN in SSM")
                        return memory_arn
            except Exception as e:
                logger.warning(f"Error checking existing memories: {e}")

            # Create new memory
            logger.info("🧠 Creating new AgentCore Memory...")

            strategies = [
                {
                    StrategyType.USER_PREFERENCE.value: {
                        "name": "BrokerPreferences",
                        "description": "Captures broker preferences, risk tolerance, and investment styles",
                        "namespaces": ["market-trends/broker/{actorId}/preferences"],
                    }
                },
                {
                    StrategyType.SEMANTIC.value: {
                        "name": "MarketTrendsSemantic",
                        "description": "Stores financial facts, market analysis, and investment insights",
                        "namespaces": ["market-trends/broker/{actorId}/semantic"],
                    }
                },
            ]

            memory = memory_client.create_memory_and_wait(
                name=memory_name,
                description="Market Trends Agent with multi-strategy memory for broker financial interests",
                strategies=strategies,
                event_expiry_days=90,
                max_wait=300,
                poll_interval=10,
            )

            memory_arn = memory["arn"]
            logger.info(f"✅ Memory created successfully: {memory_arn}")

            # Store memory ARN in SSM Parameter Store
            self.ssm_client.put_parameter(
                Name=param_name,
                Value=memory_arn,
                Type="String",
                Overwrite=True,
                Description="Memory ARN for Market Trends Agent",
            )
            logger.info("💾 Memory ARN stored in SSM Parameter Store")

            return memory_arn

        except Exception as e:
            logger.error(f"❌ Failed to create memory: {e}")
            raise

    def _trigger_codebuild(self, agent_name: str) -> str:
        """Start the CodeBuild container build and wait for completion.

        Returns the ECR image URI on success. Raises RuntimeError on failure.
        The CodeBuild project is created by ``agentcore deploy`` on first run.
        """
        codebuild = boto3.client("codebuild", region_name=self.region)
        project_name = f"bedrock-agentcore-{agent_name}-builder"

        try:
            projects = codebuild.batch_get_projects(names=[project_name])
            if not projects.get("projects"):
                raise RuntimeError(
                    f"CodeBuild project '{project_name}' not found.\n"
                    "Run 'agentcore deploy' once to bootstrap the build pipeline, "
                    "then re-run this script for subsequent deploys."
                )
        except Exception as exc:
            if "CodeBuild project" in str(exc):
                raise
            raise RuntimeError(f"Could not reach CodeBuild: {exc}") from exc

        logger.info("Starting CodeBuild project: %s", project_name)
        build_resp = codebuild.start_build(projectName=project_name)
        build_id = build_resp["build"]["id"]
        logger.info("Build started: %s — waiting for completion...", build_id)

        # Poll until the build finishes (max ~20 min).
        import time as _time

        for _ in range(120):
            _time.sleep(10)
            builds = codebuild.batch_get_builds(ids=[build_id])["builds"]
            status = builds[0]["buildStatus"] if builds else "UNKNOWN"
            if status == "SUCCEEDED":
                break
            if status not in ("IN_PROGRESS",):
                raise RuntimeError(f"CodeBuild failed with status: {status}")

        account_id = boto3.client("sts").get_caller_identity()["Account"]
        ecr_uri = f"{account_id}.dkr.ecr.{self.region}.amazonaws.com/bedrock-agentcore-{agent_name}:latest"
        logger.info("Container ready at: %s", ecr_uri)
        return ecr_uri

    def _ensure_runtime(
        self,
        agent_name: str,
        execution_role_arn: str,
        ecr_image_uri: str,
    ) -> str:
        """Create or update the AgentCore runtime via bedrock-agentcore-control."""
        control = boto3.client("bedrock-agentcore-control", region_name=self.region)
        artifact = {"containerConfiguration": {"containerUri": ecr_image_uri}}

        # Check whether a runtime with this name already exists.
        try:
            paginator = control.get_paginator("list_agent_runtimes")
            for page in paginator.paginate():
                for rt in page.get("agentRuntimeSummaries", []):
                    if rt.get("agentRuntimeName") == agent_name:
                        runtime_id = rt["agentRuntimeId"]
                        logger.info("Updating existing runtime: %s", runtime_id)
                        control.update_agent_runtime(
                            agentRuntimeId=runtime_id,
                            agentRuntimeArtifact=artifact,
                            roleArn=execution_role_arn,
                        )
                        return rt["agentRuntimeArn"]
        except ClientError:
            pass

        # No existing runtime — create one.
        logger.info("Creating new AgentCore runtime: %s", agent_name)
        resp = control.create_agent_runtime(
            agentRuntimeName=agent_name,
            agentRuntimeArtifact=artifact,
            roleArn=execution_role_arn,
            networkConfiguration={"networkMode": "PUBLIC"},
            protocolConfiguration={"serverProtocol": "HTTP"},
        )
        return resp["agentRuntimeArn"]

    def deploy_agent(
        self,
        agent_name: str,
        role_name: str = "MarketTrendsAgentRole",
        entrypoint: str = "market_trends_agent.py",
        requirements_file: str = None,
    ) -> str:
        """Deploy the Market Trends Agent using the AgentCore SDK and boto3.

        Steps:
          1. Create AgentCore Memory (bedrock_agentcore SDK).
          2. Create the IAM execution role (boto3).
          3. Build and push the container via CodeBuild (boto3).
          4. Create or update the AgentCore runtime (bedrock-agentcore-control).
        """
        try:
            logger.info("Starting Market Trends Agent Deployment")
            logger.info("  Agent Name : %s", agent_name)
            logger.info("  Region     : %s", self.region)
            logger.info("  Entrypoint : %s", entrypoint)

            # Step 1: Create AgentCore Memory (uses bedrock_agentcore SDK)
            memory_arn = self.create_agentcore_memory()

            # Step 2: Create execution role (uses boto3 IAM)
            execution_role_arn = self.create_execution_role(role_name)

            # Step 3: Build container via CodeBuild
            ecr_image_uri = self._trigger_codebuild(agent_name)

            # Step 4: Create / update the runtime via bedrock-agentcore-control
            runtime_arn = self._ensure_runtime(agent_name, execution_role_arn, ecr_image_uri)

            arn_file = Path(".agent_arn")
            arn_file.write_text(runtime_arn)

            agent_id = runtime_arn.split("/")[-1]
            log_group = f"/aws/bedrock-agentcore/runtimes/{agent_id}-DEFAULT"
            logger.info("Market Trends Agent deployed successfully!")
            logger.info("  Runtime ARN : %s", runtime_arn)
            logger.info("  Memory ARN  : %s", memory_arn)
            logger.info("  Region      : %s", self.region)
            logger.info("  Exec Role   : %s", execution_role_arn)
            logger.info("  ARN saved to: %s", arn_file)
            logger.info("  CW Logs     : %s", log_group)
            logger.info("Next steps:")
            logger.info("  Test  : uv run python test_agent.py")
            logger.info("  Evals : uv run python evaluators/scripts/deploy.py")

            return runtime_arn

        except RuntimeError as exc:
            logger.error("Deployment failed: %s", exc)
            return None
        except Exception as exc:
            import traceback

            logger.error("Deployment failed: %s\n%s", exc, traceback.format_exc())
            return None


def check_prerequisites():
    """Check if all prerequisites are met"""
    logger.info("🔍 Checking prerequisites...")

    # Check if required files exist
    required_files = [
        "market_trends_agent.py",
        "tools/browser_tool.py",
        "tools/broker_card_tools.py",
        "tools/memory_tools.py",
        "tools/__init__.py",
    ]

    # Check for dependency files (either pyproject.toml or requirements.txt)
    has_pyproject = Path("pyproject.toml").exists()
    has_requirements = Path("requirements.txt").exists()

    if not has_pyproject and not has_requirements:
        logger.error("❌ No dependency file found (pyproject.toml or requirements.txt)")
        return False

    if has_pyproject:
        logger.info("✅ Found pyproject.toml - will use uv for dependency management")
    elif has_requirements:
        logger.info("✅ Found requirements.txt - will use pip for dependency management")

    missing_files = []
    for file in required_files:
        if not Path(file).exists():
            missing_files.append(file)

    if missing_files:
        logger.error(f"❌ Missing required files: {missing_files}")
        return False

    # Note: Docker/Podman not required - AgentCore uses AWS CodeBuild for container building
    logger.info("✅ Container building will use AWS CodeBuild (no local Docker required)")

    # Check AWS credentials
    try:
        boto3.client("sts").get_caller_identity()
        logger.info("✅ AWS credentials configured")
    except Exception as e:
        logger.error(f"❌ AWS credentials not configured: {e}")
        return False

    logger.info("✅ All prerequisites met")
    return True


def main():
    """Main deployment function"""
    parser = argparse.ArgumentParser(description="Deploy Market Trends Agent to Amazon Bedrock AgentCore Runtime")
    parser.add_argument(
        "--agent-name",
        default="market_trends_agent",
        help="Name for the agent (default: market_trends_agent)",
    )
    parser.add_argument(
        "--role-name",
        default="MarketTrendsAgentRole",
        help="IAM role name (default: MarketTrendsAgentRole)",
    )
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--skip-checks", action="store_true", help="Skip prerequisite checks")

    args = parser.parse_args()

    # Check prerequisites
    if not args.skip_checks and not check_prerequisites():
        logger.error("❌ Prerequisites not met. Fix issues above or use --skip-checks")
        exit(1)

    # Create deployer and deploy
    deployer = MarketTrendsAgentDeployer(region=args.region)

    runtime_arn = deployer.deploy_agent(agent_name=args.agent_name, role_name=args.role_name)

    if runtime_arn:
        logger.info("\n🎯 Deployment completed successfully!")
        logger.info("Run 'python test_agent.py' to test your deployed agent.")
    else:
        logger.error("❌ Deployment failed")
        exit(1)


if __name__ == "__main__":
    main()
