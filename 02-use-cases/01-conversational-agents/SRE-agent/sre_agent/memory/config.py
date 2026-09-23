import logging

from pydantic import BaseModel, Field

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)


class MemoryConfig(BaseModel):
    """Configuration for SRE Agent memory system."""

    enabled: bool = Field(default=True, description="Enable memory system")
    memory_name: str = Field(
        default="sre_agent_memory", description="Base name for memory instances"
    )
    region: str = Field(
        default="us-east-1", description="AWS region for memory storage"
    )

    # Retention settings
    preferences_retention_days: int = Field(
        default=90, description="Days to retain user preferences"
    )
    infrastructure_retention_days: int = Field(
        default=30, description="Days to retain infrastructure knowledge"
    )
    investigation_retention_days: int = Field(
        default=60, description="Days to retain investigation summaries"
    )

    # Feature flags
    auto_capture_preferences: bool = Field(
        default=True, description="Automatically capture user preferences"
    )
    auto_capture_infrastructure: bool = Field(
        default=True, description="Automatically capture infrastructure patterns"
    )
    auto_generate_summaries: bool = Field(
        default=True, description="Automatically generate investigation summaries"
    )


def _load_memory_config() -> MemoryConfig:
    """Load memory configuration with defaults."""
    try:
        return MemoryConfig()
    except Exception as e:
        logger.warning(f"Failed to load memory config: {e}, using defaults")
        return MemoryConfig()
