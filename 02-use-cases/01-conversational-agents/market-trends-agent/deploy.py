#!/usr/bin/env python3
"""
Complete Market Trends Agent Deployment Script
Handles Memory, IAM role creation, code packaging, and AgentCore Runtime setup.

The agent is deployed as a direct code deploy (CodeZip): dependencies are installed
for ARM64 Linux with uv, zipped with the agent code, uploaded to an S3 bucket in your
account, and run on the AgentCore managed Python runtime. No Docker, ECR, CodeBuild
or AgentCore CLI bootstrap is needed.

IAM Role Setup
--------------
The execution role trusts bedrock-agentcore.amazonaws.com with a condition
scoped to runtimes and A/B tests in your account:

    "Condition": {
        "StringEquals": {"aws:SourceAccount": "<account-id>"},
        "ArnLike":      {"aws:SourceArn": ["arn:aws:bedrock-agentcore:*:<account-id>:runtime/*",
                                           "arn:aws:bedrock-agentcore:*:<account-id>:ab-test/*"]}
    }

The permissions policy uses explicit least-privilege statements:

  BedrockModelInvocation     — bedrock:InvokeModel* scoped to foundation models
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
import hashlib
import io
import json
import logging
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Packages installed into the code package (ARM64 Linux wheels only)
RUNTIME_PACKAGES = (
    "langgraph",
    "langchain-aws",
    "langchain-core",
    "boto3",
    "playwright",
    "bedrock-agentcore==1.8.0",
    "aws-opentelemetry-distro==0.19.0",
    "opentelemetry-instrumentation-langchain",
    # wrapt 1.16.0+ breaks LangchainInstrumentor().instrument(); see pyproject.toml
    "wrapt<1.16.0",
)
RUNTIME_PYTHON_VERSION = "3.13"
RUNTIME_VERSION = "PYTHON_3_13"
SOURCE_FILES = ("market_trends_agent.py",)
SOURCE_DIRS = ("tools",)
# Playwright's Node driver needs libatomic, which the managed Python runtime does not ship.
# Pinned Debian arm64 build from the immutable snapshot archive; bundled under lib/ in the zip.
LIBATOMIC_DEB_URL = (
    "https://snapshot.debian.org/archive/debian/20240101T000000Z/pool/main/g/gcc-12/libatomic1_12.2.0-14_arm64.deb"
)
LIBATOMIC_DEB_SHA256 = "25a32fd0fe8b083fea31d2819d50e7254a0b9e529477c0740bbb44cbe297ec70"
LIBATOMIC_MEMBER = "./usr/lib/aarch64-linux-gnu/libatomic.so.1.2.0"


class MarketTrendsAgentDeployer:
    """Complete deployer for Market Trends Agent"""

    def __init__(self, region: str = "us-east-1"):
        self.region = region
        self.iam_client = boto3.client("iam", region_name=region)
        self.ssm_client = boto3.client("ssm", region_name=region)
        self.s3_client = boto3.client("s3", region_name=region)

    def create_execution_role(self, role_name: str) -> str:
        """Create IAM execution role with least-privilege permissions.

        Trust policy: bedrock-agentcore.amazonaws.com, conditioned on
        aws:SourceAccount and aws:SourceArn scoped to runtime/* and ab-test/* resources.

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
                            "aws:SourceArn": [
                                f"arn:aws:bedrock-agentcore:*:{account_id}:runtime/*",
                                f"arn:aws:bedrock-agentcore:*:{account_id}:ab-test/*",
                            ],
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

            # Update the existing role's trust and permissions
            logger.info("📋 Updating existing role with comprehensive permissions...")
            self.iam_client.update_assume_role_policy(
                RoleName=role_name,
                PolicyDocument=json.dumps(trust_policy),
            )
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

    def _build_code_artifact(self, work_dir: Path) -> Path:
        """Install ARM64 Linux dependencies and zip them with the agent code.

        Returns the path of the zip. File modes are kept, so the Playwright
        driver and console scripts stay executable on the runtime.
        """
        package_dir = work_dir / "package"
        package_dir.mkdir()
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("uv is required to package the agent: https://docs.astral.sh/uv/")
        logger.info("📦 Installing ARM64 Linux dependencies with uv (first run takes a minute)...")
        subprocess.run(
            [
                uv,
                "pip",
                "install",
                "--target",
                str(package_dir),
                "--python-platform",
                "aarch64-manylinux2014",
                "--python-version",
                RUNTIME_PYTHON_VERSION,
                "--only-binary=:all:",
                "--no-compile",
                "--quiet",
                *RUNTIME_PACKAGES,
            ],
            check=True,
        )

        root = Path(__file__).resolve().parent
        for name in SOURCE_FILES:
            shutil.copy2(root / name, package_dir / name)
        for name in SOURCE_DIRS:
            shutil.copytree(root / name, package_dir / name, ignore=shutil.ignore_patterns("__pycache__"))

        # uv writes the local interpreter path into console scripts; point it at the runtime's python
        instrument = package_dir / "bin/opentelemetry-instrument"
        lines = instrument.read_text(encoding="utf-8").splitlines()
        instrument.write_text("\n".join(["#!/usr/bin/env python3", *lines[1:]]) + "\n", encoding="utf-8")

        self._bundle_libatomic(package_dir / "lib")

        artifact = work_dir / "market_trends_agent.zip"
        with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as archive:
            for source in sorted(package_dir.rglob("*")):
                if not source.is_file() or "__pycache__" in source.parts:
                    continue
                info = zipfile.ZipInfo(source.relative_to(package_dir).as_posix(), (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                mode = 0o755 if source.stat().st_mode & stat.S_IXUSR else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
        logger.info("✅ Built code package: %.0f MiB", artifact.stat().st_size / 2**20)
        return artifact

    def _bundle_libatomic(self, lib_dir: Path) -> None:
        """Extract libatomic.so.1 from the pinned Debian package into lib_dir.

        tools/browser_tool.py adds this directory to LD_LIBRARY_PATH before
        starting Playwright.
        """
        logger.info("📦 Bundling libatomic for the Playwright driver...")
        with urllib.request.urlopen(LIBATOMIC_DEB_URL, timeout=120) as response:
            deb = response.read()
        if hashlib.sha256(deb).hexdigest() != LIBATOMIC_DEB_SHA256:
            raise RuntimeError(f"Checksum mismatch for {LIBATOMIC_DEB_URL}")

        # A .deb is an ar archive; the files live in its data.tar.* member
        offset = 8
        while offset < len(deb):
            header = deb[offset : offset + 60]
            name = header[:16].decode().strip()
            size = int(header[48:58])
            body = deb[offset + 60 : offset + 60 + size]
            offset += 60 + size + size % 2
            if name.startswith("data.tar"):
                with tarfile.open(fileobj=io.BytesIO(body)) as data:
                    library = data.extractfile(LIBATOMIC_MEMBER)
                    lib_dir.mkdir(exist_ok=True)
                    (lib_dir / "libatomic.so.1").write_bytes(library.read())
                return
        raise RuntimeError(f"No data archive found in {LIBATOMIC_DEB_URL}")

    def _ensure_code_bucket(self, account_id: str) -> str:
        """Create the private S3 bucket that holds the code package, if it doesn't exist."""
        bucket = f"agentcore-market-trends-{account_id}-{self.region}"
        try:
            self.s3_client.head_bucket(Bucket=bucket)
            return bucket
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
                raise
        logger.info("🪣 Creating code bucket: %s", bucket)
        request = {"Bucket": bucket}
        if self.region != "us-east-1":
            request["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
        self.s3_client.create_bucket(**request)
        self.s3_client.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        return bucket

    def _upload_code(self, agent_name: str) -> tuple[str, str]:
        """Build the code package and upload it. Returns (bucket, key)."""
        account_id = boto3.client("sts").get_caller_identity()["Account"]
        bucket = self._ensure_code_bucket(account_id)
        key = f"{agent_name}/market_trends_agent.zip"
        with tempfile.TemporaryDirectory(prefix="market-trends-agent-") as temp:
            artifact = self._build_code_artifact(Path(temp))
            logger.info("⬆️  Uploading code package to s3://%s/%s", bucket, key)
            self.s3_client.upload_file(str(artifact), bucket, key)
        return bucket, key

    def _wait_until_ready(self, control, runtime_id: str) -> None:
        for _ in range(60):
            runtime = control.get_agent_runtime(agentRuntimeId=runtime_id)
            status = runtime.get("status")
            if status == "READY":
                logger.info("✅ Runtime is READY")
                return
            if status in ("CREATE_FAILED", "UPDATE_FAILED", "FAILED"):
                raise RuntimeError(f"Runtime {status}: {runtime.get('failureReason', 'no reason given')}")
            logger.info("⏳ Runtime status: %s", status)
            time.sleep(10)
        raise RuntimeError("Runtime did not become READY within 10 minutes")

    def _ensure_runtime(
        self,
        agent_name: str,
        execution_role_arn: str,
        bucket: str,
        key: str,
    ) -> str:
        """Create or update the AgentCore runtime via bedrock-agentcore-control."""
        control = boto3.client("bedrock-agentcore-control", region_name=self.region)
        artifact = {
            "codeConfiguration": {
                "code": {"s3": {"bucket": bucket, "prefix": key}},
                "runtime": RUNTIME_VERSION,
                "entryPoint": ["opentelemetry-instrument", "market_trends_agent.py"],
            }
        }
        common = {
            "agentRuntimeArtifact": artifact,
            "roleArn": execution_role_arn,
            "networkConfiguration": {"networkMode": "PUBLIC"},
            "protocolConfiguration": {"serverProtocol": "HTTP"},
        }

        # Check whether a runtime with this name already exists.
        paginator = control.get_paginator("list_agent_runtimes")
        for page in paginator.paginate():
            for rt in page.get("agentRuntimes", []):
                if rt.get("agentRuntimeName") == agent_name:
                    runtime_id = rt["agentRuntimeId"]
                    logger.info("🔄 Updating existing runtime: %s", runtime_id)
                    control.update_agent_runtime(agentRuntimeId=runtime_id, **common)
                    self._wait_until_ready(control, runtime_id)
                    return rt["agentRuntimeArn"]

        # No existing runtime — create one. Retry briefly while a new role propagates.
        logger.info("🚀 Creating new AgentCore runtime: %s", agent_name)
        for attempt in range(6):
            try:
                resp = control.create_agent_runtime(agentRuntimeName=agent_name, **common)
                break
            except ClientError as exc:
                if (
                    exc.response["Error"]["Code"] != "ValidationException"
                    or "role" not in str(exc).lower()
                    or attempt == 5
                ):
                    raise
                logger.info("⏳ Waiting for the execution role to propagate...")
                time.sleep(10)
        self._wait_until_ready(control, resp["agentRuntimeId"])
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
          3. Package the code and dependencies for ARM64 Linux and upload to S3.
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

            # Step 3: Package the code and upload it to S3
            bucket, key = self._upload_code(agent_name)

            # Step 4: Create / update the runtime via bedrock-agentcore-control
            runtime_arn = self._ensure_runtime(agent_name, execution_role_arn, bucket, key)

            arn_file = Path(__file__).resolve().parent / ".agent_arn"
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
            logger.info("  Service     : %s.DEFAULT", agent_name)
            logger.info("Next steps:")
            logger.info("  Test  : uv run python test_agent.py --region %s", self.region)
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

    if not shutil.which("uv"):
        logger.error("❌ uv not found. Install it: https://docs.astral.sh/uv/getting-started/installation/")
        return False
    logger.info("✅ uv found (used to package dependencies; no Docker required)")

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
    parser.add_argument(
        "--region", default=None, help="AWS region (default: your AWS profile's region, else us-east-1)"
    )
    parser.add_argument("--skip-checks", action="store_true", help="Skip prerequisite checks")

    args = parser.parse_args()

    # Check prerequisites
    if not args.skip_checks and not check_prerequisites():
        logger.error("❌ Prerequisites not met. Fix issues above or use --skip-checks")
        exit(1)

    # Create deployer and deploy
    region = args.region or boto3.session.Session().region_name or "us-east-1"
    deployer = MarketTrendsAgentDeployer(region=region)

    runtime_arn = deployer.deploy_agent(agent_name=args.agent_name, role_name=args.role_name)

    if runtime_arn:
        logger.info("\n🎯 Deployment completed successfully!")
        logger.info("Run 'uv run python test_agent.py --region %s' to test your deployed agent.", region)
    else:
        logger.error("❌ Deployment failed")
        exit(1)


if __name__ == "__main__":
    main()
