"""Model loader for the IT Incident Response Agent.

Reads the model ID from centralized config (which resolves AGENT_MODEL_ID
environment variable injected by the CDK stack). Falls back to Claude Sonnet
for local development.

Supports cost-efficient routing: use a cheaper/faster model for LOW priority
tickets and the full model for MEDIUM/HIGH/CRITICAL.
"""

from config import AGENT_MODEL_ID, FAST_MODEL_ID
from strands.models.bedrock import BedrockModel


def load_model(priority: str = "MEDIUM") -> BedrockModel:
    """Get Bedrock model client, selecting based on ticket priority.

    Cost routing pattern: LOW priority tickets use a cheaper/faster model
    (Haiku) for classification and simple resolutions. MEDIUM+ tickets use
    the full model (Sonnet) for complex reasoning.

    This demonstrates the 'cost shape' principle: filter cheap at the event
    plane and only use expensive reasoning when warranted.
    """
    model_id = AGENT_MODEL_ID

    # STEP: COST ROUTING — Use cheaper model for low-priority tickets
    if priority == "LOW":
        model_id = FAST_MODEL_ID

    return BedrockModel(model_id=model_id)
