"""Model loading.

The caller passes the active degradation rung's model id (resolved from AppConfig,
ADR-0007); with none, it falls back to the L0 default from config. The model id is
always a parameter here, never a hardcoded constant elsewhere.
"""

from typing import Any

from config import DEFAULT_MODEL_ID
from strands.models.bedrock import BedrockModel


def load_model(model_id: str | None = None, model_config: dict[str, Any] | None = None) -> BedrockModel:
    """Return a Bedrock model client using IAM credentials.

    Args:
        model_id: the global inference profile id. Defaults to the L0 rung model;
            the pipeline passes the active rung's model id here.
        model_config: extra BedrockModel config, e.g. {"cache_prompt": "default"}
            to cache a static system prompt (prompt-prefix caching).
    """
    return BedrockModel(model_id=model_id or DEFAULT_MODEL_ID, **(model_config or {}))
