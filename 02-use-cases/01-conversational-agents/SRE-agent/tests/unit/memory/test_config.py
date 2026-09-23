from sre_agent.memory.config import MemoryConfig, _load_memory_config


class TestMemoryConfig:
    """Tests for MemoryConfig model."""

    def test_memory_config_defaults(self):
        """Test MemoryConfig with default values."""
        config = MemoryConfig()

        assert config.enabled is True
        assert config.memory_name == "sre-agent-memory"
        assert config.region == "us-east-1"
        assert config.preferences_retention_days == 90
        assert config.infrastructure_retention_days == 30
        assert config.investigation_retention_days == 60
        assert config.auto_capture_preferences is True
        assert config.auto_capture_infrastructure is True
        assert config.auto_generate_summaries is True

    def test_memory_config_custom_values(self):
        """Test MemoryConfig with custom values."""
        config = MemoryConfig(
            enabled=False,
            memory_name="custom-memory",
            region="us-west-2",
            preferences_retention_days=30,
            infrastructure_retention_days=15,
            investigation_retention_days=45,
            auto_capture_preferences=False,
            auto_capture_infrastructure=False,
            auto_generate_summaries=False,
        )

        assert config.enabled is False
        assert config.memory_name == "custom-memory"
        assert config.region == "us-west-2"
        assert config.preferences_retention_days == 30
        assert config.infrastructure_retention_days == 15
        assert config.investigation_retention_days == 45
        assert config.auto_capture_preferences is False
        assert config.auto_capture_infrastructure is False
        assert config.auto_generate_summaries is False

    def test_load_memory_config_success(self):
        """Test successful memory config loading."""
        config = _load_memory_config()

        assert isinstance(config, MemoryConfig)
        assert config.enabled is True  # Default value

    def test_memory_config_validation(self):
        """Test memory config field validation."""
        # Test that retention days are positive integers
        config = MemoryConfig(
            preferences_retention_days=1,
            infrastructure_retention_days=1,
            investigation_retention_days=1,
        )

        assert config.preferences_retention_days == 1
        assert config.infrastructure_retention_days == 1
        assert config.investigation_retention_days == 1
