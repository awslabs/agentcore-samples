"""
Lab 03: AgentCore Runtime Deployment Helper

Deploys Strands remediation agent with AgentCore Code Interpreter to Amazon Bedrock AgentCore Runtime.

Features:
- IAM role creation for Runtime execution
- Agent code packaging (Strands + Code Interpreter)
- Runtime deployment via bedrock-agentcore-starter-toolkit
- Configuration storage in Parameter Store
- Deployment lifecycle management (create, update, delete)
- Integration with Lab-02 Gateway (optional)

Based on AWS patterns:
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-toolkit.html
- https://github.com/awslabs/amazon-bedrock-agentcore-samples
"""

import json
import boto3
import logging
import time
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime
from botocore.exceptions import ClientError

# Import centralized configuration
from lab_helpers.config import AWS_REGION
from lab_helpers.constants import PARAMETER_PATHS

logger = logging.getLogger(__name__)

# Configuration defaults
REGION = AWS_REGION  # Use centralized region from config.py
PREFIX = "aiml301"
RUNTIME_NAME = f"{PREFIX}-remediation-runtime"
RUNTIME_ROLE_NAME = f"{PREFIX}-agentcore-remediation-role"
RUNTIME_POLICY_NAME = f"{PREFIX}-remediation-runtime-policy"


class AgentCoreRuntimeDeployer:
    """Deployment helper for Strands remediation agent to AgentCore Runtime"""

    def __init__(
        self,
        region: str = REGION,
        prefix: str = PREFIX,
        runtime_name: str = RUNTIME_NAME,
        verbose: bool = True,
    ):
        """
        Initialize deployer with AWS clients and configuration.

        Args:
            region: AWS region (default: us-west-2)
            prefix: Resource naming prefix (default: aiml301)
            runtime_name: Name for deployed Runtime (default: aiml301-remediation-runtime)
            verbose: Enable verbose logging
        """
        self.region = region
        self.prefix = prefix
        self.runtime_name = runtime_name
        self.verbose = verbose

        # AWS clients
        self.iam = boto3.client("iam", region_name=region)
        self.agentcore = boto3.client("bedrock-agentcore-control", region_name=region)
        self.ssm = boto3.client("ssm", region_name=region)
        self.sts = boto3.client("sts", region_name=region)
        self.logs = boto3.client("logs", region_name=region)

        # Get account ID
        self.account_id = self.sts.get_caller_identity()["Account"]

        # Initialize logger
        if verbose:
            logging.basicConfig(level=logging.INFO)
            logger.setLevel(logging.INFO)

    def _log(self, message: str, level: str = "info"):
        """Log message with formatting"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        levels = {"info": "ℹ️", "success": "✅", "error": "❌", "warning": "⚠️"}
        icon = levels.get(level, "•")
        print(f"{icon} [{timestamp}] {message}")
        getattr(logger, level, logger.info)(message)

    def check_prerequisites(self) -> bool:
        """Check that all prerequisites for deployment are met"""
        self._log("Checking prerequisites...")

        try:
            # Check toolkit installation
            try:
                from bedrock_agentcore_starter_toolkit import Runtime  # noqa: F401

                self._log("bedrock-agentcore-starter-toolkit is installed", "success")
            except ImportError:
                self._log(
                    "bedrock-agentcore-starter-toolkit not found. "
                    "Install with: pip install bedrock-agentcore-starter-toolkit",
                    "error",
                )
                return False

            # Check AWS credentials and permissions
            identity = self.sts.get_caller_identity()
            self._log(f"AWS account: {self.account_id}", "success")
            self._log(f"AWS IAM user/role: {identity.get('Arn')}", "success")

            # Check IAM permissions (attempt to list roles)
            try:
                self.iam.list_roles(MaxItems=1)
                self._log("IAM permissions verified", "success")
            except ClientError as e:
                self._log(f"IAM permissions insufficient: {e}", "error")
                return False

            # Check AgentCore access
            try:
                self.agentcore.list_agent_runtimes()
                self._log("AgentCore access verified", "success")
            except ClientError as e:
                self._log(f"AgentCore access denied: {e}", "error")
                return False

            self._log("All prerequisites met", "success")
            return True

        except Exception as e:
            self._log(f"Prerequisite check failed: {e}", "error")
            return False

    def create_runtime_iam_role(self) -> Dict:
        """
        Create IAM role for AgentCore Runtime execution.

        The role allows:
        - Runtime service to assume it
        - CloudWatch logging
        - ECR image access
        - Bedrock model invocation (for Code Interpreter)
        - Parameter Store access

        Returns:
            Dict with role ARN and metadata
        """
        self._log("Creating IAM role for Runtime...")

        # Trust policy: Allow bedrock-agentcore service to assume role
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                    "Condition": {
                        "StringEquals": {"aws:SourceAccount": self.account_id},
                        "ArnLike": {
                            "aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:runtime/*"
                        },
                    },
                }
            ],
        }

        # Permissions policy for Runtime
        permissions_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "CloudWatchLogs",
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    "Resource": f"arn:aws:logs:{self.region}:{self.account_id}:log-group:/aws/bedrock-agentcore/runtime/*",
                },
                {
                    "Sid": "ECRAccess",
                    "Effect": "Allow",
                    "Action": [
                        "ecr:GetAuthorizationToken",
                        "ecr:BatchGetImage",
                        "ecr:GetDownloadUrlForLayer",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "BedrockModels",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                    ],
                    "Resource": f"arn:aws:bedrock:{self.region}::foundation-model/*",
                },
                {
                    "Sid": "CodeInterpreter",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:StartCodeInterpreterSession",
                        "bedrock-agentcore:InvokeCodeInterpreter",
                        "bedrock-agentcore:StopCodeInterpreterSession",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "ParameterStore",
                    "Effect": "Allow",
                    "Action": [
                        "ssm:GetParameter",
                        "ssm:GetParameters",
                        "ssm:GetParametersByPath",
                    ],
                    "Resource": f"arn:aws:ssm:{self.region}:{self.account_id}:parameter/{self.prefix}/*",
                },
            ],
        }

        try:
            # Check if role exists
            try:
                role = self.iam.get_role(RoleName=RUNTIME_ROLE_NAME)
                self._log(f"IAM role already exists: {RUNTIME_ROLE_NAME}", "warning")
                role_arn = role["Role"]["Arn"]

                # Update trust policy to ensure it's correct for current region
                self.iam.update_assume_role_policy(RoleName=RUNTIME_ROLE_NAME, PolicyDocument=json.dumps(trust_policy))
                self._log(f"Updated trust policy for region {self.region}", "success")

            except self.iam.exceptions.NoSuchEntityException:
                # Create new role
                role = self.iam.create_role(
                    RoleName=RUNTIME_ROLE_NAME,
                    AssumeRolePolicyDocument=json.dumps(trust_policy),
                    Description="Execution role for AgentCore Runtime - Lab 03 Remediation Agent",
                    MaxSessionDuration=3600,
                )
                role_arn = role["Role"]["Arn"]
                self._log(f"Created IAM role: {RUNTIME_ROLE_NAME}", "success")

                # Wait for role to propagate in IAM
                time.sleep(10)

            # Attach permissions policy
            self.iam.put_role_policy(
                RoleName=RUNTIME_ROLE_NAME,
                PolicyName=RUNTIME_POLICY_NAME,
                PolicyDocument=json.dumps(permissions_policy),
            )
            self._log(f"Attached permissions policy: {RUNTIME_POLICY_NAME}", "success")

            # Store role ARN in Parameter Store
            param_name = PARAMETER_PATHS["lab_03"]["runtime_role_arn"]
            self.ssm.put_parameter(
                Name=param_name,
                Value=role_arn,
                Type="String",
                Overwrite=True,
                Description="IAM role ARN for Lab-03 AgentCore Runtime",
            )
            self._log("Stored role ARN in Parameter Store", "success")

            return {
                "role_arn": role_arn,
                "role_name": RUNTIME_ROLE_NAME,
                "policy_name": RUNTIME_POLICY_NAME,
                "account_id": self.account_id,
            }

        except Exception as e:
            self._log(f"Failed to create IAM role: {e}", "error")
            raise

    def package_agent_code(
        self,
        agent_script_path: Path,
        requirements_path: Optional[Path] = None,
        include_files: Optional[List[Path]] = None,
    ) -> Dict:
        """
        Package Strands remediation agent code for deployment.

        Args:
            agent_script_path: Path to agent Python script
            requirements_path: Path to requirements.txt (optional)
            include_files: Additional files to include (optional)

        Returns:
            Dict with package metadata and file paths
        """
        self._log(f"Packaging agent code from {agent_script_path}...")

        # Verify agent script exists
        if not Path(agent_script_path).exists():
            self._log(f"Agent script not found: {agent_script_path}", "error")
            raise FileNotFoundError(f"Agent script not found: {agent_script_path}")

        # Read agent code
        with open(agent_script_path, "r") as f:
            agent_code = f.read()

        package_info = {
            "agent_script": str(agent_script_path),
            "code_size_bytes": len(agent_code.encode()),
            "code_size_mb": round(len(agent_code.encode()) / (1024 * 1024), 2),
            "timestamp": datetime.utcnow().isoformat(),
            "files": {"agent_script": str(agent_script_path)},
        }

        # Add requirements if provided
        if requirements_path and Path(requirements_path).exists():
            with open(requirements_path, "r") as f:
                requirements = f.read()
            package_info["files"]["requirements"] = str(requirements_path)
            package_info["requirements_lines"] = len(requirements.splitlines())

        # Add other files if provided
        if include_files:
            for file_path in include_files:
                if Path(file_path).exists():
                    package_info["files"][Path(file_path).name] = str(file_path)

        self._log(f"Agent code packaged: {package_info['code_size_mb']} MB", "success")

        return package_info

    def deploy_runtime(
        self,
        agent_code: str,
        agent_name: str = "remediation-agent",
        role_arn: Optional[str] = None,
        description: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> Dict:
        """
        Deploy Strands agent to AgentCore Runtime.

        Args:
            agent_code: Agent Python code as string
            agent_name: Name for the agent/runtime
            role_arn: IAM role ARN (fetches from Parameter Store if not provided)
            description: Runtime description
            timeout_seconds: Execution timeout

        Returns:
            Dict with deployment info (runtime ID, ARN, endpoint, etc.)
        """
        self._log(f"Deploying runtime: {agent_name}...")

        # Get role ARN if not provided
        if not role_arn:
            try:
                response = self.ssm.get_parameter(Name=PARAMETER_PATHS["lab_03"]["runtime_role_arn"])
                role_arn = response["Parameter"]["Value"]
                self._log("Retrieved role ARN from Parameter Store", "info")
            except ClientError:
                self._log("Role ARN not found in Parameter Store. Creating role...", "warning")
                role_info = self.create_runtime_iam_role()
                role_arn = role_info["role_arn"]

        try:
            # Create runtime using bedrock-agentcore-starter-toolkit
            from bedrock_agentcore_starter_toolkit import Runtime

            runtime = Runtime(
                name=self.runtime_name,
                entrypoint=agent_code,
                role_arn=role_arn,
                region_name=self.region,
                timeout_seconds=timeout_seconds,
                description=description or "Strands remediation agent with Code Interpreter - Lab 03",
            )

            # Deploy to AgentCore
            runtime_config = runtime.deploy()

            self._log("Runtime deployed successfully", "success")

            deployment_info = {
                "runtime_name": self.runtime_name,
                "runtime_id": runtime_config.get("agent_runtime_id"),
                "runtime_arn": runtime_config.get("agent_runtime_arn"),
                "role_arn": role_arn,
                "region": self.region,
                "deployment_time": datetime.utcnow().isoformat(),
                "status": "DEPLOYED",
                "entrypoint": "agent_invocation",
                "tools": [
                    "validate_remediation_environment",
                    "generate_remediation_plan",
                    "execute_remediation_step",
                ],
            }

            # Store deployment info in Parameter Store
            self.ssm.put_parameter(
                Name=f"/{self.prefix}/lab-03/runtime-config",
                Value=json.dumps(deployment_info, indent=2),
                Type="String",
                Overwrite=True,
                Description="Lab-03 AgentCore Runtime deployment configuration",
            )

            return deployment_info

        except Exception as e:
            self._log(f"Runtime deployment failed: {e}", "error")
            raise

    def get_runtime_status(self, runtime_id: Optional[str] = None) -> Dict:
        """
        Get status of deployed runtime.

        Args:
            runtime_id: Runtime ID (fetches from Parameter Store if not provided)

        Returns:
            Dict with runtime status
        """
        try:
            # Get runtime ID if not provided
            if not runtime_id:
                response = self.ssm.get_parameter(Name=f"/{self.prefix}/lab-03/runtime-config")
                config = json.loads(response["Parameter"]["Value"])
                runtime_id = config.get("runtime_id")

            if not runtime_id:
                self._log("Runtime ID not found", "error")
                return {"status": "NOT_FOUND"}

            # Get runtime details
            response = self.agentcore.get_agent_runtime(agentRuntimeIdentifier=runtime_id)

            status_info = {
                "runtime_id": response["agentRuntime"]["agentRuntimeId"],
                "runtime_arn": response["agentRuntime"]["agentRuntimeArn"],
                "status": response["agentRuntime"]["status"],
                "created_at": response["agentRuntime"].get("createdAt"),
                "last_modified": response["agentRuntime"].get("lastModifiedAt"),
            }

            self._log(f"Runtime status: {status_info['status']}", "info")
            return status_info

        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                self._log(f"Runtime not found: {runtime_id}", "warning")
                return {"status": "NOT_FOUND"}
            raise

    def save_deployment_config(self, config: Dict, output_path: Optional[Path] = None) -> Path:
        """
        Save deployment configuration to file.

        Args:
            config: Deployment configuration dict
            output_path: Output file path (optional)

        Returns:
            Path to saved configuration file
        """
        if not output_path:
            output_path = Path(__file__).parent.parent.parent / "lab_03_deployment_config.json"

        with open(output_path, "w") as f:
            json.dump(config, f, indent=2)

        self._log(f"Configuration saved to {output_path}", "success")
        return output_path

    def cleanup(self, force: bool = False) -> bool:
        """
        Clean up Lab-03 resources.

        Args:
            force: Force deletion without confirmation

        Returns:
            True if cleanup successful
        """
        self._log("Starting cleanup...")

        if not force:
            confirm = input(
                f"Delete Lab-03 runtime '{self.runtime_name}' and related resources? This cannot be undone. (yes/no): "
            )
            if confirm.lower() != "yes":
                self._log("Cleanup cancelled", "warning")
                return False

        try:
            # Get runtime ID from Parameter Store
            try:
                response = self.ssm.get_parameter(Name=f"/{self.prefix}/lab-03/runtime-config")
                config = json.loads(response["Parameter"]["Value"])
                runtime_id = config.get("runtime_id")

                if runtime_id:
                    # Delete runtime
                    self.agentcore.delete_agent_runtime(agentRuntimeIdentifier=runtime_id)
                    self._log(f"Deleted runtime: {runtime_id}", "success")
            except ClientError as e:
                if e.response["Error"]["Code"] != "ParameterNotFound":
                    self._log(f"Error deleting runtime: {e}", "warning")

            # Delete IAM role and policies
            try:
                self.iam.delete_role_policy(RoleName=RUNTIME_ROLE_NAME, PolicyName=RUNTIME_POLICY_NAME)
                self._log(f"Deleted role policy: {RUNTIME_POLICY_NAME}", "success")
            except ClientError as e:
                if e.response["Error"]["Code"] != "NoSuchEntity":
                    self._log(f"Error deleting policy: {e}", "warning")

            try:
                self.iam.delete_role(RoleName=RUNTIME_ROLE_NAME)
                self._log(f"Deleted IAM role: {RUNTIME_ROLE_NAME}", "success")
            except ClientError as e:
                if e.response["Error"]["Code"] != "NoSuchEntity":
                    self._log(f"Error deleting role: {e}", "warning")

            # Delete Parameter Store entries
            try:
                self.ssm.delete_parameter(Name=PARAMETER_PATHS["lab_03"]["runtime_role_arn"])
                self._log("Deleted Parameter Store entry: runtime-role-arn", "success")
            except ClientError:
                pass

            try:
                self.ssm.delete_parameter(Name=PARAMETER_PATHS["lab_03"]["runtime_config"])
                self._log("Deleted Parameter Store entry: runtime-config", "success")
            except ClientError:
                pass

            # Delete CloudWatch log groups
            try:
                log_groups = self.logs.describe_log_groups(
                    logGroupNamePrefix=f"/aws/bedrock-agentcore/runtime/{self.runtime_name}"
                )
                for log_group in log_groups.get("logGroups", []):
                    self.logs.delete_log_group(logGroupName=log_group["logGroupName"])
                    self._log(f"Deleted log group: {log_group['logGroupName']}", "success")
            except ClientError:
                pass

            self._log("Cleanup completed successfully", "success")
            return True

        except Exception as e:
            self._log(f"Cleanup failed: {e}", "error")
            raise


def store_runtime_configuration(
    runtime_arn: str,
    runtime_id: str = None,
    region: str = "us-west-2",
    prefix: str = "aiml301_sre_agentcore",
) -> None:
    """Store runtime configuration in Parameter Store for persistence across sessions"""
    from lab_helpers.parameter_store import put_parameter

    print("\n" + "=" * 70)
    print("🔍 DEBUG: store_runtime_configuration() called")
    print("=" * 70)
    print(f"  Runtime ARN: {runtime_arn}")
    print(f"  Runtime ID: {runtime_id}")
    print(f"  Region: {region}")
    print(f"  Prefix: {prefix}")
    print()

    # Store runtime ARN using centralized constants
    runtime_arn_path = PARAMETER_PATHS["lab_03"]["runtime_arn"]
    print("📝 Storing runtime ARN to Parameter Store:")
    print(f"  Path: {runtime_arn_path}")
    print(f"  Value: {runtime_arn}")
    try:
        result = put_parameter(
            key=runtime_arn_path,
            value=runtime_arn,
            description="AgentCore Runtime ARN for Lab-03",
            region_name=region,
            overwrite=True,
        )
        print(f"✅ Successfully stored runtime ARN (version: {result})")
    except Exception as e:
        print(f"❌ Failed to store runtime ARN: {e}")
        import traceback

        traceback.print_exc()
        raise

    # Store runtime ID if provided
    if runtime_id:
        runtime_id_path = PARAMETER_PATHS["lab_03"]["runtime_id"]
        print("\n📝 Storing runtime ID to Parameter Store:")
        print(f"  Path: {runtime_id_path}")
        print(f"  Value: {runtime_id}")
        try:
            result = put_parameter(
                key=runtime_id_path,
                value=runtime_id,
                description="AgentCore Runtime ID for Lab-03",
                region_name=region,
                overwrite=True,
            )
            print(f"✅ Successfully stored runtime ID (version: {result})")
        except Exception as e:
            print(f"❌ Failed to store runtime ID: {e}")
            import traceback

            traceback.print_exc()
            raise
    else:
        print("\n⏭️  Runtime ID not provided, skipping...")

    print("\n" + "=" * 70)
    print("✅ store_runtime_configuration() complete")
    print("=" * 70 + "\n")
