#!/usr/bin/env python3
"""
Amazon Bedrock AgentCore Cleanup Script

This script replaces the cleanup.sh bash script with a Python-based cleanup
that works with both traditional deployments and agentcore-starter-toolkit deployments.

Usage:
    python cleanup.py <websocket-folder>

Examples:
    python cleanup.py 01-bedrock-sonic-ws
    python cleanup.py 02-strands-ws
"""

import argparse
import json
import os
import sys
import subprocess
from pathlib import Path
from typing import Optional  # noqa: F401


class Colors:
    """ANSI color codes for terminal output"""

    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    RED = "\033[0;31m"
    NC = "\033[0m"  # No Color


class AgentCoreCleanup:
    """Handles cleanup of AgentCore resources"""

    def __init__(self, websocket_folder: str):
        self.websocket_folder = websocket_folder

        # Resolve paths relative to the project root directory
        self.base_dir = Path(__file__).parent.parent

        self.config_file = self.base_dir / websocket_folder / "setup_config.json"
        self.gateway_config_file = self.base_dir / websocket_folder / "gateway_config.json"
        self.config = None
        self.gateway_config = None

        # Validate folder exists
        if not (self.base_dir / websocket_folder).exists():
            self._error(f"Folder not found: {websocket_folder}")
            self._print_available_folders()
            sys.exit(1)

    def _print(self, message: str, color: str = Colors.NC):
        """Print colored message"""
        print(f"{color}{message}{Colors.NC}")

    def _error(self, message: str):
        """Print error message"""
        self._print(f"❌ {message}", Colors.RED)

    def _success(self, message: str):
        """Print success message"""
        self._print(f"✅ {message}", Colors.GREEN)

    def _info(self, message: str):
        """Print info message"""
        self._print(f"ℹ️  {message}", Colors.YELLOW)

    def _print_available_folders(self):
        """Print available websocket folders"""
        print("\nAvailable folders:")
        for folder in [
            "01-bedrock-sonic-ws",
            "02-strands-ws",
            "03-langchain-transcribe-polly-ws",
            "04-pipecat-sonic-ws",
            "echo",
        ]:
            if (self.base_dir / folder).exists():
                print(f"  - {folder}")

    def _run_command(self, cmd: list, check: bool = False) -> subprocess.CompletedProcess:
        """Run a shell command and return the result"""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=check)
            return result
        except subprocess.CalledProcessError as e:
            if check:
                self._error(f"Command failed: {' '.join(cmd)}")
                self._error(f"Error: {e.stderr}")
            return e

    def load_configuration(self):
        """Load configuration from setup_config.json and gateway_config.json"""
        has_config = False

        if self.config_file.exists():
            self._info(f"Loading configuration from {self.config_file}")
            with open(self.config_file, "r") as f:
                self.config = json.load(f)
            self._success("Configuration loaded")
            has_config = True
        else:
            self._info(f"No configuration file found at {self.config_file}")

        if self.gateway_config_file.exists():
            self._info(f"Loading gateway configuration from {self.gateway_config_file}")
            with open(self.gateway_config_file, "r") as f:
                self.gateway_config = json.load(f)
            self._success("Gateway configuration loaded")
            has_config = True
        else:
            self._info(f"No gateway configuration found at {self.gateway_config_file}")

        if not has_config:
            self._info("Will attempt cleanup using environment variables or defaults")

        return has_config

    def print_cleanup_config(self):
        """Print cleanup configuration"""
        print("\n" + "=" * 70)
        print("🔧 Cleanup Configuration")
        print("=" * 70)

        if self.config:
            print(f"\nDeployment Method: {self.config.get('deployment_method', 'traditional')}")
            print(f"AWS Region:        {self.config.get('aws_region', 'N/A')}")
            print(f"Account ID:        {self.config.get('account_id', 'N/A')}")
            print(f"Agent ARN:         {self.config.get('agent_arn', 'N/A')}")
            print(f"IAM Role:          {self.config.get('iam_role_arn', 'N/A')}")

            if "ecr_repo_name" in self.config:
                print(f"ECR Repository:    {self.config.get('ecr_repo_name', 'N/A')}")

        if self.gateway_config:
            gateway = self.gateway_config.get("gateway", {})
            print(f"\nGateway Name:      {gateway.get('gateway_name', 'N/A')}")
            print(f"Gateway ARN:       {gateway.get('gateway_arn', 'N/A')}")
            print(f"Gateway URL:       {gateway.get('gateway_url', 'N/A')}")

        if not self.config and not self.gateway_config:
            print("\nNo configuration files found - using defaults")

        print("=" * 70 + "\n")

    def cleanup_with_agentcore_toolkit(self) -> bool:
        """Cleanup using agentcore destroy command"""
        self._print("\n🧹 Cleaning up with agentcore toolkit...", Colors.YELLOW)

        # Check if agentcore CLI is available
        if not self._check_agentcore_installed():
            return False

        # Change to websocket directory
        websocket_path = self.base_dir / self.websocket_folder / "websocket"
        if not websocket_path.exists():
            self._error(f"Websocket directory not found: {websocket_path}")
            return False

        original_dir = Path.cwd()
        os.chdir(websocket_path)

        try:
            # Check if .bedrock_agentcore.yaml exists
            if not Path(".bedrock_agentcore.yaml").exists():
                self._info("No .bedrock_agentcore.yaml found, skipping agentcore destroy")
                return False

            # Run agentcore destroy
            self._info("Running agentcore destroy...")
            result = self._run_command(["agentcore", "destroy"], check=False)

            if result.returncode == 0:
                self._success("AgentCore resources destroyed")
                return True
            else:
                self._error("agentcore destroy failed")
                print(result.stderr)
                return False

        finally:
            os.chdir(original_dir)

    def _check_agentcore_installed(self) -> bool:
        """Check if agentcore CLI is installed"""
        result = self._run_command(["which", "agentcore"], check=False)
        return result.returncode == 0

    def delete_agent_runtime(self):
        """Delete the agent runtime"""
        if not self.config or "agent_arn" not in self.config:
            self._info("No agent ARN found, skipping agent deletion")
            return

        agent_arn = self.config["agent_arn"]
        agent_id = agent_arn.split("/")[-1]
        region = self.config.get("aws_region", "us-east-1")

        self._print(f"\n🤖 Deleting agent runtime: {agent_id}", Colors.YELLOW)

        # Check if agent exists
        check_cmd = [
            "aws",
            "bedrock-agentcore-control",
            "get-agent-runtime",
            "--agent-runtime-id",
            agent_id,
            "--region",
            region,
        ]

        result = self._run_command(check_cmd, check=False)

        if result.returncode != 0:
            self._info("Agent runtime not found or already deleted")
            return

        # Delete agent
        delete_cmd = [
            "aws",
            "bedrock-agentcore-control",
            "delete-agent-runtime",
            "--agent-runtime-id",
            agent_id,
            "--region",
            region,
        ]

        result = self._run_command(delete_cmd, check=False)

        if result.returncode == 0:
            self._success("Agent runtime deleted")

            # Wait for deletion to propagate
            self._info("Waiting for deletion to propagate...")
            import time

            time.sleep(2)

            # Verify deletion
            verify_result = self._run_command(check_cmd, check=False)
            if verify_result.returncode != 0:
                self._success("Verified: Agent runtime no longer exists")
            else:
                self._info("WARNING: Agent runtime still exists after deletion")
        else:
            self._error("Agent runtime deletion failed")
            print(result.stderr)

    def delete_iam_role(self):
        """Delete IAM role and policies"""
        if not self.config:
            # Try default role name
            role_name = f"WebSocket{self.websocket_folder.capitalize()}AgentRole"
        else:
            role_arn = self.config.get("iam_role_arn", "")
            if not role_arn:
                self._info("No IAM role ARN found, skipping IAM cleanup")
                return
            role_name = role_arn.split("/")[-1]

        self._print(f"\n🔐 Deleting IAM role: {role_name}", Colors.YELLOW)

        # Delete role policy
        delete_policy_cmd = [
            "aws",
            "iam",
            "delete-role-policy",
            "--role-name",
            role_name,
            "--policy-name",
            f"{role_name}Policy",
        ]

        result = self._run_command(delete_policy_cmd, check=False)
        if result.returncode == 0:
            self._success("Role policy deleted")
        else:
            self._info("Policy deletion failed or already deleted")

        # Delete role
        delete_role_cmd = ["aws", "iam", "delete-role", "--role-name", role_name]

        result = self._run_command(delete_role_cmd, check=False)
        if result.returncode == 0:
            self._success("IAM role deleted")
        else:
            self._info("Role deletion failed or already deleted")

    def delete_ecr_repository(self):
        """Delete ECR repository and images"""
        if not self.config or "ecr_repo_name" not in self.config:
            self._info("\nNo ECR repository found in config, skipping ECR cleanup")
            return

        repo_name = self.config["ecr_repo_name"]
        region = self.config.get("aws_region", "us-east-1")

        self._print(f"\n🐳 Deleting ECR repository: {repo_name}", Colors.YELLOW)

        # Delete repository (force deletes all images)
        delete_cmd = [
            "aws",
            "ecr",
            "delete-repository",
            "--repository-name",
            repo_name,
            "--region",
            region,
            "--force",
        ]

        result = self._run_command(delete_cmd, check=False)

        if result.returncode == 0:
            self._success("ECR repository deleted")
        else:
            self._info("ECR repository deletion failed or already deleted")

    def delete_gateway(self):
        """Delete MCP Gateway and related resources using starter toolkit"""
        if not self.gateway_config:
            self._info("\nNo gateway configuration found, skipping gateway cleanup")
            return

        gateway = self.gateway_config.get("gateway", {})
        gateway_id = gateway.get("gateway_id")
        gateway_arn = gateway.get("gateway_arn")  # noqa: F841
        region = self.gateway_config.get("aws", {}).get("region", "us-east-1")

        if not gateway_id:
            self._info("\nNo gateway ID found, skipping gateway cleanup")
            return

        self._print(f"\n🌐 Deleting MCP Gateway: {gateway_id}", Colors.YELLOW)

        try:
            from bedrock_agentcore_starter_toolkit.operations.gateway.client import (
                GatewayClient,
            )

            # Initialize Gateway client
            client = GatewayClient(region_name=region)  # noqa: F841

            # Delete gateway (this should handle targets automatically)
            try:
                self._info("Deleting gateway and all targets...")
                # The toolkit's delete method should handle cleanup
                import boto3

                bedrock_client = boto3.client("bedrock-agentcore-control", region_name=region)

                # First, delete all targets
                try:
                    targets_response = bedrock_client.list_gateway_targets(gatewayIdentifier=gateway_id)
                    targets = targets_response.get("items", [])

                    for target in targets:
                        target_id = target.get("targetId")
                        self._info(f"Deleting target: {target_id}")
                        try:
                            bedrock_client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
                            self._success(f"Target {target_id} deleted")
                        except Exception as e:
                            self._info(f"Failed to delete target {target_id}: {e}")

                    # Wait for target deletions to propagate
                    if targets:
                        self._info("Waiting for target deletions to propagate...")
                        import time

                        time.sleep(5)

                        # Verify targets are deleted
                        verify_response = bedrock_client.list_gateway_targets(gatewayIdentifier=gateway_id)
                        remaining_targets = verify_response.get("items", [])
                        if remaining_targets:
                            self._info(f"Warning: {len(remaining_targets)} targets still exist, waiting longer...")
                            time.sleep(5)
                        else:
                            self._success("All targets deleted successfully")

                except Exception as e:
                    self._info(f"Failed to list/delete targets: {e}")

                # Delete the gateway
                bedrock_client.delete_gateway(gatewayIdentifier=gateway_id)
                self._success("MCP Gateway deleted")

                # Wait for deletion
                self._info("Waiting for gateway deletion to complete...")
                import time

                time.sleep(3)

            except Exception as e:
                if "ResourceNotFoundException" in str(e) or "not found" in str(e).lower():
                    self._info("Gateway already deleted")
                else:
                    self._error(f"Gateway deletion failed: {e}")

            # Clean up Lambda function if it was created
            self._cleanup_lambda_resources(region)

            # Clean up Gateway execution role
            self._cleanup_gateway_iam_role()

        except ImportError:
            self._error("bedrock_agentcore_starter_toolkit not installed, falling back to boto3")
            self._delete_gateway_with_boto3(gateway_id, region)
        except Exception as e:
            self._error(f"Gateway cleanup failed: {e}")
            import traceback

            traceback.print_exc()

    def _cleanup_lambda_resources(self, region: str):
        """Clean up Lambda function and its IAM role"""
        try:
            import boto3

            lambda_client = boto3.client("lambda", region_name=region)

            account_id = self.gateway_config.get("aws", {}).get("account_id", "")  # noqa: F841
            lambda_function_name = "AgentCoreLambdaTestFunction"

            self._info("Deleting test Lambda function...")
            try:
                lambda_client.delete_function(FunctionName=lambda_function_name)
                self._success("Lambda function deleted")
            except Exception as e:
                if "ResourceNotFoundException" in str(e):
                    self._info("Lambda function already deleted or not found")
                else:
                    self._info(f"Lambda cleanup skipped: {e}")

            # Clean up IAM role for Lambda
            iam_client = boto3.client("iam")
            lambda_role_name = "AgentCoreTestLambdaRole"

            try:
                # Detach policies first
                try:
                    policies_response = iam_client.list_attached_role_policies(RoleName=lambda_role_name)
                    for policy in policies_response.get("AttachedPolicies", []):
                        iam_client.detach_role_policy(RoleName=lambda_role_name, PolicyArn=policy["PolicyArn"])
                except Exception:
                    pass

                # Delete inline policies
                try:
                    inline_policies = iam_client.list_role_policies(RoleName=lambda_role_name)
                    for policy_name in inline_policies.get("PolicyNames", []):
                        iam_client.delete_role_policy(RoleName=lambda_role_name, PolicyName=policy_name)
                except Exception:
                    pass

                # Delete role
                self._info("Deleting Lambda IAM role...")
                iam_client.delete_role(RoleName=lambda_role_name)
                self._success("Lambda IAM role deleted")
            except Exception as e:
                if "NoSuchEntity" in str(e):
                    self._info("Lambda IAM role already deleted or not found")
                else:
                    self._info(f"Lambda IAM role cleanup skipped: {e}")

        except Exception as e:
            self._info(f"Lambda resources cleanup skipped: {e}")

    def _cleanup_gateway_iam_role(self):
        """Clean up Gateway execution IAM role"""
        try:
            import boto3

            iam_client = boto3.client("iam")
            gateway_role_name = "AgentCoreGatewayExecutionRole"

            try:
                # Detach policies
                try:
                    policies_response = iam_client.list_attached_role_policies(RoleName=gateway_role_name)
                    for policy in policies_response.get("AttachedPolicies", []):
                        iam_client.detach_role_policy(RoleName=gateway_role_name, PolicyArn=policy["PolicyArn"])
                except Exception:
                    pass

                # Delete inline policies
                try:
                    inline_policies = iam_client.list_role_policies(RoleName=gateway_role_name)
                    for policy_name in inline_policies.get("PolicyNames", []):
                        iam_client.delete_role_policy(RoleName=gateway_role_name, PolicyName=policy_name)
                except Exception:
                    pass

                # Delete role
                self._info("Deleting Gateway IAM role...")
                iam_client.delete_role(RoleName=gateway_role_name)
                self._success("Gateway IAM role deleted")
            except Exception as e:
                if "NoSuchEntity" in str(e):
                    self._info("Gateway IAM role already deleted or not found")
                else:
                    self._info(f"Gateway IAM role cleanup skipped: {e}")

        except Exception as e:
            self._info(f"Gateway IAM role cleanup skipped: {e}")

    def _delete_gateway_with_boto3(self, gateway_id: str, region: str):
        """Fallback method to delete gateway using boto3 directly"""
        try:
            import boto3

            bedrock_client = boto3.client("bedrock-agentcore-control", region_name=region)

            # Delete targets
            targets_response = bedrock_client.list_gateway_targets(gatewayIdentifier=gateway_id)
            for target in targets_response.get("items", []):
                bedrock_client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target["targetId"])

            # Delete gateway
            bedrock_client.delete_gateway(gatewayIdentifier=gateway_id)
            self._success("Gateway deleted (boto3 fallback)")

        except Exception as e:
            self._error(f"Boto3 fallback cleanup failed: {e}")

    def delete_config_file(self):
        """Delete configuration files"""
        if self.config_file.exists():
            self._print(f"\n🗑️  Deleting configuration file: {self.config_file}", Colors.YELLOW)
            self.config_file.unlink()
            self._success("Configuration file deleted")

        if self.gateway_config_file.exists():
            self._print(
                f"\n🗑️  Deleting gateway configuration file: {self.gateway_config_file}",
                Colors.YELLOW,
            )
            self.gateway_config_file.unlink()
            self._success("Gateway configuration file deleted")

    def delete_memory(self):
        """Delete AgentCore Memory resource if it was created during deployment."""
        if not self.config or "memory" not in self.config:
            self._info("\nNo memory configuration found, skipping memory cleanup")
            return

        memory_id = self.config["memory"].get("memory_id")
        memory_name = self.config["memory"].get("memory_name", "unknown")
        region = self.config.get("aws_region", "us-east-1")

        if not memory_id:
            self._info("No memory ID found, skipping memory cleanup")
            return

        self._print(
            f"\n🧠 Deleting AgentCore Memory: {memory_name} ({memory_id})",
            Colors.YELLOW,
        )

        try:
            import boto3

            control_client = boto3.client("bedrock-agentcore-control", region_name=region)
            control_client.delete_memory(memoryId=memory_id)
            self._success(f"Memory {memory_id} deleted")
        except Exception as e:
            if "not found" in str(e).lower() or "ResourceNotFoundException" in str(e):
                self._info("Memory already deleted or not found")
            else:
                self._warning(f"Failed to delete memory: {e}")

    def print_summary(self):
        """Print cleanup summary"""
        print("\n" + "=" * 70)
        self._success("Cleanup complete!")
        print("=" * 70)

        print("\n💡 Resources cleaned up:")
        if self.config:
            if "agent_arn" in self.config:
                print(f"   - Agent Runtime: {self.config['agent_arn']}")
            if "iam_role_arn" in self.config:
                print(f"   - IAM Role: {self.config['iam_role_arn']}")
            if "ecr_repo_name" in self.config:
                print(f"   - ECR Repository: {self.config['ecr_repo_name']}")
        if self.gateway_config:
            gateway = self.gateway_config.get("gateway", {})
            if "gateway_arn" in gateway:
                print(f"   - MCP Gateway: {gateway['gateway_arn']}")
        if self.config and "memory" in self.config:
            print(f"   - AgentCore Memory: {self.config['memory'].get('memory_id', 'N/A')}")
        print("   - Configuration files")
        print()

    def cleanup(self):
        """Main cleanup workflow"""
        try:
            self._print(f"\n🧹 Cleaning up {self.websocket_folder} resources...", Colors.YELLOW)
            self._print(f"📁 Using folder: {self.websocket_folder}\n", Colors.YELLOW)

            # Load configuration
            has_config = self.load_configuration()

            # Print configuration
            self.print_cleanup_config()

            # Determine cleanup method
            if has_config and self.config and self.config.get("deployment_method") == "agentcore-starter-toolkit":
                # Try agentcore destroy first
                if self.cleanup_with_agentcore_toolkit():
                    self._info("AgentCore toolkit cleanup successful")
                    # Still need to clean up IAM role manually
                    self.delete_iam_role()
                else:
                    self._info("Falling back to manual cleanup")
                    self.delete_agent_runtime()
                    self.delete_iam_role()
            else:
                # Traditional cleanup
                self.delete_agent_runtime()
                self.delete_iam_role()
                self.delete_ecr_repository()

            # Delete gateway if exists
            self.delete_gateway()

            # Delete memory if exists
            self.delete_memory()

            # Delete config files
            self.delete_config_file()

            # Print summary
            self.print_summary()

        except KeyboardInterrupt:
            self._error("\nCleanup cancelled by user")
            sys.exit(1)
        except Exception as e:
            self._error(f"Cleanup failed: {e}")
            import traceback

            traceback.print_exc()
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Clean up Amazon Bedrock AgentCore resources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cleanup.py 01-bedrock-sonic-ws
  python cleanup.py 02-strands-ws
        """,
    )

    parser.add_argument(
        "websocket_folder",
        choices=[
            "01-bedrock-sonic-ws",
            "02-strands-ws",
            "03-langchain-transcribe-polly-ws",
            "04-pipecat-sonic-ws",
            "echo",
        ],
        help="Websocket folder to clean up",
    )

    args = parser.parse_args()

    # Create cleanup handler and run
    cleanup = AgentCoreCleanup(args.websocket_folder)
    cleanup.cleanup()


if __name__ == "__main__":
    main()
