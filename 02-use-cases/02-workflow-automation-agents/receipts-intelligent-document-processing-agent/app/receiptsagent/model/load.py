"""Model loading.

Phase 1 (walking skeleton): loads the default L0 model from config.
Phase 6 will resolve the model id from the active degradation rung in AppConfig
(see the ladder design in spec §6); the seam for that is `model_id` being a
parameter here, never a hardcoded constant elsewhere.
"""

from typing import Any

from config import DEFAULT_MODEL_ID
from strands.models.bedrock import BedrockModel


def load_model(model_id: str | None = None, model_config: dict[str, Any] | None = None) -> BedrockModel:
    """Return a Bedrock model client using IAM credentials.

    Args:
        model_id: the global inference profile id. Defaults to the L0 rung model;
            Phase 6 passes the active rung's model id here.
        model_config: extra BedrockModel config, e.g. {"cache_prompt": "default"}
            to cache a static system prompt (spec §7 prompt-prefix caching).
    """
    return BedrockModel(model_id=model_id or DEFAULT_MODEL_ID, **(model_config or {}))
