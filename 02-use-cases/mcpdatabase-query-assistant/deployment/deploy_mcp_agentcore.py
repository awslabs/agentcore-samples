#!/usr/bin/env python3
"""
Enhanced MCP Server Deployment Script with Automatic Cognito Setup

Requirements:
- Python 3.10 or higher
- AWS CLI configured with appropriate permissions
- Required Python packages (see requirements.txt)

This script will:
1. Check Python version compatibility
2. Check for existing Cognito configuration
3. Create Cognito User Pool and Client if not exists
4. Create a test user and generate JWT tokens
5. Deploy the MCP server with proper authentication
6. Manage configuration files in ../config/ directory
7. Reuse existing agents instead of creating duplicates
8. Force rebuild option for code changes

Usage:
  python deploy_mcp_agentcore.py           # Normal deployment (reuse existing agent)
  python deploy_mcp_agentcore.py --rebuild # Rebuild Docker image and update existing agent
"""

import argparse
import os
import sys
import json
import yaml
import re
import shlex
import boto3
from pathlib import Path
from botocore.exceptions import ClientError


def check_python_version():
    """Check if Python version meets minimum requirements"""
    required_version = (3, 10)
    current_version = sys.version_info[:2]

    print("Python version check:")
    print(f"   Current: Python {sys.version.split()[0]}")
    print(f"   Required: Python {required_version[0]}.{required_version[1]}+")

    if current_version < required_version:
        print(
            f"Python version {current_version[0]}.{current_version[1]} is not supported"
        )
        print(
            f"   Please upgrade to Python {required_version[0]}.{required_version[1]} or higher"
        )
        print(f"   Current version: {sys.version}")
        print("\nInstallation suggestions:")
        print("   macOS (Homebrew): brew install python@3.11")
        print("   Ubuntu/Debian: sudo apt install python3.11")
        print("   Windows: Download from https://python.org")
        sys.exit(1)

    print("Python version is compatible")
    return True


try:
    from bedrock_agentcore_starter_toolkit import Runtime

    print("AgentCore Runtime toolkit loaded successfully")
except ImportError as e:
    print(f"Failed to import AgentCore toolkit: {e}")
    sys.exit(1)


class CognitoSetup:
    def __init__(self, region="us-east-1"):
        self.region = region
        self.cognito_client = boto3.client("cognito-idp", region_name=region)
        self.config_file = Path("../config/cognito.json")

    def check_existing_setup(self):
        """Check if Cognito configuration already exists"""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)

                # Verify the user pool still exists
                try:
                    self.cognito_client.describe_user_pool(
                        UserPoolId=config["user_pool_id"]
                    )
                    print("Existing Cognito configuration found and verified")
                    return config
                except ClientError:
                    print(
                        " Existing configuration found but user pool doesn't exist. Will recreate."
                    )
                    return None
            except Exception as e:
                print(f" Error reading existing config: {e}. Will recreate.")
                return None
        return None

    def create_user_pool(self, pool_name="DatabaseMCPServerPool"):
        """Create a new Cognito User Pool"""
        print(f"Creating Cognito User Pool: {pool_name}")

        try:
            response = self.cognito_client.create_user_pool(
                PoolName=pool_name,
                Policies={
                    "PasswordPolicy": {
                        "MinimumLength": 8,
                        "RequireUppercase": True,
                        "RequireLowercase": True,
                        "RequireNumbers": True,
                        "RequireSymbols": True,
                        "TemporaryPasswordValidityDays": 7,
                    }
                },
                AutoVerifiedAttributes=["email"],
                UsernameAttributes=["email"],
                VerificationMessageTemplate={"DefaultEmailOption": "CONFIRM_WITH_CODE"},
                MfaConfiguration="OFF",
                AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
            )

            user_pool_id = response["UserPool"]["Id"]
            print(f"User Pool created: {user_pool_id}")
            return user_pool_id

        except ClientError as e:
            print(f"Failed to create user pool: {e}")
            raise

    def create_user_pool_client(
        self, user_pool_id, client_name="DatabaseMCPServerClient"
    ):
        """Create a User Pool Client"""
        print(f"Creating User Pool Client: {client_name}")

        try:
            response = self.cognito_client.create_user_pool_client(
                UserPoolId=user_pool_id,
                ClientName=client_name,
                ExplicitAuthFlows=[
                    "ALLOW_ADMIN_USER_PASSWORD_AUTH",
                    "ALLOW_USER_PASSWORD_AUTH",
                    "ALLOW_REFRESH_TOKEN_AUTH",
                ],
                RefreshTokenValidity=30,
                AccessTokenValidity=24,
                IdTokenValidity=24,
                TokenValidityUnits={
                    "AccessToken": "hours",
                    "IdToken": "hours",
                    "RefreshToken": "days",
                },
            )

            client_id = response["UserPoolClient"]["ClientId"]
            print(f"User Pool Client created: {client_id}")
            return client_id

        except ClientError as e:
            print(f"Failed to create user pool client: {e}")
            raise

    def create_test_user(
        self,
        user_pool_id,
        username="testuser@example.com",
        temp_password="TempPass123!",
    ):
        """Create a test user in the User Pool"""
        print(f"Creating test user: {username}")

        try:
            # Create user
            self.cognito_client.admin_create_user(
                UserPoolId=user_pool_id,
                Username=username,
                UserAttributes=[
                    {"Name": "email", "Value": username},
                    {"Name": "email_verified", "Value": "true"},
                ],
                TemporaryPassword=temp_password,
                MessageAction="SUPPRESS",
            )

            # Set permanent password
            self.cognito_client.admin_set_user_password(
                UserPoolId=user_pool_id,
                Username=username,
                Password=temp_password,
                Permanent=True,
            )

            print(f"Test user created: {username}")
            return username, temp_password

        except ClientError as e:
            if e.response["Error"]["Code"] == "UsernameExistsException":
                print(f"Test user already exists: {username}")
                return username, temp_password
            else:
                print(f"Failed to create test user: {e}")
                raise

    def generate_jwt_tokens(self, user_pool_id, client_id, username, password):
        """Generate JWT tokens for the test user"""
        print("Generating JWT tokens...")

        try:
            response = self.cognito_client.admin_initiate_auth(
                UserPoolId=user_pool_id,
                ClientId=client_id,
                AuthFlow="ADMIN_NO_SRP_AUTH",
                AuthParameters={"USERNAME": username, "PASSWORD": password},
            )

            if "AuthenticationResult" in response:
                tokens = response["AuthenticationResult"]
                print("JWT tokens generated successfully")
                return {
                    "access_token": tokens["AccessToken"],
                    "id_token": tokens["IdToken"],
                    "refresh_token": tokens.get("RefreshToken"),
                }
            else:
                print("Failed to generate tokens - no AuthenticationResult")
                return None

        except ClientError as e:
            print(f"Failed to generate JWT tokens: {e}")
            raise

    def save_configuration(self, user_pool_id, client_id, tokens):
        """Save Cognito configuration to file"""
        print("Saving Cognito configuration...")

        # Ensure config directory exists
        self.config_file.parent.mkdir(exist_ok=True)

        config = {
            "user_pool_id": user_pool_id,
            "client_id": client_id,
            "discovery_url": f"https://cognito-idp.{self.region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration",
            "bearer_token": tokens["access_token"],
            "id_token": tokens["id_token"],
        }

        if tokens.get("refresh_token"):
            config["refresh_token"] = tokens["refresh_token"]

        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        print(f"Configuration saved to {self.config_file}")
        return config

    def setup_cognito(self):
        """Main method to set up Cognito and always refresh JWT tokens"""
        print("Checking Cognito setup...")

        # Check if already configured
        existing_config = self.check_existing_setup()
        if existing_config:
            print("Existing Cognito configuration found - refreshing JWT tokens...")

            # Extract existing configuration
            user_pool_id = existing_config["user_pool_id"]
            client_id = existing_config["client_id"]

            # Generate fresh JWT tokens using existing user
            username = "testuser@example.com"
            password = (
                ""  # Match the password from 'create_test_user' for Cognito user pool
            )

            try:
                tokens = self.generate_jwt_tokens(
                    user_pool_id, client_id, username, password
                )
                if tokens:
                    # Update configuration with fresh tokens
                    config = self.save_configuration(user_pool_id, client_id, tokens)
                    print("JWT tokens refreshed successfully!")
                    return config
                else:
                    print("Failed to refresh tokens, using existing configuration")
                    return existing_config
            except Exception as e:
                print(f"Failed to refresh tokens: {e}, using existing configuration")
                return existing_config

        print("Setting up new Cognito configuration...")

        # Create User Pool
        user_pool_id = self.create_user_pool()

        # Create User Pool Client
        client_id = self.create_user_pool_client(user_pool_id)

        # Create test user
        username, password = self.create_test_user(user_pool_id)

        # Generate JWT tokens
        tokens = self.generate_jwt_tokens(user_pool_id, client_id, username, password)

        if not tokens:
            raise Exception("Failed to generate JWT tokens")

        # Save configuration
        config = self.save_configuration(user_pool_id, client_id, tokens)

        print("Cognito setup completed successfully!")
        return config


class EnhancedMCPDeployment:
    def __init__(self, server_name="mcp_db_server"):
        self.server_name = server_name
        self.cognito_setup = CognitoSetup()
        self.aws_account_id = self._get_aws_account_id()
        self.aws_region = self._get_aws_region()
    
    def _get_aws_account_id(self):
        """Get AWS account ID from STS and validate format"""
        try:
            sts_client = boto3.client('sts')
            account_id = sts_client.get_caller_identity()['Account']
            
            # Validate account ID is 12 digits only
            if not re.match(r'^\d{12}$', account_id):
                print(f"ERROR: Invalid AWS account ID format: {account_id}")
                sys.exit(1)
            
            print(f"Using AWS Account ID: {account_id}")
            return account_id
        except Exception as e:
            print(f"Error getting AWS account ID: {e}")
            sys.exit(1)
    
    def _get_aws_region(self):
        """Get AWS region and validate format"""
        region = "us-east-1"  # Default region
        
        # Validate region format (e.g., us-east-1, eu-west-2)
        if not re.match(r'^[a-z]{2}-[a-z]+-\d+$', region):
            print(f"ERROR: Invalid AWS region format: {region}")
            sys.exit(1)
        
        return region

    def setup_dockerfile_location(self):
        """Setup Dockerfile and .dockerignore in the expected location for AgentCore toolkit"""
        try:
            dockerfile_source = Path("Dockerfile")  # In deployment directory
            dockerfile_target = Path("../Dockerfile")  # In project root
            dockerignore_source = Path(".dockerignore")  # In deployment directory
            dockerignore_target = Path("../.dockerignore")  # In project root

            files_copied = 0

            if dockerfile_source.exists():
                print("Using custom Dockerfile from deployment directory")

                # Copy our custom Dockerfile to the root for toolkit compatibility
                import shutil

                shutil.copy2(dockerfile_source, dockerfile_target)
                print(
                    "Created temporary Dockerfile in project root for toolkit compatibility"
                )
                files_copied += 1

            if dockerignore_source.exists():
                print("Using custom .dockerignore from deployment directory")

                # Copy our custom .dockerignore to the root for toolkit compatibility
                import shutil

                shutil.copy2(dockerignore_source, dockerignore_target)
                print(
                    "Created temporary .dockerignore in project root for toolkit compatibility"
                )
                files_copied += 1

            if files_copied == 0:
                print("ℹ️  No custom Docker files found, toolkit will generate them")
                return False

            return True

        except Exception as e:
            print(f" Could not setup Docker files location: {e}")
            return False

    def cleanup_temporary_docker_files(self):
        """Clean up temporary Docker files created for toolkit compatibility"""
        try:
            dockerfile_target = Path("../Dockerfile")  # In project root
            dockerignore_target = Path("../.dockerignore")  # In project root

            files_cleaned = 0

            if dockerfile_target.exists():
                dockerfile_target.unlink()
                files_cleaned += 1
                print("Removed temporary Dockerfile from project root")

            if dockerignore_target.exists():
                dockerignore_target.unlink()
                files_cleaned += 1
                print("Removed temporary .dockerignore from project root")

            if files_cleaned > 0:
                print("Temporary Docker files cleaned up")
                print("Master files remain in deployment/ directory")

        except Exception as e:
            print(f" Could not cleanup temporary Docker files: {e}")

    def cleanup_generated_dockerfile(self):
        """Clean up toolkit-generated Docker files and restore our custom ones"""
        try:
            dockerfile_source = Path("Dockerfile")  # In deployment directory
            dockerfile_target = Path("../Dockerfile")  # In project root
            dockerignore_source = Path(".dockerignore")  # In deployment directory
            dockerignore_target = Path("../.dockerignore")  # In project root

            files_restored = 0

            if dockerfile_source.exists() and dockerfile_target.exists():
                print("Restoring custom Dockerfile from deployment directory")

                # Replace toolkit-generated Dockerfile with our custom one
                import shutil

                shutil.copy2(dockerfile_source, dockerfile_target)
                files_restored += 1

            if dockerignore_source.exists() and dockerignore_target.exists():
                print("Restoring custom .dockerignore from deployment directory")

                # Replace toolkit-generated .dockerignore with our custom one
                import shutil

                shutil.copy2(dockerignore_source, dockerignore_target)
                files_restored += 1

            if files_restored > 0:
                print("Custom Docker files restored")

        except Exception as e:
            print(f" Could not cleanup Docker files: {e}")

    def setup_agentcore_config_location(self):
        """Setup AgentCore configuration file in the expected location"""
        try:
            config_source = Path("../config/.bedrock_agentcore.yaml")
            config_target = Path("../.bedrock_agentcore.yaml")

            # Ensure config directory exists
            config_source.parent.mkdir(exist_ok=True)

            # If config exists in config directory but not in root, copy it
            if config_source.exists() and not config_target.exists():
                print(
                    "Copying AgentCore config from config/ to root for toolkit compatibility"
                )
                import shutil

                shutil.copy2(config_source, config_target)

            # If config exists in root but not in config directory, move it
            elif config_target.exists() and not config_source.exists():
                print("Moving existing AgentCore config to config/ directory")
                import shutil

                shutil.move(config_target, config_source)
                # Create symlink for toolkit compatibility
                try:
                    config_target.symlink_to(config_source.resolve())
                    print("Created symlink for AgentCore toolkit compatibility")
                except OSError:
                    # If symlink fails (Windows), copy instead
                    shutil.copy2(config_source, config_target)
                    print("Created copy for AgentCore toolkit compatibility")

            # If both exist, ensure they're synchronized (config/ is master)
            elif config_source.exists() and config_target.exists():
                # Check if target is a symlink pointing to source
                if not (
                    config_target.is_symlink()
                    and config_target.resolve() == config_source.resolve()
                ):
                    print("Synchronizing AgentCore config files")
                    config_target.unlink()  # Remove old file
                    try:
                        config_target.symlink_to(config_source.resolve())
                        print("Updated symlink for AgentCore toolkit compatibility")
                    except OSError:
                        import shutil

                        shutil.copy2(config_source, config_target)
                        print("Updated copy for AgentCore toolkit compatibility")

            return config_source

        except Exception as e:
            print(f" Could not setup AgentCore config location: {e}")
            # Fallback to root location
            return Path("../.bedrock_agentcore.yaml")

    def sync_agentcore_config_after_deployment(self):
        """Sync AgentCore config back to config directory after deployment"""
        try:
            config_source = Path("../config/.bedrock_agentcore.yaml")
            config_target = Path("../.bedrock_agentcore.yaml")

            # If toolkit updated the root file, sync back to config directory
            if config_target.exists() and not config_target.is_symlink():
                print("Syncing AgentCore config back to config/ directory")
                import shutil

                shutil.copy2(config_target, config_source)

                # Replace root file with symlink
                config_target.unlink()
                try:
                    config_target.symlink_to(config_source.resolve())
                    print("Replaced root config with symlink to config/ directory")
                except OSError:
                    # If symlink fails, keep the copy
                    shutil.copy2(config_source, config_target)
                    print("Kept copy in root for toolkit compatibility")

        except Exception as e:
            print(f" Could not sync AgentCore config: {e}")

    def delete_existing_aws_agent(self, agent_arn):
        """Delete existing AWS agent using AgentCore toolkit"""
        try:
            print(f" Deleting existing AWS agent: {agent_arn}")

            # Use the AgentCore toolkit to delete the agent
            from bedrock_agentcore_starter_toolkit import Runtime

            # Extract agent ID from ARN
            # ARN format: arn:aws:bedrock-agentcore:us-east-1:975910639313:runtime/mcp_db_server_v1754404518-qTN6WS7tJj
            agent_id = agent_arn.split("/")[-1]

            # Create a temporary runtime instance to delete the agent
            temp_runtime = Runtime()

            # Try to delete using the toolkit's internal methods
            # Note: This might not be directly exposed, so we'll use boto3 as fallback
            try:
                # First try with AgentCore toolkit if it has delete methods
                if hasattr(temp_runtime, "delete_agent"):
                    temp_runtime.delete_agent(agent_id)
                    print("Agent deleted using AgentCore toolkit")
                    return True
            except Exception as e:
                print(f" AgentCore toolkit delete failed: {e}")

            # Fallback to direct AWS API call
            try:
                # Try to delete using bedrock-agent-runtime
                # Note: AgentCore runtimes might not be deletable via standard Bedrock APIs
                print(" AgentCore runtimes cannot be deleted via standard AWS APIs")
                print("The agent will be updated in place instead")
                return False

            except Exception as e:
                print(f" AWS API delete failed: {e}")
                return False

        except Exception as e:
            print(f" Could not delete existing agent: {e}")
            return False

    def handle_existing_agent(self, force_rebuild=False):
        """Handle existing agent by ensuring we reuse the existing AWS agent

        Args:
            force_rebuild (bool): If True, will still reuse existing agent but force image rebuild
        """
        try:
            print("Checking for existing agent configuration...")

            config_file = Path("config/.bedrock_agentcore.yaml")
            if config_file.exists():
                with open(config_file, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)

                if config and "agents" in config:
                    # Look for any agent that matches our server name or has our server name as base
                    existing_agents = []
                    for agent_name, agent_config in config["agents"].items():
                        if (
                            agent_name == self.server_name
                            or agent_name.startswith(f"{self.server_name}_")
                            or agent_name.startswith(f"{self.server_name}_v")
                        ):
                            # Check if this agent has a valid AWS ARN
                            if (
                                "bedrock_agentcore" in agent_config
                                and "agent_arn" in agent_config["bedrock_agentcore"]
                            ):
                                existing_agents.append((agent_name, agent_config))

                    if existing_agents:
                        # Use the first valid existing agent
                        existing_name, existing_config = existing_agents[0]
                        existing_arn = existing_config["bedrock_agentcore"]["agent_arn"]

                        print(f"Found existing AWS agent: {existing_name}")
                        print(f"Agent ARN: {existing_arn}")

                        # If the agent name is not the standard name, rename it in config
                        if existing_name != self.server_name:
                            print(
                                f"Renaming agent config from '{existing_name}' to '{self.server_name}'"
                            )

                            # Copy the configuration to the standard name
                            config["agents"][self.server_name] = existing_config.copy()
                            config["agents"][self.server_name]["name"] = (
                                self.server_name
                            )

                            # Remove the old configuration
                            del config["agents"][existing_name]

                            # Update default agent
                            config["default_agent"] = self.server_name

                            # Write back the updated configuration
                            with open(config_file, "w", encoding="utf-8") as f:
                                yaml.dump(config, f, default_flow_style=False)

                            print("Agent configuration renamed for consistency")

                        # Clean up any other old agents
                        agents_to_remove = []
                        for agent_name in config["agents"].keys():
                            if agent_name != self.server_name and (
                                agent_name.startswith(f"{self.server_name}_")
                                or agent_name.startswith(f"{self.server_name}_v")
                            ):
                                agents_to_remove.append(agent_name)

                        if agents_to_remove:
                            print("Cleaning up old agent configurations...")
                            for old_agent in agents_to_remove:
                                del config["agents"][old_agent]
                                print(f"   Removed: {old_agent}")

                            # Write back the cleaned configuration
                            with open(config_file, "w", encoding="utf-8") as f:
                                yaml.dump(config, f, default_flow_style=False)

                        print(
                            "Will reuse existing AWS agent (agent ARN remains unchanged)"
                        )
                        return existing_arn

            print("ℹ️  No existing agent configuration found")
            return None

        except Exception as e:
            print(f" Could not handle existing agent: {e}")
            return None

    def deploy(self, force_rebuild=False):
        """Deploy MCP server with automatic Cognito setup

        Args:
            force_rebuild (bool): If True, rebuild Docker image and update existing agent
        """
        print(f"Starting enhanced MCP deployment for: {self.server_name}")
        print("=" * 60)

        # Store original working directory for restoration
        original_cwd = os.getcwd()

        try:
            # Step 1: Setup Cognito
            cognito_config = self.cognito_setup.setup_cognito()

            # Step 2: Configure and deploy MCP server
            print("\nConfiguring MCP server...")

            # Setup custom Docker files location
            self.setup_dockerfile_location()

            # Setup AgentCore config file location
            self.setup_agentcore_config_location()

            # Handle existing agent conflicts and get existing ARN if any
            existing_agent_arn = self.handle_existing_agent(force_rebuild)

            if existing_agent_arn:
                if force_rebuild:
                    print(
                        f"🔄 Rebuilding Docker image for existing agent: {existing_agent_arn}"
                    )

                    # Change to parent directory for AgentCore Runtime
                    parent_dir = Path(__file__).parent.parent
                    os.chdir(parent_dir)

                    # Extract agent name from existing config
                    config_file = Path("config/.bedrock_agentcore.yaml")
                    if config_file.exists():
                        with open(config_file, "r", encoding="utf-8") as f:
                            config = yaml.safe_load(f)

                        # Get the existing agent name
                        existing_agents = [
                            (name, agent_config)
                            for name, agent_config in config.get("agents", {}).items()
                            if "bedrock_agentcore" in agent_config
                            and "agent_arn" in agent_config["bedrock_agentcore"]
                        ]

                        if existing_agents:
                            # Extract the actual agent name from the ARN
                            agent_arn = existing_agents[0][1]["bedrock_agentcore"][
                                "agent_arn"
                            ]
                            # ARN format: arn:aws:bedrock-agentcore:us-east-1:xxxxx:runtime/mcp_db_server_xxxx-xxxx
                            existing_name = agent_arn.split("/")[-1].split("-")[
                                0
                            ]  # Extract mcp_db_server_1755524390
                            
                            # Validate existing_name to prevent command injection
                            if not re.match(r"^[a-zA-Z0-9_]+$", existing_name):
                                print(f"ERROR: Invalid agent name format: {existing_name}")
                                return None
                            
                            print(f"DEBUG: Using existing agent name: {existing_name}")

                            print("Building new Docker image...")
                            print("Pushing to ECR...")

                            # For rebuild, just build and push Docker image manually
                            # Don't use AgentCore toolkit to avoid conflicts
                            import subprocess

                            # Build Docker image
                            print(f"Building: bedrock_agentcore-{existing_name}:latest")
                            build_cmd = [
                                "docker",
                                "build",
                                "-t",
                                f"bedrock_agentcore-{existing_name}:latest",
                                ".",
                            ]
                            result = subprocess.run(build_cmd, text=True)
                            if result.returncode != 0:
                                print(
                                    f"Docker build failed with return code: {result.returncode}"
                                )
                                return None

                            # Get ECR repository URI - use the full agent name
                            ecr_uri = f"{self.aws_account_id}.dkr.ecr.{self.aws_region}.amazonaws.com/bedrock-agentcore-{existing_name}"
                            
                            # Validate ECR URI format to prevent command injection
                            if not re.match(r"^[a-zA-Z0-9\.\-/:_]+$", ecr_uri):
                                print(f"ERROR: Invalid ECR URI format: {ecr_uri}")
                                return None

                            # Tag and push to ECR
                            print(f"Tagging: {ecr_uri}:latest")
                            tag_cmd = [
                                "docker",
                                "tag",
                                f"bedrock_agentcore-{existing_name}:latest",
                                f"{ecr_uri}:latest",
                            ]
                            result = subprocess.run(tag_cmd, text=True)
                            if result.returncode != 0:
                                print(
                                    f"Docker tag failed with return code: {result.returncode}"
                                )
                                return None

                            # Login to ECR
                            print("Logging into ECR...")
                            # Get ECR password
                            get_password = [
                                "aws",
                                "ecr",
                                "get-login-password",
                                "--region",
                                self.aws_region,
                            ]
                            password_result = subprocess.run(
                                get_password, capture_output=True, text=True
                            )
                            if password_result.returncode != 0:
                                print(
                                    f"ECR get-login-password failed with return code: {password_result.returncode}"
                                )
                                return None

                            # Docker login with password
                            # Note: aws_account_id and aws_region are validated in __init__
                            # Using list format (not shell=True) prevents command injection
                            login_cmd = [
                                "docker",
                                "login",
                                "--username",
                                "AWS",
                                "--password-stdin",
                                f"{self.aws_account_id}.dkr.ecr.{self.aws_region}.amazonaws.com",
                            ]
                            result = subprocess.run(
                                login_cmd, input=password_result.stdout, text=True
                            )
                            if result.returncode != 0:
                                print(
                                    f"ECR login failed with return code: {result.returncode}"
                                )
                                return None

                            # Push image
                            # Note: ecr_uri is validated earlier (line ~742)
                            print(f"Pushing: {ecr_uri}:latest")
                            push_cmd = ["docker", "push", f"{ecr_uri}:latest"]
                            result = subprocess.run(push_cmd, text=True)
                            if result.returncode != 0:
                                print(
                                    f"Docker push failed with return code: {result.returncode}"
                                )
                                return None

                            print("✅ Docker image rebuilt and pushed to ECR")
                            print(
                                "✅ Existing agent will use the new image on next invocation"
                            )

                            agent_arn = existing_agent_arn

                    # Restore original working directory
                    os.chdir(original_cwd)

                    # Update JWT authorization
                    self.configure_jwt_authorization(cognito_config, self.server_name)

                    # Display deployment summary
                    self.display_deployment_summary(
                        cognito_config,
                        self.server_name,
                        existing_agent_arn,
                        rebuilt=True,
                    )

                else:
                    print(f"Reusing existing agent: {existing_agent_arn}")

                    # Update JWT authorization for existing agent
                    print("Updating JWT authorization for existing agent...")
                    self.configure_jwt_authorization(cognito_config, self.server_name)

                    # Restore original working directory
                    os.chdir(original_cwd)

                    # Display deployment summary with existing agent
                    self.display_deployment_summary(
                        cognito_config, self.server_name, existing_agent_arn
                    )

            else:
                # Create new agent only if none exists
                import time

                timestamp = int(time.time())
                fresh_agent_name = f"{self.server_name}_{timestamp}"

                print(f"🆕 Creating new agent: {fresh_agent_name}")

                # Change to parent directory for AgentCore Runtime
                parent_dir = Path(__file__).parent.parent
                os.chdir(parent_dir)

                runtime = Runtime()
                runtime.configure(
                    agent_name=fresh_agent_name,
                    entrypoint="src/mcp_postgres_server.py",
                    auto_create_execution_role=True,
                    requirements_file="requirements.txt",
                    region="us-east-1",
                    protocol="MCP",
                    authorizer_configuration={
                        "customJWTAuthorizer": {
                            "allowedClients": [cognito_config["client_id"]],
                            "discoveryUrl": cognito_config["discovery_url"],
                        }
                    }
                    if cognito_config
                    else None,
                )

                # Configure JWT authorization BEFORE launch
                print("Configuring JWT authorization...")
                self.configure_jwt_authorization(cognito_config, fresh_agent_name)

                # Restore our custom Docker files after toolkit configuration
                os.chdir(original_cwd)  # Go back to deployment directory
                self.cleanup_generated_dockerfile()
                os.chdir(parent_dir)  # Return to parent for launch

                # Step 3: Launch the server
                print("\nLaunching MCP server with JWT support...")
                runtime.launch()

                # Step 4: Update client configuration with new agent ARN
                self.update_client_with_new_agent(fresh_agent_name)

                # Sync config back to config directory after deployment
                self.sync_agentcore_config_after_deployment()

                # Clean up temporary Docker files (optional - keeps project clean)
                self.cleanup_temporary_docker_files()

                # Restore original working directory
                os.chdir(original_cwd)

                # Step 5: Display deployment summary
                self.display_deployment_summary(cognito_config, fresh_agent_name)

            return True

        except Exception as e:
            # Restore original working directory in case of error
            try:
                os.chdir(original_cwd)
            except OSError:
                pass
            print(f"\nDeployment failed: {e}")
            return False

    def configure_jwt_authorization(self, cognito_config, agent_name):
        """Configure JWT authorization in the agent config before deployment"""
        try:
            config_file = Path("../config/.bedrock_agentcore.yaml")

            if config_file.exists():
                with open(config_file, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)

                # Add JWT authorizer configuration to the agent
                if "agents" in config and agent_name in config["agents"]:
                    config["agents"][agent_name]["authorizer_configuration"] = {
                        "customJWTAuthorizer": {
                            "allowedClients": [cognito_config["client_id"]],
                            "discoveryUrl": cognito_config["discovery_url"],
                        }
                    }

                    # Write the updated configuration
                    with open(config_file, "w", encoding="utf-8") as f:
                        yaml.dump(config, f, default_flow_style=False)

                    print("JWT authorization configured in agent config")
                    return True
                else:
                    print(f" Agent {agent_name} not found in config")
                    return False
            else:
                print(" Agent config file not found")
                return False

        except Exception as e:
            print(f" Could not configure JWT authorization: {e}")
            return False

    def update_client_with_new_agent(self, agent_name):
        """Update the MCP client app with the new agent ARN"""
        try:
            # Read the current agent configuration to get the ARN
            config_file = Path("../config/.bedrock_agentcore.yaml")
            if config_file.exists():
                with open(config_file, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)

                if (
                    "agents" in config
                    and agent_name in config["agents"]
                    and "bedrock_agentcore" in config["agents"][agent_name]
                    and "agent_arn" in config["agents"][agent_name]["bedrock_agentcore"]
                ):
                    new_arn = config["agents"][agent_name]["bedrock_agentcore"][
                        "agent_arn"
                    ]

                    # Update the MCP client app
                    client_file = Path("../src/database_query_client.py")
                    if client_file.exists():
                        with open(client_file, "r", encoding="utf-8") as f:
                            content = f.read()

                        # Find and replace the agent ARN line
                        import re

                        pattern = r'agent_arn = "arn:aws:bedrock-agentcore:us-east-1:975910639313:runtime/[^"]*"'
                        replacement = (
                            f'agent_arn = "{new_arn}"  # Fresh agent with JWT support'
                        )

                        updated_content = re.sub(pattern, replacement, content)

                        with open(client_file, "w", encoding="utf-8") as f:
                            f.write(updated_content)

                        print(f"Updated MCP client with new agent ARN: {new_arn}")
                        return new_arn

        except Exception as e:
            print(f" Could not update client configuration: {e}")
            return None

    def update_agentcore_config(self, cognito_config):
        """Update bedrock agentcore configuration with Cognito authorizer"""
        config_file = Path("../config/.bedrock_agentcore.yaml")

        # Ensure the config directory exists
        config_file.parent.mkdir(exist_ok=True)

        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            # Add Cognito authorizer configuration
            if "agents" in config and self.server_name in config["agents"]:
                config["agents"][self.server_name]["authorizer_configuration"] = {
                    "customJWTAuthorizer": {
                        "allowedClients": [cognito_config["client_id"]],
                        "discoveryUrl": cognito_config["discovery_url"],
                    }
                }

                with open(config_file, "w", encoding="utf-8") as f:
                    yaml.dump(config, f, default_flow_style=False)

                print("Updated AgentCore configuration with Cognito authorizer")

    def display_deployment_summary(
        self, cognito_config, agent_name=None, agent_arn=None, rebuilt=False
    ):
        """Display deployment summary"""
        print("\n" + "=" * 60)
        print("DEPLOYMENT COMPLETED SUCCESSFULLY!")
        print("=" * 60)

        print("\nMCP Server Details:")
        print(f"   Server Name: {agent_name or self.server_name}")
        print("   Protocol: MCP with JWT Authentication")
        print("   Database: PostgreSQL RDS")

        if agent_arn:
            print(f"   Agent ARN: {agent_arn}")
            if rebuilt:
                print("   Status: Rebuilt Docker image and updated existing agent")
            else:
                print("   Status: Reused existing agent")
        else:
            print("   Status: Created new agent")

        print("\nCognito Authentication:")
        print(f"   User Pool ID: {cognito_config['user_pool_id']}")
        print(f"   Client ID: {cognito_config['client_id']}")
        print("   Test User: testuser@example.com")
        print("   JWT Token: Generated and saved")

        print("\nTesting:")
        print("   You can now test your MCP server with the generated JWT tokens")
        print("   Cognito configuration: ../config/cognito.json")
        print("   AgentCore configuration: ../config/.bedrock_agentcore.yaml")

        print("\nMonitoring:")
        print(
            f"   Check logs with: aws logs tail /aws/bedrock-agentcore/runtimes/{self.server_name}-*/DEFAULT --follow"
        )

        print("\nNote:")
        if agent_arn:
            print("   Reused existing AWS agent - no new resources created")
            print("   JWT authorization updated for existing agent")
        else:
            print("   AWS agents retain their original ARN even when updated")
            print(
                "   This is normal behavior - the agent functionality is updated correctly"
            )
        print(f"   Local configuration uses consistent naming: {self.server_name}")


def main():
    """Main deployment function"""
    parser = argparse.ArgumentParser(
        description="Deploy MCP Server to Amazon Bedrock AgentCore Runtime",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 deploy_mcp_agentcore.py           # Normal deployment (reuse existing agent)
  python3 deploy_mcp_agentcore.py --rebuild # Rebuild Docker image and update existing agent
        """,
    )
    parser.add_argument(
        "--rebuild",
        "-r",
        action="store_true",
        help="Rebuild Docker image and update existing agent (or create new if none exists)",
    )

    args = parser.parse_args()

    if args.rebuild:
        print("🔄 REBUILD MODE: Will rebuild Docker image and update existing agent")
    else:
        print("♻️  NORMAL MODE: Will reuse existing agent if available")

    # Check Python version compatibility first
    check_python_version()

    # Check required dependencies
    import importlib.util

    if importlib.util.find_spec("bedrock_agentcore_starter_toolkit") is None:
        print("Failed to find AgentCore Runtime toolkit")
        print("Please install the required dependencies:")
        print("   pip install bedrock_agentcore_starter_toolkit")
        sys.exit(1)
    else:
        print("AgentCore Runtime toolkit loaded successfully")

    parser = argparse.ArgumentParser(
        description="Enhanced MCP Server Deployment with Cognito Setup"
    )
    parser.add_argument(
        "--server-name",
        default="mcp_db_server",
        help="Name for the MCP server (default: mcp_db_server)",
    )
    parser.add_argument(
        "--rebuild",
        "-r",
        action="store_true",
        help="Rebuild Docker image and update existing agent (or create new if none exists)",
    )

    args = parser.parse_args()

    if args.rebuild:
        print("🔄 REBUILD MODE: Will rebuild Docker image and update existing agent")
    else:
        print("♻️  NORMAL MODE: Will reuse existing agent if available")

    deployment = EnhancedMCPDeployment(args.server_name)
    success = deployment.deploy(force_rebuild=args.rebuild)

    if success:
        print("\nDeployment completed successfully!")
        sys.exit(0)
    else:
        print("\nDeployment failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
