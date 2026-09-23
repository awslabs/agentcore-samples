"""
AgentCore Configuration Validator
Validates AgentCore configuration against schema and business rules
"""

import re
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class ConfigValidator:
    """Validate AgentCore configuration"""

    def __init__(self):
        """Initialize configuration validator"""
        self.arn_pattern = re.compile(r"^arn:aws:[\w-]+:[\w-]*:\d{12}:.*")
        self.url_pattern = re.compile(r"^https?://[^\s/$.?#].[^\s]*$")
        self.valid_log_levels = {
            "DEBUG",
            "INFO",
            "WARN",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }

    def validate_static(self, config: Dict[str, Any]) -> None:
        """Validate static configuration"""
        self._validate_required_fields(config, ["aws", "agents", "okta"])
        self._validate_aws_config(config.get("aws", {}))
        self._validate_agent_config(config.get("agents", {}))
        self._validate_okta_config(config.get("okta", {}))

        # Validate tools schema if present
        if "tools_schema" in config:
            self._validate_tools_schema(config["tools_schema"])

    def validate_dynamic(self, config: Dict[str, Any]) -> None:
        """Validate dynamic configuration"""
        # Validate ARN formats
        if "runtime" in config:
            self._validate_runtime_arns(config["runtime"])

        if "mcp_lambda" in config:
            self._validate_mcp_lambda_config(config["mcp_lambda"])

        # Validate URLs
        if "gateway" in config:
            self._validate_gateway_config(config["gateway"])

    def _validate_required_fields(self, config: Dict[str, Any], required_fields: List[str]) -> None:
        """Validate that required fields exist"""
        for field in required_fields:
            if field not in config:
                raise ValueError(f"Missing required configuration field: {field}")

    def _validate_aws_config(self, aws_config: Dict[str, Any]) -> None:
        """Validate AWS configuration"""
        if not aws_config.get("region"):
            raise ValueError("AWS region is required")

        if not aws_config.get("account_id"):
            raise ValueError("AWS account ID is required")

        # Validate account ID format (12 digits)
        account_id = str(aws_config.get("account_id", ""))
        if not re.match(r"^\d{12}$", account_id):
            raise ValueError(f"Invalid AWS account ID format: {account_id}")

    def _validate_agent_config(self, agents_config: Dict[str, Any]) -> None:
        """Validate agents configuration"""
        if not agents_config.get("modelid"):
            raise ValueError("Agent model ID is required")

        # Validate max_concurrent if present
        max_concurrent = agents_config.get("max_concurrent")
        if max_concurrent is not None:
            if not isinstance(max_concurrent, int) or max_concurrent < 1:
                raise ValueError(f"max_concurrent must be a positive integer, got: {max_concurrent}")

    def _validate_okta_config(self, okta_config: Dict[str, Any]) -> None:
        """Validate Okta configuration"""
        if not okta_config.get("domain"):
            raise ValueError("Okta domain is required")

        jwt_config = okta_config.get("jwt", {})
        if not jwt_config.get("audience"):
            raise ValueError("Okta JWT audience is required")

        if not jwt_config.get("discovery_url"):
            raise ValueError("Okta JWT discovery URL is required")

        # Validate discovery URL format
        discovery_url = jwt_config.get("discovery_url", "")
        if not self.url_pattern.match(discovery_url):
            raise ValueError(f"Invalid Okta discovery URL format: {discovery_url}")

    def _validate_tools_schema(self, tools_schema: List[Dict[str, Any]]) -> None:
        """Validate tools schema"""
        if not isinstance(tools_schema, list):
            raise ValueError("Tools schema must be a list")

        for i, tool in enumerate(tools_schema):
            if not isinstance(tool, dict):
                raise ValueError(f"Tool {i} must be a dictionary")

            if not tool.get("name"):
                raise ValueError(f"Tool {i} missing required 'name' field")

            if not tool.get("description"):
                raise ValueError(f"Tool {i} missing required 'description' field")

            if "inputSchema" not in tool:
                raise ValueError(f"Tool {i} missing required 'inputSchema' field")

    def _validate_runtime_arns(self, runtime_config: Dict[str, Any]) -> None:
        """Validate runtime ARN formats"""
        for agent_type in ["diy_agent", "sdk_agent"]:
            if agent_type in runtime_config:
                agent_config = runtime_config[agent_type]

                # Validate ARN format if present
                arn = agent_config.get("arn")
                if arn and not self.arn_pattern.match(arn):
                    raise ValueError(f"Invalid ARN format for {agent_type}: {arn}")

                # Validate endpoint ARN format if present
                endpoint_arn = agent_config.get("endpoint_arn")
                if endpoint_arn and not self.arn_pattern.match(endpoint_arn):
                    raise ValueError(f"Invalid endpoint ARN format for {agent_type}: {endpoint_arn}")

    def _validate_mcp_lambda_config(self, mcp_config: Dict[str, Any]) -> None:
        """Validate MCP lambda configuration"""
        # Validate function ARN if present
        function_arn = mcp_config.get("function_arn")
        if function_arn and not self.arn_pattern.match(function_arn):
            raise ValueError(f"Invalid MCP lambda function ARN format: {function_arn}")

        # Validate role ARN if present
        role_arn = mcp_config.get("role_arn")
        if role_arn and not self.arn_pattern.match(role_arn):
            raise ValueError(f"Invalid MCP lambda role ARN format: {role_arn}")

    def _validate_gateway_config(self, gateway_config: Dict[str, Any]) -> None:
        """Validate gateway configuration"""
        # Validate gateway URL if present
        gateway_url = gateway_config.get("url")
        if gateway_url and not self.url_pattern.match(gateway_url):
            raise ValueError(f"Invalid gateway URL format: {gateway_url}")

        # Validate gateway ARN if present
        gateway_arn = gateway_config.get("arn")
        if gateway_arn and not self.arn_pattern.match(gateway_arn):
            raise ValueError(f"Invalid gateway ARN format: {gateway_arn}")

    def _validate_sampling_rates(self, config: Dict[str, Any]) -> None:
        """Validate sampling rates are between 0.0 and 1.0"""

        def check_sampling_rate(value: Any, path: str) -> None:
            if isinstance(value, (int, float)):
                if not (0.0 <= value <= 1.0):
                    raise ValueError(f"Sampling rate at {path} must be between 0.0 and 1.0, got: {value}")

        # Check observability sampling rates
        obs_config = config.get("observability", {})
        if "tracing" in obs_config:
            tracing = obs_config["tracing"]
            if "sampling_rate" in tracing:
                check_sampling_rate(tracing["sampling_rate"], "observability.tracing.sampling_rate")

    def _validate_log_levels(self, config: Dict[str, Any]) -> None:
        """Validate log levels are valid"""

        def check_log_level(value: Any, path: str) -> None:
            if isinstance(value, str) and value.upper() not in self.valid_log_levels:
                raise ValueError(
                    f"Invalid log level at {path}: {value}. Valid levels: {', '.join(self.valid_log_levels)}"
                )

        # Check observability log levels
        obs_config = config.get("observability", {})
        if "logging" in obs_config:
            logging_config = obs_config["logging"]
            if "level" in logging_config:
                check_log_level(logging_config["level"], "observability.logging.level")
