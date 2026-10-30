#!/usr/bin/env python3
"""
Data Analyst Conversational Assistant — AgentCore Deployment Script

Deploys the agent backend using the native bedrock-agentcore SDK and boto3.
This is an alternative to CDK for teams that prefer scripted/CI-CD deployments.

Resources created:
  1. IAM execution role (bedrock-agentcore.amazonaws.com trust)
  2. AgentCore Memory (STM + LTM with semantic facts strategy)
  3. AgentCore Runtime (container-based, ARM64)
  4. AgentCore Gateway + Lambda target (PostgreSQL query tools)

Prerequisites:
  - AWS credentials configured (aws configure)
  - Docker installed and running (for container build)
  - First-time users: run 'agentcore deploy' once to bootstrap CodeBuild project,
    then use this script for subsequent deployments
  - Aurora PostgreSQL cluster already deployed (via CDK stack)

Usage:
  python deploy.py --region us-east-1
"""

import argparse
import json
import logging
import time
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

AGENT_NAME = "DataAnalystConversationalAgent"
MEMORY_NAME = "DataAnalystMemory"
GATEWAY_NAME = "DataAnalystToolsGateway"
ROLE_NAME = "DataAnalystAgentRole"


class DataAnalystDeployer:
    """Deploys the Data Analyst Conversational Assistant to AgentCore."""

    def __init__(self, region: str = "us-east-1"):
        self.region = region
        self.account_id = boto3.client("sts").get_caller_identity()["Account"]
        self.iam_client = boto3.client("iam", region_name=region)
        self.control_client = boto3.client("bedrock-agentcore-control", region_name=region)
        self.bedrock_client = boto3.client("bedrock", region_name=region)
        self.lambda_client = boto3.client("lambda", region_name=region)

    # ------------------------------------------------------------------
    # Step 1: IAM Execution Role
    # ------------------------------------------------------------------

    def create_execution_role(self) -> str:
        """Create IAM role with permissions for Runtime, Memory, Gateway, and model invocation."""
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": ["sts:AssumeRole", "sts:TagSession"],
                }
            ],
        }

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
                        f"arn:aws:bedrock:{self.region}:{self.account_id}:*",
                        "arn:aws:bedrock:*:*:inference-profile/*",
                    ],
                },
                {
                    "Sid": "ECRAccess",
                    "Effect": "Allow",
                    "Action": [
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:BatchGetImage",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:GetAuthorizationToken",
                    ],
                    "Resource": ["*"],
                },
                {
                    "Sid": "CloudWatchLogs",
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogGroups",
                        "logs:DescribeLogStreams",
                    ],
                    "Resource": [f"arn:aws:logs:{self.region}:{self.account_id}:log-group:/aws/bedrock-agentcore/*"],
                },
                {
                    "Sid": "XRayTracing",
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
                    "Sid": "CloudWatchMetrics",
                    "Effect": "Allow",
                    "Action": ["cloudwatch:PutMetricData"],
                    "Resource": ["*"],
                    "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
                },
                {
                    "Sid": "AgentCoreMemory",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetMemory",
                        "bedrock-agentcore:GetMemoryRecord",
                        "bedrock-agentcore:RetrieveMemoryRecords",
                        "bedrock-agentcore:ListMemoryRecords",
                        "bedrock-agentcore:DeleteMemoryRecord",
                        "bedrock-agentcore:CreateEvent",
                        "bedrock-agentcore:ListSessions",
                        "bedrock-agentcore:ListEvents",
                        "bedrock-agentcore:GetEvent",
                    ],
                    "Resource": [f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:memory/*"],
                },
                {
                    "Sid": "AgentCoreIdentity",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetWorkloadAccessToken",
                        "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                        "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                    ],
                    "Resource": [
                        f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:workload-identity-directory/default",
                        f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:workload-identity-directory/default/workload-identity/*",
                    ],
                },
                {
                    "Sid": "RDSDataAPI",
                    "Effect": "Allow",
                    "Action": [
                        "rds-data:ExecuteStatement",
                        "rds-data:BatchExecuteStatement",
                    ],
                    "Resource": [f"arn:aws:rds:{self.region}:{self.account_id}:cluster:*"],
                },
                {
                    "Sid": "SecretsManager",
                    "Effect": "Allow",
                    "Action": ["secretsmanager:GetSecretValue"],
                    "Resource": [f"arn:aws:secretsmanager:{self.region}:{self.account_id}:secret:*"],
                },
                {
                    "Sid": "DynamoDB",
                    "Effect": "Allow",
                    "Action": [
                        "dynamodb:Query",
                        "dynamodb:Scan",
                        "dynamodb:GetItem",
                        "dynamodb:PutItem",
                        "dynamodb:UpdateItem",
                    ],
                    "Resource": [f"arn:aws:dynamodb:{self.region}:{self.account_id}:table/*"],
                },
                {
                    "Sid": "GatewayInvoke",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:InvokeAgentRuntime",
                        "bedrock-agentcore:GetGateway",
                        "bedrock-agentcore:GetGatewayTarget",
                        "bedrock-agentcore:ListGatewayTargets",
                    ],
                    "Resource": [f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:*"],
                },
            ],
        }

        try:
            logger.info("Creating IAM execution role: %s", ROLE_NAME)
            resp = self.iam_client.create_role(
                RoleName=ROLE_NAME,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Execution role for Data Analyst Conversational Agent (AgentCore)",
            )
            role_arn = resp["Role"]["Arn"]
            logger.info("Created role: %s", role_arn)
        except self.iam_client.exceptions.EntityAlreadyExistsException:
            logger.info("Role %s already exists, updating policy", ROLE_NAME)
            resp = self.iam_client.get_role(RoleName=ROLE_NAME)
            role_arn = resp["Role"]["Arn"]

        self.iam_client.put_role_policy(
            RoleName=ROLE_NAME,
            PolicyName="DataAnalystAgentPolicy",
            PolicyDocument=json.dumps(execution_policy),
        )

        time.sleep(10)
        return role_arn

    # ------------------------------------------------------------------
    # Step 2: AgentCore Memory
    # ------------------------------------------------------------------

    def create_memory(self) -> str:
        """Create AgentCore Memory with STM + LTM semantic facts strategy."""
        from bedrock_agentcore.memory import MemoryClient
        from bedrock_agentcore.memory.constants import StrategyType

        memory_client = MemoryClient(region_name=self.region)

        try:
            memories = memory_client.list_memories()
            for memory in memories:
                if memory.get("name") == MEMORY_NAME and memory.get("status") == "ACTIVE":
                    logger.info("Found existing memory: %s", memory["arn"])
                    return memory["arn"]
        except Exception as e:
            logger.warning("Error checking existing memories: %s", e)

        logger.info("Creating AgentCore Memory: %s", MEMORY_NAME)
        strategies = [
            {
                StrategyType.SEMANTIC.value: {
                    "name": "Facts",
                    "description": "Extracts facts from data analysis conversations",
                    "namespaces": ["/facts/{actorId}"],
                }
            },
        ]

        memory = memory_client.create_memory_and_wait(
            name=MEMORY_NAME,
            description="Memory for data analyst conversational assistant — STM + LTM semantic facts",
            strategies=strategies,
            event_expiry_days=90,
            max_wait=300,
            poll_interval=10,
        )

        logger.info("Memory created: %s", memory["arn"])
        return memory["arn"]

    # ------------------------------------------------------------------
    # Step 3: AgentCore Runtime
    # ------------------------------------------------------------------

    def deploy_runtime(self, role_arn: str, memory_id: str, env_vars: dict) -> str:
        """Create or update AgentCore Runtime (container-based).

        Requires a prior 'agentcore deploy' to bootstrap the CodeBuild project
        and ECR repository, OR a pre-built container URI passed via env_vars.
        """
        container_uri = env_vars.pop("CONTAINER_URI", None)
        if not container_uri:
            container_uri = self._trigger_codebuild()

        runtime_env = {
            "MEMORY_ID": memory_id,
            "BEDROCK_MODEL_ID": env_vars.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
            "READONLY_SECRET_ARN": env_vars.get("READONLY_SECRET_ARN", ""),
            "AURORA_RESOURCE_ARN": env_vars.get("AURORA_RESOURCE_ARN", ""),
            "DATABASE_NAME": env_vars.get("DATABASE_NAME", "video_games_sales"),
            "QUESTION_ANSWERS_TABLE": env_vars.get("QUESTION_ANSWERS_TABLE", ""),
            "MAX_RESPONSE_SIZE_BYTES": "1048576",
        }

        artifact = {"containerConfiguration": {"containerUri": container_uri}}

        try:
            paginator = self.control_client.get_paginator("list_agent_runtimes")
            for page in paginator.paginate():
                for rt in page.get("agentRuntimeSummaries", []):
                    if rt.get("agentRuntimeName") == AGENT_NAME:
                        runtime_id = rt["agentRuntimeId"]
                        logger.info("Updating existing runtime: %s", runtime_id)
                        self.control_client.update_agent_runtime(
                            agentRuntimeId=runtime_id,
                            agentRuntimeArtifact=artifact,
                            roleArn=role_arn,
                            environmentVariables=runtime_env,
                        )
                        return rt["agentRuntimeArn"]
        except ClientError:
            pass

        logger.info("Creating AgentCore Runtime: %s", AGENT_NAME)
        resp = self.control_client.create_agent_runtime(
            agentRuntimeName=AGENT_NAME,
            agentRuntimeArtifact=artifact,
            roleArn=role_arn,
            networkConfiguration={"networkMode": "PUBLIC"},
            protocolConfiguration={"serverProtocol": "HTTP"},
            environmentVariables=runtime_env,
            description="Data analyst conversational assistant",
        )
        logger.info("Runtime created: %s", resp["agentRuntimeArn"])
        return resp["agentRuntimeArn"]

    def _trigger_codebuild(self) -> str:
        """Build container via CodeBuild (bootstrapped by 'agentcore deploy')."""
        codebuild = boto3.client("codebuild", region_name=self.region)
        project_name = f"bedrock-agentcore-{AGENT_NAME}-builder"

        projects = codebuild.batch_get_projects(names=[project_name])
        if not projects.get("projects"):
            raise RuntimeError(
                f"CodeBuild project '{project_name}' not found. "
                "Run 'agentcore deploy' once to bootstrap, then re-run this script."
            )

        logger.info("Starting CodeBuild: %s", project_name)
        build_resp = codebuild.start_build(projectName=project_name)
        build_id = build_resp["build"]["id"]

        for _ in range(120):
            time.sleep(10)
            builds = codebuild.batch_get_builds(ids=[build_id])["builds"]
            status = builds[0]["buildStatus"] if builds else "UNKNOWN"
            if status == "SUCCEEDED":
                break
            if status not in ("IN_PROGRESS",):
                raise RuntimeError(f"CodeBuild failed: {status}")

        ecr_uri = f"{self.account_id}.dkr.ecr.{self.region}.amazonaws.com/bedrock-agentcore-{AGENT_NAME}:latest"
        logger.info("Container ready: %s", ecr_uri)
        return ecr_uri

    # ------------------------------------------------------------------
    # Step 4: AgentCore Gateway + Lambda Target
    # ------------------------------------------------------------------

    def create_gateway(self, db_tools_lambda_arn: str) -> dict:
        """Create Gateway and register PostgreSQL tools as a Lambda target.

        The Lambda handles tool routing via context.client_context.custom['bedrockAgentCoreToolName'].
        No standalone MCP server is needed — Gateway provides the MCP protocol layer.
        """
        tool_schema = [
            {
                "name": "get_tables_information",
                "description": "Get database schema metadata including table structures, columns, and relationships",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "execute_sql_query",
                "description": "Execute a read-only SQL query against the PostgreSQL database",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sql_query": {
                            "type": "string",
                            "description": "The SQL query to execute (read-only)",
                        },
                        "description": {
                            "type": "string",
                            "description": "Description of what the query analyzes",
                        },
                    },
                    "required": ["sql_query", "description"],
                },
            },
        ]

        gateway_id = None
        try:
            paginator = self.control_client.get_paginator("list_gateways")
            for page in paginator.paginate():
                for gw in page.get("gateways", []):
                    if gw.get("name") == GATEWAY_NAME:
                        gateway_id = gw["gatewayId"]
                        logger.info("Found existing gateway: %s", gateway_id)
                        break
        except ClientError:
            pass

        if not gateway_id:
            logger.info("Creating AgentCore Gateway: %s", GATEWAY_NAME)
            resp = self.control_client.create_gateway(
                name=GATEWAY_NAME,
                protocolType="MCP",
                description="Gateway for PostgreSQL database tools",
            )
            gateway_id = resp["gatewayId"]

            for _ in range(60):
                gw = self.control_client.get_gateway(gatewayIdentifier=gateway_id)
                if gw.get("status") == "ACTIVE":
                    break
                time.sleep(5)

        logger.info("Adding Lambda target to gateway")
        target_name = "DatabaseToolsTarget"
        try:
            self.control_client.create_gateway_target(
                gatewayIdentifier=gateway_id,
                name=target_name,
                description="PostgreSQL query tools for data analysis",
                targetConfiguration={
                    "mcp": {
                        "lambda": {
                            "lambdaArn": db_tools_lambda_arn,
                            "toolSchema": {"inlinePayload": tool_schema},
                        }
                    }
                },
                credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
            )
        except ClientError as e:
            if "ConflictException" in str(type(e)):
                logger.info("Gateway target already exists, skipping")
            else:
                raise

        self._add_lambda_gateway_permission(db_tools_lambda_arn, gateway_id)

        gateway_url = f"https://gateway.bedrock-agentcore.{self.region}.amazonaws.com/gateways/{gateway_id}"
        logger.info("Gateway URL: %s", gateway_url)
        return {"gateway_id": gateway_id, "gateway_url": gateway_url}

    def _add_lambda_gateway_permission(self, lambda_arn: str, gateway_id: str) -> None:
        """Grant AgentCore Gateway permission to invoke the Lambda."""
        function_name = lambda_arn.split(":")[-1]
        statement_id = "AllowAgentCoreGateway"
        gateway_arn = f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:gateway/{gateway_id}"

        try:
            self.lambda_client.remove_permission(FunctionName=function_name, StatementId=statement_id)
        except ClientError:
            pass

        self.lambda_client.add_permission(
            FunctionName=function_name,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal="bedrock-agentcore.amazonaws.com",
            SourceArn=gateway_arn,
        )

    # ------------------------------------------------------------------
    # Orchestrator
    # ------------------------------------------------------------------

    def deploy(self, env_vars: dict) -> dict:
        """Run the full deployment pipeline.

        Args:
            env_vars: Environment variables from CDK outputs (AURORA_RESOURCE_ARN, etc.)

        Returns:
            Dictionary with all deployed resource identifiers
        """
        logger.info("=" * 60)
        logger.info("DATA ANALYST CONVERSATIONAL ASSISTANT — AgentCore Deployment")
        logger.info("=" * 60)
        logger.info("Region: %s | Account: %s", self.region, self.account_id)

        # Step 1: IAM Role
        role_arn = self.create_execution_role()

        # Step 2: Memory
        memory_arn = self.create_memory()
        memory_id = memory_arn.split("/")[-1]

        # Step 3: Runtime
        runtime_arn = self.deploy_runtime(role_arn, memory_id, env_vars.copy())

        # Step 4: Gateway (requires DB tools Lambda ARN)
        gateway_info = {}
        db_tools_lambda_arn = env_vars.get("DB_TOOLS_LAMBDA_ARN")
        if db_tools_lambda_arn:
            gateway_info = self.create_gateway(db_tools_lambda_arn)
        else:
            logger.warning(
                "DB_TOOLS_LAMBDA_ARN not provided — skipping Gateway setup. "
                "Deploy the CDK stack first to create the Lambda, then re-run."
            )

        # Save outputs
        outputs = {
            "region": self.region,
            "role_arn": role_arn,
            "memory_arn": memory_arn,
            "memory_id": memory_id,
            "runtime_arn": runtime_arn,
            "gateway": gateway_info,
        }

        output_file = Path("deploy_outputs.json")
        output_file.write_text(json.dumps(outputs, indent=2))

        logger.info("=" * 60)
        logger.info("Deployment complete!")
        logger.info("  Runtime ARN : %s", runtime_arn)
        logger.info("  Memory ID   : %s", memory_id)
        if gateway_info:
            logger.info("  Gateway URL : %s", gateway_info.get("gateway_url"))
        logger.info("  Outputs     : %s", output_file)
        logger.info("=" * 60)

        return outputs


def main():
    parser = argparse.ArgumentParser(description="Deploy Data Analyst Conversational Assistant to AgentCore")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--aurora-arn", help="Aurora cluster ARN (from CDK output)")
    parser.add_argument("--secret-arn", help="Read-only DB secret ARN (from CDK output)")
    parser.add_argument("--dynamodb-table", help="DynamoDB table name (from CDK output)")
    parser.add_argument(
        "--db-tools-lambda-arn",
        help="Lambda ARN for database tools (from CDK output)",
    )
    parser.add_argument(
        "--container-uri",
        help="Pre-built container URI (skip CodeBuild if provided)",
    )
    parser.add_argument(
        "--model-id",
        default="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        help="Bedrock model ID",
    )

    args = parser.parse_args()

    env_vars = {
        "AURORA_RESOURCE_ARN": args.aurora_arn or "",
        "READONLY_SECRET_ARN": args.secret_arn or "",
        "QUESTION_ANSWERS_TABLE": args.dynamodb_table or "",
        "DB_TOOLS_LAMBDA_ARN": args.db_tools_lambda_arn or "",
        "BEDROCK_MODEL_ID": args.model_id,
    }
    if args.container_uri:
        env_vars["CONTAINER_URI"] = args.container_uri

    deployer = DataAnalystDeployer(region=args.region)
    deployer.deploy(env_vars=env_vars)


if __name__ == "__main__":
    main()
