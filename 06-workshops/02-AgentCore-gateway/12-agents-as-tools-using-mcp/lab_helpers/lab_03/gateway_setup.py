"""
Lab 03: AgentCore Gateway and Runtime Target Setup

Creates Gateway infrastructure and registers the remediation Runtime as a target.

Based on Lab-02 patterns + gateway-to-runtime M2M authentication.

Features:
- Creates Gateway service role with proper trust policies
- Creates AgentCore Gateway with IAM authentication
- Registers remediation Runtime as MCP target
- Supports M2M OAuth2 authentication (optional)
- Configuration stored in Parameter Store
"""

import json
import boto3
import time
import logging
from typing import Dict, Optional, List
from botocore.exceptions import ClientError

# Import centralized configuration
from lab_helpers.config import AWS_REGION

logger = logging.getLogger(__name__)

# Configuration
REGION = AWS_REGION  # Use centralized region from config.py
PREFIX = "aiml301"
GATEWAY_NAME = f"{PREFIX}-remediation-gateway"
GATEWAY_ROLE_NAME = f"{PREFIX}-remediation-gateway-role"
GATEWAY_POLICY_NAME = f"{PREFIX}-gateway-runtime-policy"


class AgentCoreGatewaySetup:
    """Setup helper for AgentCore Gateway with Runtime targets"""

    def __init__(self, region: str = REGION, prefix: str = PREFIX, verbose: bool = True):
        """
        Initialize gateway setup helper.

        Args:
            region: AWS region
            prefix: Resource naming prefix
            verbose: Enable logging
        """
        self.region = region
        self.prefix = prefix
        self.verbose = verbose

        # AWS clients
        self.iam = boto3.client("iam", region_name=region)
        self.agentcore = boto3.client("bedrock-agentcore-control", region_name=region)
        self.ssm = boto3.client("ssm", region_name=region)
        self.sts = boto3.client("sts", region_name=region)

        # Get account ID
        self.account_id = self.sts.get_caller_identity()["Account"]

        if verbose:
            logging.basicConfig(level=logging.INFO)
            logger.setLevel(logging.INFO)

    def _log(self, message: str):
        """Log message"""
        print(f"✓ {message}")
        logger.info(message)

    def _error(self, message: str):
        """Log error"""
        print(f"✗ {message}")
        logger.error(message)

    def create_gateway_service_role(self) -> Dict:
        """
        Create IAM service role for Gateway to invoke Runtime targets.

        Gateway needs permissions to:
        1. Invoke Runtime targets
        2. Access CloudWatch logs
        3. Manage AgentCore resources
        4. Access OAuth credentials (for M2M auth)

        Returns:
            Dict with role ARN, role name, and metadata
        """
        self._log("Creating IAM role for Gateway...")

        # Trust policy: Allow bedrock-agentcore service to assume role
        # Restricted to Gateway ARNs in this account and region
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
                            "aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:gateway/*"
                        },
                    },
                }
            ],
        }

        # Permissions policy: Gateway operations, Runtime invocation, CloudWatch logs
        # Updated to match working riv301 deployment with WorkloadIdentity and correct OAuth2/Secrets patterns
        permissions_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "InvokeRuntimeTarget",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:InvokeRuntime",
                        "bedrock-agentcore:InvokeGateway",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "InvokeLambda",
                    "Effect": "Allow",
                    "Action": "lambda:InvokeFunction",
                    "Resource": f"arn:aws:lambda:{self.region}:{self.account_id}:function:*",
                },
                {
                    "Sid": "WorkloadIdentity",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetWorkloadAccessToken",
                        "bedrock-agentcore:CreateWorkloadIdentity",
                    ],
                    "Resource": [
                        f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:workload-identity-directory/default",
                        f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:workload-identity-directory/default/workload-identity/*",
                    ],
                },
                {
                    "Sid": "OAuth2Credentials",
                    "Effect": "Allow",
                    "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
                    "Resource": [
                        f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:token-vault/default",
                        f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:token-vault/*/oauth2credentialprovider/*",
                        f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:workload-identity-directory/default",
                        f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:workload-identity-directory/default/workload-identity/*",
                    ],
                },
                {
                    "Sid": "SecretsManager",
                    "Effect": "Allow",
                    "Action": ["secretsmanager:GetSecretValue"],
                    "Resource": [
                        f"arn:aws:secretsmanager:{self.region}:{self.account_id}:secret:bedrock-agentcore-identity!*",
                        f"arn:aws:secretsmanager:{self.region}:{self.account_id}:secret:bedrock-agentcore-*",
                    ],
                },
                {
                    "Sid": "CloudWatchLogs",
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    "Resource": f"arn:aws:logs:{self.region}:{self.account_id}:log-group:/aws/bedrock-agentcore/gateways/*",
                },
            ],
        }

        try:
            # Check if role already exists
            try:
                role = self.iam.get_role(RoleName=GATEWAY_ROLE_NAME)
                self._log(f"Gateway service role already exists: {GATEWAY_ROLE_NAME}")
                role_arn = role["Role"]["Arn"]

                # Update trust policy to ensure it has gamma service principals
                self.iam.update_assume_role_policy(RoleName=GATEWAY_ROLE_NAME, PolicyDocument=json.dumps(trust_policy))
                self._log("Trust policy updated")

            except self.iam.exceptions.NoSuchEntityException:
                # Create new role
                response = self.iam.create_role(
                    RoleName=GATEWAY_ROLE_NAME,
                    AssumeRolePolicyDocument=json.dumps(trust_policy),
                    Description="Service role for AgentCore Gateway to invoke Runtime targets - Lab 03",
                )
                role_arn = response["Role"]["Arn"]
                self._log(f"Gateway service role created: {GATEWAY_ROLE_NAME}")

                # Wait for role to propagate
                time.sleep(10)

            # Attach permissions policy
            self.iam.put_role_policy(
                RoleName=GATEWAY_ROLE_NAME,
                PolicyName=GATEWAY_POLICY_NAME,
                PolicyDocument=json.dumps(permissions_policy),
            )
            self._log(f"Permissions policy attached: {GATEWAY_POLICY_NAME}")

            # Store in Parameter Store
            self.ssm.put_parameter(
                Name=f"/{self.prefix}/lab-03/gateway-role-arn",
                Value=role_arn,
                Type="String",
                Overwrite=True,
                Description="Gateway service role ARN for Lab-03",
            )
            self._log("Gateway role ARN stored in Parameter Store")

            return {
                "role_arn": role_arn,
                "role_name": GATEWAY_ROLE_NAME,
                "policy_name": GATEWAY_POLICY_NAME,
                "account_id": self.account_id,
                "region": self.region,
            }

        except Exception as e:
            self._error(f"Failed to create Gateway service role: {e}")
            raise

    def create_gateway(
        self,
        gateway_name: str = GATEWAY_NAME,
        role_arn: Optional[str] = None,
        protocol_type: str = "MCP",
        authorizer_type: str = "AWS_IAM",
        authorizer_configuration: Optional[Dict] = None,
    ) -> Dict:
        """
        Create AgentCore Gateway.

        Args:
            gateway_name: Name for the gateway
            role_arn: Service role ARN (fetches from Parameter Store if not provided)
            protocol_type: Gateway protocol (MCP, HTTP, etc.)
            authorizer_type: Inbound auth type (AWS_IAM, CUSTOM_JWT)
            authorizer_configuration: JWT authorizer config (required for CUSTOM_JWT)
                Format: {"customJWTAuthorizer": {"discoveryUrl": "...", "allowedClients": [...]}}

        Returns:
            Dict with gateway ID, URL, and metadata
        """
        self._log("Creating AgentCore Gateway...")

        # Get role ARN if not provided
        if not role_arn:
            try:
                response = self.ssm.get_parameter(Name=f"/{self.prefix}/lab-03/gateway-role-arn")
                role_arn = response["Parameter"]["Value"]
                self._log("Retrieved Gateway role ARN from Parameter Store")
            except ClientError:
                self._log("Gateway role not found. Creating...")
                role_info = self.create_gateway_service_role()
                role_arn = role_info["role_arn"]

        try:
            # Build create_gateway API call parameters
            create_params = {
                "name": gateway_name,
                "roleArn": role_arn,
                "protocolType": protocol_type,
                "authorizerType": authorizer_type,
            }

            # Add authorizer configuration if provided (required for CUSTOM_JWT)
            if authorizer_configuration:
                create_params["authorizerConfiguration"] = authorizer_configuration

            # Create gateway
            response = self.agentcore.create_gateway(**create_params)

            gateway_id = response["gatewayId"]
            gateway_url = response["gatewayUrl"]

            self._log(f"Gateway created: {gateway_name}")

            gateway_info = {
                "gateway_id": gateway_id,
                "gateway_url": gateway_url,
                "gateway_name": gateway_name,
                "role_arn": role_arn,
                "protocol_type": protocol_type,
                "authorizer_type": authorizer_type,
                "region": self.region,
            }

            # Store in Parameter Store
            self.ssm.put_parameter(
                Name=f"/{self.prefix}/lab-03/gateway-config",
                Value=json.dumps(gateway_info, indent=2),
                Type="String",
                Overwrite=True,
                Description="Lab-03 Gateway configuration",
            )
            self._log("Gateway configuration stored in Parameter Store")

            return gateway_info

        except ClientError as e:
            if "AlreadyExists" in str(e) or "already" in str(e).lower():
                self._log(f"Gateway already exists: {gateway_name}")
                # Try to retrieve existing gateway
                return self._get_gateway_by_name(gateway_name)
            else:
                self._error(f"Failed to create gateway: {e}")
                raise

    def _get_gateway_by_name(self, gateway_name: str) -> Dict:
        """Retrieve existing gateway by name"""
        try:
            response = self.agentcore.list_gateways()
            for gw in response.get("gateways", []):
                if gw["name"] == gateway_name:
                    return {
                        "gateway_id": gw["gatewayId"],
                        "gateway_url": gw["gatewayUrl"],
                        "gateway_name": gw["name"],
                        "region": self.region,
                    }
        except Exception as e:
            self._error(f"Failed to retrieve gateway: {e}")
        return None

    def register_runtime_target(
        self,
        gateway_id: str,
        runtime_arn: str,
        target_name: str = "remediation-runtime-target",
        tool_schema: Optional[List[Dict]] = None,
        credentials_type: str = "GATEWAY_IAM_ROLE",
    ) -> Dict:
        """
        Register Runtime as a target on the Gateway.

        Args:
            gateway_id: Gateway identifier
            runtime_arn: Runtime ARN to register as target
            target_name: Name for the target
            tool_schema: Tool schema definition (optional)
            credentials_type: Credential provider type (GATEWAY_IAM_ROLE, OAUTH2)

        Returns:
            Dict with target ID and metadata
        """
        self._log(f"Registering Runtime as Gateway target: {target_name}...")

        # Default tool schema if not provided
        if not tool_schema:
            tool_schema = [
                {
                    "name": "invoke_remediation_agent",
                    "description": "Invoke the remediation agent with Code Interpreter for infrastructure automation",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Natural language query for remediation analysis",
                            }
                        },
                        "required": ["query"],
                    },
                }
            ]

        try:
            # Construct MCP endpoint URL from Runtime ARN
            # Format: https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT
            encoded_arn = runtime_arn.replace(":", "%3A").replace("/", "%2F")
            mcp_endpoint_url = f"https://bedrock-agentcore.{self.region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"

            self._log(f"MCP Endpoint URL: {mcp_endpoint_url}")

            # Register Runtime as MCP target
            # Use correct structure: "mcp" wrapper with "mcpServer" using "endpoint" (not "runtimeArn")
            response = self.agentcore.create_gateway_target(
                gatewayIdentifier=gateway_id,
                name=target_name,
                targetConfiguration={"mcp": {"mcpServer": {"endpoint": mcp_endpoint_url}}},
                credentialProviderConfigurations=[{"credentialProviderType": credentials_type}],
            )

            target_id = response["targetId"]

            self._log("Runtime registered as Gateway target")
            self._log(f"  Target ID: {target_id}")
            self._log(f"  Target Name: {target_name}")
            self._log(f"  Runtime ARN: {runtime_arn}")

            target_info = {
                "target_id": target_id,
                "target_name": target_name,
                "runtime_arn": runtime_arn,
                "gateway_id": gateway_id,
                "credentials_type": credentials_type,
                "tool_schema": tool_schema,
            }

            # Store in Parameter Store
            self.ssm.put_parameter(
                Name=f"/{self.prefix}/lab-03/gateway-runtime-target",
                Value=json.dumps(target_info, indent=2),
                Type="String",
                Overwrite=True,
                Description="Lab-03 Gateway Runtime target configuration",
            )
            self._log("Target configuration stored in Parameter Store")

            return target_info

        except Exception as e:
            self._error(f"Failed to register Runtime target: {e}")
            raise

    def list_gateway_targets(self, gateway_id: str) -> List[Dict]:
        """List all targets registered on the Gateway"""
        try:
            response = self.agentcore.list_gateway_targets(gatewayIdentifier=gateway_id)
            targets = response.get("targets", [])
            self._log(f"Found {len(targets)} Gateway target(s)")
            return targets
        except Exception as e:
            self._error(f"Failed to list Gateway targets: {e}")
            return []

    def get_gateway_status(self, gateway_id: str) -> Dict:
        """Get Gateway status"""
        try:
            response = self.agentcore.get_gateway(gatewayIdentifier=gateway_id)
            gateway = response["gateway"]
            status = {
                "gateway_id": gateway["gatewayId"],
                "gateway_name": gateway["name"],
                "status": gateway.get("status"),
                "url": gateway.get("gatewayUrl"),
                "protocol": gateway.get("protocolType"),
                "created_at": gateway.get("createdAt"),
                "last_modified": gateway.get("lastModifiedAt"),
            }
            self._log(f"Gateway status: {status['status']}")
            return status
        except Exception as e:
            self._error(f"Failed to get Gateway status: {e}")
            return {"status": "UNKNOWN"}

    def get_stored_config(self) -> Dict:
        """Retrieve stored gateway and runtime target configuration from Parameter Store"""
        try:
            config = {}

            # Get gateway config
            try:
                response = self.ssm.get_parameter(Name=f"/{self.prefix}/lab-03/gateway-config")
                config["gateway"] = json.loads(response["Parameter"]["Value"])
                self._log("Retrieved Gateway configuration from Parameter Store")
            except ClientError:
                self._log("Gateway configuration not found in Parameter Store")

            # Get runtime target config
            try:
                response = self.ssm.get_parameter(Name=f"/{self.prefix}/lab-03/gateway-runtime-target")
                config["runtime_target"] = json.loads(response["Parameter"]["Value"])
                self._log("Retrieved Runtime target configuration from Parameter Store")
            except ClientError:
                self._log("Runtime target configuration not found in Parameter Store")

            return config

        except Exception as e:
            self._error(f"Failed to retrieve stored configuration: {e}")
            return {}

    def cleanup(self, force: bool = False) -> bool:
        """
        Clean up Lab-03 Gateway resources.

        Args:
            force: Force deletion without confirmation

        Returns:
            True if cleanup successful
        """
        self._log("Starting Gateway cleanup...")

        if not force:
            confirm = input("Delete Lab-03 Gateway and related resources? This cannot be undone. (yes/no): ")
            if confirm.lower() != "yes":
                self._log("Cleanup cancelled")
                return False

        try:
            # Get gateway ID from Parameter Store
            try:
                response = self.ssm.get_parameter(Name=f"/{self.prefix}/lab-03/gateway-config")
                config = json.loads(response["Parameter"]["Value"])
                gateway_id = config.get("gateway_id")

                if gateway_id:
                    self.agentcore.delete_gateway(gatewayIdentifier=gateway_id)
                    self._log(f"Deleted Gateway: {gateway_id}")
            except ClientError:
                pass

            # Delete IAM role and policies
            try:
                self.iam.delete_role_policy(RoleName=GATEWAY_ROLE_NAME, PolicyName=GATEWAY_POLICY_NAME)
                self._log(f"Deleted role policy: {GATEWAY_POLICY_NAME}")
            except ClientError:
                pass

            try:
                self.iam.delete_role(RoleName=GATEWAY_ROLE_NAME)
                self._log(f"Deleted IAM role: {GATEWAY_ROLE_NAME}")
            except ClientError:
                pass

            # Delete Parameter Store entries
            try:
                self.ssm.delete_parameter(Name=f"/{self.prefix}/lab-03/gateway-role-arn")
                self._log("Deleted Parameter Store entry: gateway-role-arn")
            except ClientError:
                pass

            try:
                self.ssm.delete_parameter(Name=f"/{self.prefix}/lab-03/gateway-config")
                self._log("Deleted Parameter Store entry: gateway-config")
            except ClientError:
                pass

            try:
                self.ssm.delete_parameter(Name=f"/{self.prefix}/lab-03/gateway-runtime-target")
                self._log("Deleted Parameter Store entry: gateway-runtime-target")
            except ClientError:
                pass

            self._log("Gateway cleanup completed")
            return True

        except Exception as e:
            self._error(f"Cleanup failed: {e}")
            raise
