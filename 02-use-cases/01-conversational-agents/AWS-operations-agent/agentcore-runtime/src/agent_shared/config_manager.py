"""
AgentCore Configuration Manager
Unified configuration management for all AgentCore consumers
"""

import yaml
from typing import Dict, Any, List
from pathlib import Path
from . import mylogger

logger = mylogger.get_logger()


class AgentCoreConfigManager:
    """Unified configuration management for all AgentCore consumers"""

    def __init__(self, environment: str = "debug"):
        """
        Initialize configuration manager

        Args:
            environment: Environment type ("debug" or "performance")
        """
        self.environment = environment
        self.project_root = self._find_project_root()
        self._validator = None  # Will be imported when needed to avoid circular imports

    def _find_project_root(self) -> Path:
        """Find the project root directory containing .agentcore.yaml"""
        current = Path(__file__).parent
        while current != current.parent:
            if (current / ".agentcore.yaml").exists():
                return current
            current = current.parent

        # Fallback to parent of shared directory
        return Path(__file__).parent.parent

    def _load_yaml(self, relative_path: str) -> Dict[str, Any]:
        """Load YAML file relative to project root"""
        file_path = self.project_root / relative_path

        if not file_path.exists():
            logger.warning(f"Configuration file not found: {file_path}")
            return {}

        try:
            with open(file_path, "r") as f:
                content = yaml.safe_load(f) or {}
            logger.debug(f"Loaded configuration from {file_path}")
            return content
        except Exception as e:
            logger.error(f"Failed to load configuration from {file_path}: {e}")
            return {}

    def _save_yaml(self, relative_path: str, data: Dict[str, Any]) -> None:
        """Save YAML file relative to project root"""
        file_path = self.project_root / relative_path

        # Create directory if it doesn't exist
        file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(file_path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, indent=2)
            logger.debug(f"Saved configuration to {file_path}")
        except Exception as e:
            logger.error(f"Failed to save configuration to {file_path}: {e}")
            raise

    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """Deep merge two dictionaries, with override taking precedence"""
        result = base.copy()

        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value

        return result

    # Static Configuration Methods
    def get_static_config(self) -> Dict[str, Any]:
        """Get static configuration (version controlled)"""
        # Load consolidated static config file
        return self._load_yaml("config/static-config.yaml")

    def get_base_settings(self) -> Dict[str, Any]:
        """Get base settings only (backward compatibility)"""
        return self.get_static_config()

    # Dynamic Configuration Methods
    def get_dynamic_config(self) -> Dict[str, Any]:
        """Get dynamic configuration (deployment generated)"""
        # Load consolidated dynamic config file
        return self._load_yaml("config/dynamic-config.yaml")

    def update_dynamic_config(self, updates: Dict[str, Any]) -> None:
        """Update dynamic configuration file"""
        file_path = "config/dynamic-config.yaml"
        current = self._load_yaml(file_path)
        updated = self._deep_merge(current, updates)
        self._save_yaml(file_path, updated)

    # Merged Configuration Methods
    def get_merged_config(self) -> Dict[str, Any]:
        """Get complete configuration (static + dynamic merged)"""
        static = self.get_static_config()
        dynamic = self.get_dynamic_config()
        return self._deep_merge(static, dynamic)

    # Convenience Methods for Backward Compatibility
    def get_model_settings(self) -> Dict[str, Any]:
        """Get model settings (backward compatibility)"""
        config = self.get_merged_config()
        aws_config = config.get("aws", {})
        agents_config = config.get("agents", {})

        return {
            "region_name": aws_config.get("region", "us-east-1"),
            "model_id": agents_config.get("modelid", "global.anthropic.claude-haiku-4-5-20251001-v1:0"),
            "temperature": 0.7,  # Default from current usage
            "max_tokens": 4096,  # Default from current usage
        }

    def get_gateway_url(self) -> str:
        """Get gateway URL (backward compatibility)"""
        config = self.get_merged_config()
        return config.get("gateway", {}).get("url", "")

    def get_oauth_settings(self) -> Dict[str, Any]:
        """Get OAuth settings (backward compatibility)"""
        config = self.get_merged_config()
        return config.get("okta", {})

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """Get Bedrock agent tools schema (for gateway target creation)"""
        config = self.get_static_config()
        return config.get("tools_schema", [])

    def get_mcp_lambda_config(self) -> Dict[str, Any]:
        """Get MCP lambda configuration (for deployment and gateway operations)"""
        config = self.get_merged_config()
        return config.get("mcp_lambda", {})

    def validate(self) -> bool:
        """Validate current configuration"""
        try:
            # Import validator here to avoid circular imports
            if self._validator is None:
                from .config_validator import ConfigValidator

                self._validator = ConfigValidator()

            static = self.get_static_config()
            dynamic = self.get_dynamic_config()
            self.get_merged_config()

            self._validator.validate_static(static)
            self._validator.validate_dynamic(dynamic)

            return True
        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            return False
