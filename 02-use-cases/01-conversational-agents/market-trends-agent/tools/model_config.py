"""
Shared Bedrock model configuration for the Market Trends Agent.

Every LangChain model in this agent is built here so that the model in use is
defined in one place. The model id can be overridden at deploy time with the
MODEL_ID environment variable, which means the runtime can be pointed at a
different model without a code change.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Claude Haiku 4.5 through a global cross-region inference profile.
DEFAULT_MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

# Inference-profile prefixes that appear in front of the real provider name in a
# model id, for example "global.anthropic.claude-haiku-4-5-20251001-v1:0".
_INFERENCE_PROFILE_PREFIXES = {"eu", "us", "us-gov", "apac", "sa", "global"}


def get_model_id() -> str:
    """Return the model id the agent should use."""
    return os.getenv("MODEL_ID", DEFAULT_MODEL_ID)


def resolve_provider(model_id: str) -> str:
    """Derive the Bedrock provider name from a model id or inference-profile id.

    ChatBedrock can infer the provider itself, but it only strips the regional
    inference-profile prefixes and does not recognise "global.". It therefore
    reads a global cross-region profile as provider "global" and fails with
    "NotImplementedError: Provider global model does not support chat." Resolving
    the provider here keeps every inference profile usable.
    """
    parts = model_id.split(".")
    if len(parts) > 1 and parts[0].lower() in _INFERENCE_PROFILE_PREFIXES:
        return parts[1]
    return parts[0]


def build_chat_model(**kwargs):
    """Build a ChatBedrock client for the configured model.

    Any keyword argument accepted by ChatBedrock can be passed through, so
    callers can still set model_kwargs, and model_id can be overridden per call.
    """
    from langchain_aws import ChatBedrock

    model_id = kwargs.pop("model_id", None) or get_model_id()
    provider = kwargs.pop("provider", None) or resolve_provider(model_id)
    region_name = kwargs.pop("region_name", None) or os.getenv("AWS_REGION", "us-east-1")

    logger.info(f"Building chat model {model_id} (provider={provider}, region={region_name})")

    return ChatBedrock(
        model_id=model_id,
        provider=provider,
        region_name=region_name,
        **kwargs,
    )
