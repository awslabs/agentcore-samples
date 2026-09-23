#!/usr/bin/env python3
"""
Amazon Bedrock AgentCore Deployment Script using Starter Toolkit

This script is a Python-based deployment
using the bedrock-agentcore-starter-toolkit.

Usage:
    python deploy.py <websocket-folder> [options]

Examples:
    python deploy.py 01-bedrock-sonic-ws
    python deploy.py 02-strands-ws --region us-west-2
    python deploy.py 03-langchain-transcribe-polly-ws --agent-name my-langchain-agent
    python deploy.py 01-bedrock-sonic-ws --agent-name my-sonic-agent
"""

import argparse
import json
import os
import sys
import subprocess
import shutil
import time
import traceback
import yaml
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Optional

import boto3
from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient
from bedrock_agentcore_starter_toolkit.operations.runtime.launch import (
    launch_bedrock_agentcore,
)


class Colors:
    """ANSI color codes for terminal output"""

    GREEN = "\033[0;32m"
    BLUE = "\033[0;34m"
    YELLOW = "\033[1;33m"
    RED = "\033[0;31m"
    NC = "\033[0m"  # No Color


class AgentCoreDeployer:
    """Handles deployment of agents to Amazon Bedrock AgentCore Runtime"""

    def __init__(self, websocket_folder: str, args: argparse.Namespace):
        self.websocket_folder = websocket_folder
        self.args = args

        # Resolve paths relative to the project root directory
        self.base_dir = Path(__file__).parent.parent

        # Validate folder exists
        self.websocket_path = self.base_dir / websocket_folder / "websocket"
        if not self.websocket_path.exists():
            self._error(f"Websocket folder not found: {self.websocket_path}")
            sys.exit(1)

        # Set configuration
        self.aws_region = args.region or os.getenv("AWS_REGION", "us-east-1")
        self.account_id = args.account_id or os.getenv("ACCOUNT_ID")
        self.agent_name = args.agent_name or f"bidi_{websocket_folder.replace('-', '_')}_agent"

        if not self.account_id:
            self._error("ACCOUNT_ID is required. Set via --account-id or ACCOUNT_ID environment variable")
            sys.exit(1)

        self.config_file = self.base_dir / websocket_folder / "setup_config.json"

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
        self._print(f"ℹ️  {message}", Colors.BLUE)

    def _warning(self, message: str):
        """Print warning message"""
        self._print(f"⚠️  {message}", Colors.YELLOW)

    def _run_command(self, cmd: list, cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
        """Run a shell command and return the result"""
        try:
            result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)
            return result
        except subprocess.CalledProcessError as e:
            self._error(f"Command failed: {' '.join(cmd)}")
            self._error(f"Error: {e.stderr}")
            if check:
                raise
            return e

    def create_memory(self) -> Optional[Dict]:
        """Create an AgentCore Memory resource for the strands agent."""
        if self.websocket_folder != "02-strands-ws":
            return None

        self._print("\n🧠 Creating AgentCore Memory...", Colors.YELLOW)

        try:
            from bedrock_agentcore.memory import MemoryClient

            client = MemoryClient(region_name=self.aws_region)

            memory_name = f"{self.agent_name}_memory"

            # Check if memory already exists by listing and matching name
            try:
                existing = client.list_memories()
                for mem in existing.get("memories", []):
                    if mem.get("name") == memory_name:
                        memory_id = mem["id"]
                        self._info(f"Found existing memory: {memory_name} (ID: {memory_id})")
                        return {"memory_id": memory_id, "memory_name": memory_name}
            except Exception:
                pass  # list may not be supported or empty, proceed to create

            memory = client.create_memory(
                name=memory_name,
                description=f"Chat history for {self.agent_name}",
            )

            memory_id = memory.get("id")
            self._success(f"Memory created: {memory_name} (ID: {memory_id})")

            return {"memory_id": memory_id, "memory_name": memory_name}

        except ImportError:
            self._warning("bedrock-agentcore package not installed, skipping memory creation")
            self._info("Install with: pip install bedrock-agentcore")
            return None
        except Exception as e:
            self._warning(f"Failed to create memory: {e}")
            self._info("You can create memory manually and set MEMORY_ID env var")
            return None

    def deploy_mcp_gateway(self) -> Optional[Dict]:
        """Deploy MCP Gateways (for strands and langchain agents that use MCP tools)"""
        if self.websocket_folder not in (
            "02-strands-ws",
            "03-langchain-transcribe-polly-ws",
        ):
            return None

        self._print("\n🌐 Deploying MCP Gateways...", Colors.YELLOW)

        # Initialize Gateway client
        client = GatewayClient(region_name=self.aws_region)
        bedrock_client = boto3.client("bedrock-agentcore-control", region_name=self.aws_region)

        # Define the four gateways to create
        gateway_configs = [
            {
                "name": "auth-tools",
                "mcp_server": "auth-tools-mcp",
                "tools": ["authenticate_user", "verify_identity"],
            },
            {
                "name": "banking-tools",
                "mcp_server": "banking-tools-mcp",
                "tools": [
                    "get_account_balance",
                    "get_recent_transactions",
                    "transfer_funds",
                    "get_account_summary",
                ],
            },
            {
                "name": "mortgage-tools",
                "mcp_server": "mortgage-tools-mcp",
                "tools": [
                    "get_mortgage_rates",
                    "calculate_mortgage_payment",
                    "check_mortgage_eligibility",
                    "get_mortgage_application_status",
                ],
            },
            {
                "name": "faq-kb-tools",
                "mcp_server": "anybank-faq-kb",
                "tools": ["search_anybank_faq", "answer_anybank_question"],
            },
        ]

        deployed_gateways = []

        for gw_config in gateway_configs:
            gateway_name = gw_config["name"]
            self._print(f"\n📦 Deploying {gateway_name} gateway...", Colors.BLUE)

            gateway = None

            # Check if gateway already exists
            try:
                response = bedrock_client.list_gateways()
                for gw in response.get("items", []):
                    if gw.get("name") == gateway_name:
                        self._info(f"Found existing gateway: {gateway_name} (ID: {gw['gatewayId']})")
                        gateway_detail = bedrock_client.get_gateway(gatewayIdentifier=gw["gatewayId"])
                        gateway = gateway_detail
                        break
            except Exception as e:
                self._warning(f"Could not check for existing gateway: {e}")

            # Create MCP Gateway if it doesn't exist
            if not gateway:
                self._info(f"Creating {gateway_name} gateway...")
                try:
                    gateway = client.create_mcp_gateway(name=gateway_name)
                except Exception as e:
                    error_msg = str(e)
                    if "already exists" in error_msg.lower() or "conflict" in error_msg.lower():
                        self._warning(f"Gateway {gateway_name} already exists, fetching...")
                        response = bedrock_client.list_gateways()
                        for gw in response.get("items", []):
                            if gw.get("name") == gateway_name:
                                gateway_detail = bedrock_client.get_gateway(gatewayIdentifier=gw["gatewayId"])
                                gateway = gateway_detail
                                self._success(f"Retrieved existing gateway: {gw['gatewayId']}")
                                break
                        if not gateway:
                            raise Exception(f"Gateway '{gateway_name}' exists but could not be found")
                    else:
                        raise

            gateway_arn = gateway["gatewayArn"]
            gateway_url = gateway["gatewayUrl"]
            role_arn = gateway["roleArn"]
            gateway_id = gateway["gatewayId"]

            self._success(f"Gateway ready: {gateway_id}")
            self._info(f"   URL: {gateway_url}")

            # Create MCP Server Target
            self._info(f"Creating MCP Server Target for {gw_config['mcp_server']}...")

            try:
                target = client.create_mcp_gateway_target(
                    gateway=gateway,
                    name=gw_config["mcp_server"],
                    target_type="lambda",
                    target_payload=None,
                )
                self._success(f"MCP Server target created: {target['targetId']}")
            except Exception as e:
                if "already exists" in str(e).lower() or "conflict" in str(e).lower():
                    self._warning(f"MCP Server target already exists for {gateway_name}, continuing...")
                    targets_response = bedrock_client.list_gateway_targets(gatewayIdentifier=gateway_id)
                    target = targets_response.get("items", [{}])[0] if targets_response.get("items") else {}
                else:
                    raise

            # Store gateway info
            deployed_gateways.append(
                {
                    "gateway_name": gateway_name,
                    "gateway_id": gateway_id,
                    "gateway_arn": gateway_arn,
                    "gateway_url": gateway_url,
                    "role_arn": role_arn,
                    "target_id": target.get("targetId", "unknown"),
                    "mcp_server_name": gw_config["mcp_server"],
                    "tools": gw_config["tools"],
                }
            )

        # Save gateway configuration
        gateway_config = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "deployment_type": "mcp-gateway",
            "gateways": deployed_gateways,
            "aws": {"account_id": self.account_id, "region": self.aws_region},
        }

        config_path = self.base_dir / self.websocket_folder / "gateway_config.json"
        with open(config_path, "w") as f:
            json.dump(gateway_config, f, indent=2)

        self._success(f"Gateway configuration saved to {config_path}")
        self._info(f"   Deployed {len(deployed_gateways)} gateways")

        return {"gateways": deployed_gateways}

    def check_prerequisites(self):
        """Check if required tools are installed"""
        self._print("\n📋 Checking prerequisites...", Colors.YELLOW)

        required_tools = {
            "python3": "Python 3.10+",
            "aws": "AWS CLI",
            "agentcore": "bedrock-agentcore-starter-toolkit",
        }

        missing_tools = []

        for tool, description in required_tools.items():
            if not shutil.which(tool):
                missing_tools.append(f"{tool} ({description})")

        if missing_tools:
            self._error("Missing required tools:")
            for tool in missing_tools:
                print(f"  - {tool}")
            print("\nInstall missing tools:")
            print("  pip install bedrock-agentcore-starter-toolkit")
            sys.exit(1)

        self._success("All prerequisites met")

    def setup_agentcore_project(self):
        """Set up AgentCore project structure"""
        self._print("\n📦 Setting up AgentCore project...", Colors.YELLOW)

        # Create .bedrock_agentcore.yaml configuration
        config = {
            "agent_name": self.agent_name,
            "region": self.aws_region,
            "entry_point": "server.py",
            "runtime": "python3.12",
            "bedrock_agentcore": {
                "agent_runtime_name": self.agent_name,
                "network_mode": "PUBLIC",
            },
        }

        config_path = self.websocket_path / ".bedrock_agentcore.yaml"

        # Write configuration
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)

        self._success(f"Created AgentCore configuration: {config_path}")

    def create_iam_role(self) -> str:
        """Create IAM role for the agent"""
        self._print("\n🔐 Creating IAM role...", Colors.YELLOW)

        role_name = f"WebSocket{self.websocket_folder.capitalize()}AgentRole"

        # Read policy files early so they're available for both create and update paths
        deploy_dir = Path(__file__).parent
        agent_role_path = deploy_dir / "agent_role.json"
        trust_policy_path = deploy_dir / "trust_policy.json"

        if not agent_role_path.exists() or not trust_policy_path.exists():
            self._error("Policy files not found (agent_role.json, trust_policy.json)")
            sys.exit(1)

        # Check if role exists
        check_cmd = ["aws", "iam", "get-role", "--role-name", role_name]
        result = self._run_command(check_cmd, check=False)

        if result.returncode == 0:
            role_data = json.loads(result.stdout)
            role_arn = role_data["Role"]["Arn"]
            self._info(f"IAM role {role_name} already exists")

            # Always update the policy to ensure latest permissions are applied
            with open(agent_role_path, "r") as f:
                agent_role_policy = f.read().replace("${ACCOUNT_ID}", self.account_id)

            put_policy_cmd = [
                "aws",
                "iam",
                "put-role-policy",
                "--role-name",
                role_name,
                "--policy-name",
                f"{role_name}Policy",
                "--policy-document",
                agent_role_policy,
            ]
            self._run_command(put_policy_cmd)
            self._success(f"Updated policy on existing role: {role_arn}")
            return role_arn

        # Read and substitute ACCOUNT_ID in agent_role.json
        with open(agent_role_path, "r") as f:
            agent_role_policy = f.read().replace("${ACCOUNT_ID}", self.account_id)

        # Create role
        create_role_cmd = [
            "aws",
            "iam",
            "create-role",
            "--role-name",
            role_name,
            "--assume-role-policy-document",
            f"file://{trust_policy_path}",
            "--output",
            "json",
        ]

        result = self._run_command(create_role_cmd)
        self._success("Role created")

        # Attach policy
        put_policy_cmd = [
            "aws",
            "iam",
            "put-role-policy",
            "--role-name",
            role_name,
            "--policy-name",
            f"{role_name}Policy",
            "--policy-document",
            agent_role_policy,
        ]

        self._run_command(put_policy_cmd)
        self._success("Policy attached")

        # Get role ARN
        result = self._run_command(["aws", "iam", "get-role", "--role-name", role_name, "--output", "json"])
        role_data = json.loads(result.stdout)
        role_arn = role_data["Role"]["Arn"]

        self._success(f"IAM role created: {role_arn}")

        # Wait for IAM propagation
        self._info("Waiting 10 seconds for IAM role to propagate...")
        time.sleep(10)

        return role_arn

    def deploy_agent(
        self,
        role_arn: str,
        gateway_info: Optional[Dict] = None,
        memory_info: Optional[Dict] = None,
    ) -> Dict:
        """Deploy agent using starter toolkit"""
        self._print("\n🚀 Deploying agent to AgentCore Runtime...", Colors.YELLOW)

        # Change to websocket directory
        original_dir = Path.cwd()
        os.chdir(self.websocket_path)

        try:
            # Remove any existing configuration file first
            config_path = Path(".bedrock_agentcore.yaml")
            if config_path.exists():
                self._info("Removing existing configuration...")
                config_path.unlink()

            # Create .bedrock_agentcore.yaml configuration file directly
            self._info("Creating AgentCore configuration...")

            # The toolkit expects an 'agents' section with agent definitions
            # Use ecr_auto_create to let the SDK create the repository and get the full URI
            config = {
                "agents": {
                    self.agent_name: {
                        "name": self.agent_name,
                        "entrypoint": "server.py",
                        "runtime": "python3.12",
                        "aws": {
                            "account": self.account_id,
                            "region": self.aws_region,
                            "execution_role": role_arn,
                            "ecr_auto_create": True,
                        },
                    }
                },
                "default_agent": self.agent_name,
                "region": self.aws_region,
            }

            # Prepare environment variables
            env_vars = {}

            # Add MCP Gateway environment variables if available (for strands and langchain)
            if self.websocket_folder in ("02-strands-ws", "03-langchain-transcribe-polly-ws") and gateway_info:
                gateways = gateway_info.get("gateways", [])

                if gateways:
                    # Pass all gateway ARNs and URLs as JSON-encoded environment variables
                    gateway_arns = [gw["gateway_arn"] for gw in gateways]
                    gateway_urls = [gw["gateway_url"] for gw in gateways]

                    env_vars["MCP_GATEWAY_ARNS"] = json.dumps(gateway_arns)
                    env_vars["MCP_GATEWAY_URLS"] = json.dumps(gateway_urls)

                    self._info(f"Added MCP Gateway environment variables for {len(gateways)} gateways")
                    for gw in gateways:
                        self._info(f"   {gw['gateway_name']}: {gw['gateway_url']}")

            # Add AgentCore Memory environment variable if available (for strands)
            if memory_info and memory_info.get("memory_id"):
                env_vars["MEMORY_ID"] = memory_info["memory_id"]
                env_vars["MEMORY_REGION"] = self.aws_region
                self._info(f"Added MEMORY_ID={memory_info['memory_id']} to environment")

            # Add Pipecat-specific environment variables from .env file (if any)
            if self.websocket_folder == "04-pipecat-sonic-ws":
                env_file = self.websocket_path / ".env"
                if env_file.exists():
                    self._info("Loading Pipecat environment variables from .env file...")
                    with open(env_file) as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                key, _, value = line.partition("=")
                                key, value = key.strip(), value.strip()
                                if value:
                                    env_vars[key] = value
                                    self._info(f"   {key}: ***configured***")

            with open(config_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False)

            self._success(f"Configuration created: {config_path}")

            # Determine deployment mode
            local = self.args.local
            use_codebuild = not self.args.local_build

            if local:
                self._info("Deploying with local mode (requires Docker)")
            elif not use_codebuild:
                self._info("Deploying with local build mode (requires Docker)")
            else:
                self._info("Deploying with CodeBuild (no Docker required)")

            self._info("Launching agent (this may take a few minutes)...")

            # Use starter toolkit to launch
            result = launch_bedrock_agentcore(
                config_path=config_path,
                agent_name=self.agent_name,
                local=local,
                use_codebuild=use_codebuild,
                env_vars=env_vars,
                auto_update_on_conflict=True,
            )

            # Extract agent information from result
            agent_arn = result.agent_arn
            agent_id = result.agent_id

            if not agent_arn:
                raise RuntimeError("Failed to get agent ARN from deployment result")

            self._success("Agent deployed successfully!")
            self._info(f"   Agent ARN: {agent_arn}")
            self._info(f"   Agent ID: {agent_id}")

            return {
                "agent_arn": agent_arn,
                "agent_runtime_name": self.agent_name,
                "role_arn": role_arn,
            }

        finally:
            os.chdir(original_dir)

    def save_configuration(self, deployment_info: Dict):
        """Save deployment configuration to JSON file"""
        self._print("\n💾 Saving configuration...", Colors.YELLOW)

        config = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "websocket_folder": self.websocket_folder,
            "aws_region": self.aws_region,
            "account_id": self.account_id,
            "agent_name": self.agent_name,
            "agent_runtime_name": deployment_info["agent_runtime_name"],
            "agent_arn": deployment_info["agent_arn"],
            "iam_role_arn": deployment_info["role_arn"],
            "deployment_method": "agentcore-starter-toolkit",
        }

        # Include memory info if available
        if "memory" in deployment_info:
            config["memory"] = deployment_info["memory"]

        with open(self.config_file, "w") as f:
            json.dump(config, f, indent=2)

        self._success(f"Configuration saved to {self.config_file}")

    def print_summary(self, deployment_info: Dict):
        """Print deployment summary"""
        self._print("\n" + "=" * 80, Colors.GREEN)
        self._print("✅ Deployment Complete!", Colors.GREEN)
        self._print("=" * 80, Colors.GREEN)

        self._print("\n📊 Configuration Summary", Colors.BLUE)
        self._print("=" * 80, Colors.GREEN)

        print(f"\n{Colors.YELLOW}AWS Configuration:{Colors.NC}")
        print(f"   Account ID:        {self.account_id}")
        print(f"   Region:            {self.aws_region}")

        print(f"\n{Colors.YELLOW}Agent Runtime:{Colors.NC}")
        print(f"   Agent Name:        {deployment_info['agent_runtime_name']}")
        print(f"   Agent ARN:         {deployment_info['agent_arn']}")
        print(f"   IAM Role:          {deployment_info['role_arn']}")

        # Show gateway info if available (strands deployment)
        if "gateways" in deployment_info:
            gateways = deployment_info["gateways"]
            print(f"\n{Colors.YELLOW}MCP Gateways ({len(gateways)} deployed):{Colors.NC}")
            for gw in gateways:
                print(f"\n   {gw['gateway_name']}:")
                print(f"      Gateway ID:     {gw['gateway_id']}")
                print(f"      Gateway URL:    {gw['gateway_url']}")
                print(f"      Target ID:      {gw['target_id']}")
                print(f"      Tools:          {', '.join(gw['tools'])}")

        # Show memory info if available (strands deployment)
        if "memory" in deployment_info:
            mem = deployment_info["memory"]
            print(f"\n{Colors.YELLOW}AgentCore Memory:{Colors.NC}")
            print(f"   Memory ID:         {mem.get('memory_id', 'N/A')}")
            print(f"   Memory Name:       {mem.get('memory_name', 'N/A')}")

        self._print("\n" + "=" * 80, Colors.GREEN)
        self._print("\n🚀 Next Steps", Colors.BLUE)
        self._print("=" * 80, Colors.GREEN)

        print(f"\n{Colors.YELLOW}1. Start the client:{Colors.NC}")
        print(f"   ./utils/start_client.sh {self.websocket_folder}")

        print(f"\n{Colors.YELLOW}2. Or test with agentcore CLI:{Colors.NC}")
        print('   agentcore invoke "Hello!"')

        print(f"\n{Colors.YELLOW}3. View logs:{Colors.NC}")
        print("   Check CloudWatch Logs in AWS Console")

        print(f"\n{Colors.YELLOW}4. When done, clean up:{Colors.NC}")
        print(f"   python utils/cleanup.py {self.websocket_folder}")

        self._print("\n" + "=" * 80, Colors.GREEN)

    def deploy(self):
        """Main deployment workflow"""
        try:
            self._print(f"\n🚀 AgentCore Deployment - {self.websocket_folder}", Colors.BLUE)
            self._print(f"📁 Using websocket folder: {self.websocket_folder}\n", Colors.BLUE)

            # Step 1: Check prerequisites
            self.check_prerequisites()

            # Step 1.5: Deploy MCP Gateway (only for strands)
            gateway_info = self.deploy_mcp_gateway()

            # Step 1.6: Create AgentCore Memory (only for strands)
            memory_info = self.create_memory()

            # Step 2: Create IAM role
            role_arn = self.create_iam_role()

            # Step 3: Deploy agent
            deployment_info = self.deploy_agent(role_arn, gateway_info, memory_info)

            # Add gateway info to deployment info if available
            if gateway_info:
                deployment_info["gateway"] = gateway_info

            # Add memory info to deployment info if available
            if memory_info:
                deployment_info["memory"] = memory_info

            # Step 4: Save configuration
            self.save_configuration(deployment_info)

            # Step 5: Print summary
            self.print_summary(deployment_info)

        except KeyboardInterrupt:
            self._error("\nDeployment cancelled by user")
            sys.exit(1)
        except Exception as e:
            self._error(f"Deployment failed: {e}")
            traceback.print_exc()
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Deploy agents to Amazon Bedrock AgentCore Runtime using starter toolkit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python deploy.py 01-bedrock-sonic-ws
  python deploy.py 02-strands-ws --region us-west-2
  python deploy.py 03-langchain-transcribe-polly-ws --agent-name my-langchain-agent
  python deploy.py 01-bedrock-sonic-ws --agent-name my-sonic-agent --local-build

Environment Variables:
  ACCOUNT_ID    AWS Account ID (required if not provided via --account-id)
  AWS_REGION    AWS Region (default: us-east-1)
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
            "webrtc-kvs",
        ],
        help="Websocket folder to deploy",
    )

    parser.add_argument("--account-id", help="AWS Account ID (or set ACCOUNT_ID env var)")

    parser.add_argument("--region", help="AWS Region (default: us-east-1 or AWS_REGION env var)")

    parser.add_argument("--agent-name", help="Custom agent name (default: bidi_<folder>_agent)")

    parser.add_argument("--local", action="store_true", help="Build and run locally (requires Docker)")

    parser.add_argument(
        "--local-build",
        action="store_true",
        help="Build locally, deploy to cloud (requires Docker)",
    )

    args = parser.parse_args()

    # Create deployer and run
    deployer = AgentCoreDeployer(args.websocket_folder, args)
    deployer.deploy()


if __name__ == "__main__":
    main()
